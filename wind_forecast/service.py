"""Application services shared by the command line and Streamlit UI."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from wind_forecast.agent import ForecastAgent
from wind_forecast.application.code_identity import code_fingerprint
from wind_forecast.bootstrap import build_runtime
from wind_forecast.config import Settings
from wind_forecast.contracts import (
    Prediction,
    RunRequest,
    RunResult,
    WeatherSnapshot,
    parse_request,
)
from wind_forecast.data import validate_hourly_history
from wind_forecast.evaluation import rolling_backtest
from wind_forecast.features import FEATURE_COLUMNS, FeaturePipeline, training_rows
from wind_forecast.models import (
    DEFAULT_MODEL_PARAMETERS,
    BaselineModel,
    ForecastModel,
    fit_residual_calibration,
    _metadata_path,
)
from wind_forecast.weather import validate_weather, weather_fingerprint

UTC = timezone.utc
ALMATY = timezone(timedelta(hours=5))
DEFAULT_TRAIN_START = date(2025, 10, 1)
DEFAULT_TRAIN_END = date(2025, 12, 14)
DEFAULT_CALIBRATION_START = date(2025, 12, 15)
DEFAULT_CALIBRATION_END = date(2025, 12, 29)
DEFAULT_BACKTEST_START = date(2026, 1, 1)
DEFAULT_BACKTEST_END = date(2026, 1, 30)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _timestamp_values(frame: pd.DataFrame) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for raw in frame.to_dict(orient="records"):
        row: dict[str, Any] = {}
        for key, value in raw.items():
            if isinstance(value, (pd.Timestamp, datetime)):
                row[key] = value.isoformat().replace("+00:00", "Z")
            elif pd.isna(value):
                row[key] = None
            elif isinstance(value, np.generic):
                row[key] = value.item()
            else:
                row[key] = value
        records.append(row)
    return records


@dataclass(frozen=True)
class _PreparedSource:
    paths: dict[str, Path]
    fingerprints: dict[str, str]
    identity: str
    is_hourly: bool = False


class _ApplicationPredictor:
    """Expose feature preparation, model loading, and prediction as agent stages."""

    def __init__(self, application: "Application", request: RunRequest) -> None:
        self.application = application
        self.request = request
        self.history: pd.DataFrame | None = None
        self.snapshot_provenance: dict[str, Any] = {}

    def reuse_identity(self) -> dict[str, Any]:
        """Declare identities without loading model objects before the weather gate."""
        app = self.application
        return {
            "config": {
                "mode": self.request.mode,
                "source_fingerprint": app._source_identity(),
                "weather_source": app._snapshot_identity,
                "model_parameters": app._model_parameters(),
                "feature_schema": list(FEATURE_COLUMNS),
                "dependencies": app._dependency_identity(),
                "application_code": app._predictor_code_identity(),
            },
            "model": app._active_model_identity(
                self.request.mode, self.request.origin
            ),
        }

    def prepare(
        self, history: pd.DataFrame, snapshot: WeatherSnapshot, request: RunRequest
    ) -> pd.DataFrame:
        self.history = history.copy()
        self.snapshot_provenance = dict(snapshot.provenance)
        return self.application.feature_pipeline.build(history, snapshot, request)

    def train_or_load(
        self,
        history: pd.DataFrame,
        features: pd.DataFrame,
        snapshot: WeatherSnapshot,
        request: RunRequest,
    ) -> ForecastModel:
        del features, snapshot
        model = self.application._ensure_model(request.mode, request.origin)
        if model is None:
            raise RuntimeError("forecast_model_unavailable")
        self.history = history.copy()
        return model

    def predict(
        self, model_bundle: ForecastModel, features: pd.DataFrame, request: RunRequest
    ) -> Prediction:
        history = self.history if self.history is not None else pd.DataFrame()
        prediction = model_bundle.predict(features)
        prediction.diagnostics.update(
            self.application._model_diagnostics(model_bundle, history, request)
        )
        prediction.diagnostics["metrics"] = self.application._past_backtest_metrics(
            request.origin, request.mode, self.snapshot_provenance
        )
        return prediction


class Application:
    """Build, execute, and read forecast workflows for both product interfaces."""

    def __init__(
        self,
        settings: Settings,
        *,
        offline: bool = False,
        weather_fixture: Path | None = None,
        weather_provider: Any | None = None,
        analyzer: Any | None = None,
        model_parameters: Mapping[str, Any] | None = None,
    ) -> None:
        self.settings = settings
        self.offline = bool(offline)
        self.weather_fixture = Path(weather_fixture).expanduser().resolve() if weather_fixture else None
        runtime = build_runtime(
            settings,
            offline=self.offline,
            weather_fixture=self.weather_fixture,
            weather_provider=weather_provider,
            analyzer=analyzer,
        )
        self.weather_provider = runtime.weather_provider
        self.analyzer = runtime.analyzer
        self.model_parameters = {**DEFAULT_MODEL_PARAMETERS, **dict(model_parameters or {})}
        self.data_pipeline = runtime.data_pipeline
        self.feature_pipeline = runtime.feature_pipeline
        self.store = runtime.store
        self._source: _PreparedSource | None = None
        self._history: pd.DataFrame | None = None
        self._snapshot_identity = self._weather_identity()
        self.last_training_error: str | None = None
        self.last_simulation_index: Path | None = None

    def prepare(self) -> Path:
        """Write or reuse an hourly history cache keyed by source file contents."""
        source = self._source_files(refresh=True)
        destination = self.settings.cache_dir / "prepared"
        manifest_path = destination / "source_fingerprints.json"
        if manifest_path.is_file():
            try:
                cached = json.loads(manifest_path.read_text(encoding="utf-8"))
                if cached.get("source_fingerprints") == source.fingerprints and (
                    destination / "hourly.csv"
                ).is_file() and cached.get("prepared_sha256") == _sha256(
                    destination / "hourly.csv"
                ):
                    return destination
            except (OSError, json.JSONDecodeError):
                pass

        destination.mkdir(parents=True, exist_ok=True)
        if source.is_hourly:
            frame = pd.read_csv(source.paths["prepared"])
            for column in ("hour_start", "available_at"):
                frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce")
            frame["observation_count"] = pd.to_numeric(
                frame["observation_count"], errors="coerce"
            ).astype("int64")
            for column in ("power", "wind_speed", "temperature"):
                frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("float64")
            validate_hourly_history(frame)
            frame.to_csv(destination / "hourly.csv", index=False)
            quality_path = source.paths["prepared"].parent / "quality.json"
            if quality_path.is_file():
                (destination / "quality.json").write_bytes(quality_path.read_bytes())
        else:
            self.data_pipeline.prepare(source.paths, destination)
        cached_manifest = {
            "schema_version": 1,
            "source_fingerprints": source.fingerprints,
            "prepared_sha256": _sha256(destination / "hourly.csv"),
        }
        self._write_json(manifest_path, cached_manifest)
        self._source = source
        self._history = None
        return destination

    def _source_files(self, *, refresh: bool = False) -> _PreparedSource:
        if self._source is not None and not refresh:
            return self._source
        if self._source is not None:
            current = {key: _sha256(path) for key, path in self._source.paths.items()}
            if current == self._source.fingerprints:
                return self._source
            self._source = None
            self._history = None

        data_dir = Path(self.settings.data_dir).expanduser().resolve()
        hourly = data_dir / "hourly.csv" if data_dir.is_dir() else data_dir
        if hourly.is_file() and hourly.name == "hourly.csv":
            paths = {"prepared": hourly}
            fingerprints = {key: _sha256(path) for key, path in paths.items()}
            source = _PreparedSource(
                paths, fingerprints, _json_hash(fingerprints), is_hourly=True
            )
            self._source = source
            return source

        if not data_dir.is_dir():
            raise ValueError(f"data directory does not exist: {data_dir}")
        files = [path for path in data_dir.iterdir() if path.is_file() and path.suffix.lower() == ".csv"]
        paths: dict[str, Path] = {}
        for turbine_id, tokens in (
            ("turbine_1", ("turbine 1.csv", "turbine_1.csv")),
            ("turbine_2", ("turbine 2.csv", "turbine_2.csv")),
        ):
            matches = [
                path for path in files
                if any(token in path.name.lower() for token in tokens)
            ]
            if len(matches) != 1:
                raise ValueError(
                    f"expected exactly one CSV for {turbine_id} in {data_dir}; found {len(matches)}"
                )
            paths[turbine_id] = matches[0]
        fingerprints = {key: _sha256(path) for key, path in sorted(paths.items())}
        source = _PreparedSource(paths, fingerprints, _json_hash(fingerprints))
        self._source = source
        return source

    def _load_history(self) -> pd.DataFrame:
        if self._history is not None:
            return self._history.copy()
        prepared = self.prepare()
        if self._history is None:
            frame = pd.read_csv(prepared / "hourly.csv")
            for column in ("hour_start", "available_at"):
                frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce")
            frame["observation_count"] = pd.to_numeric(
                frame["observation_count"], errors="coerce"
            ).astype("int64")
            for column in ("power", "wind_speed", "temperature"):
                frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("float64")
            validate_hourly_history(frame)
            self._history = frame
        return self._history.copy()

    def _source_identity(self) -> str:
        return self._source_files().identity

    def _weather_identity(self) -> str:
        if self.offline:
            return "synthetic"
        if self.weather_fixture is not None:
            try:
                return f"fixture-{_sha256(self.weather_fixture)}"
            except OSError:
                return "fixture-missing"
        return "openmeteo-ifs"

    def _snapshot_cache_path(self, request: RunRequest) -> Path:
        origin = pd.Timestamp(request.origin).tz_convert("UTC").strftime("%Y%m%dT%H%MZ")
        key = _json_hash(
            {
                "source": self._source_identity(),
                "mode": request.mode,
                "weather_source": self._snapshot_identity,
                "origin": origin,
                "horizon": request.horizon,
            }
        )
        return self.settings.cache_dir / "application-snapshots" / f"{key}.json"

    def _snapshot(self, request: RunRequest, refresh: bool = False) -> WeatherSnapshot:
        cache_path = self._snapshot_cache_path(request)
        if not refresh and cache_path.is_file():
            try:
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
                rows = pd.DataFrame.from_records(payload["rows"])
                for column in ("valid_time", "initialized_at", "issued_at", "available_at"):
                    if column in rows:
                        rows[column] = pd.to_datetime(rows[column], utc=True, errors="coerce")
                snapshot = WeatherSnapshot(
                    rows=rows,
                    raw_responses=payload.get("raw_responses", []),
                    fingerprint=str(payload["fingerprint"]),
                    provenance=payload["provenance"],
                )
                validate_weather(snapshot, request)
                return snapshot
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                pass
        snapshot = self.weather_provider.fetch(request, refresh=refresh)
        validate_weather(snapshot, request)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "fingerprint": snapshot.fingerprint,
            "provenance": snapshot.provenance,
            "raw_responses": snapshot.raw_responses,
            "rows": _timestamp_values(snapshot.rows),
        }
        cache_path.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        return snapshot

    @staticmethod
    def _daily_requests(
        start: date, end: date, mode: str, horizon: int = 48
    ) -> list[RunRequest]:
        if end < start:
            raise ValueError("date range end must be on or after start")
        requests: list[RunRequest] = []
        day = start
        while day <= end:
            origin = datetime.combine(day, time.min, tzinfo=ALMATY).astimezone(UTC)
            requests.append(RunRequest(origin=origin, horizon=horizon, mode=mode))
            day += timedelta(days=1)
        return requests

    def _daily_snapshots(
        self,
        history: pd.DataFrame,
        start: date,
        end: date,
        mode: str,
        *,
        refresh: bool = False,
    ) -> list[tuple[RunRequest, WeatherSnapshot]]:
        if self.weather_fixture is not None:
            raise ValueError("a single weather fixture supports run, not training or backtest ranges")
        rows: list[tuple[RunRequest, WeatherSnapshot]] = []
        for request in self._daily_requests(start, end, mode):
            available = history.loc[
                (history["turbine_id"].astype(str).isin(("turbine_1", "turbine_2")))
                & history["available_at"].le(pd.Timestamp(request.origin))
                & history["quality_flag"].astype(str).isin(("good", "partial"))
            ]
            if available["turbine_id"].nunique() < 2:
                continue
            try:
                snapshot = self._snapshot(request, refresh=refresh)
            except Exception as exc:
                # Stop at the first unavailable daily archive request. The provider
                # already has a bounded per-origin retry budget; later dates cannot
                # improve the missing chronological prefix.
                raise RuntimeError(
                    f"weather snapshot unavailable for {request.origin.isoformat()} ({type(exc).__name__})"
                ) from exc
            rows.append((request, snapshot))
        return rows

    def _model_parameters(self, iterations: int | None = None) -> dict[str, Any]:
        parameters = dict(self.model_parameters)
        if iterations is not None:
            parameters["iterations"] = int(iterations)
        return parameters

    @staticmethod
    def _dependency_identity() -> dict[str, str]:
        versions: dict[str, str] = {}
        for package in ("numpy", "pandas", "catboost"):
            try:
                versions[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                continue
        return versions

    @staticmethod
    def _predictor_code_identity() -> dict[str, str]:
        functions = {
            "train": Application.train,
            "ensure_model": Application._ensure_model,
            "load_active_model": Application._load_active_model,
            "model_diagnostics": Application._model_diagnostics,
            "past_backtest_metrics": Application._past_backtest_metrics,
            "build_features": FeaturePipeline.build,
            "predict": ForecastModel.predict,
        }
        return {
            name: code_fingerprint(function.__code__)
            for name, function in functions.items()
            if getattr(function, "__code__", None) is not None
        }

    def _active_model_identity(
        self, mode: str, origin: datetime | pd.Timestamp
    ) -> dict[str, Any] | None:
        """Check the active model's declared identity and file hashes without loading it."""
        path = self._active_model_path(mode)
        try:
            pointer = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(pointer, dict):
                return None
            if (
                pointer.get("source_fingerprint") != self._source_identity()
                or pointer.get("mode") != mode
                or pointer.get("weather_source") != self._snapshot_identity
                or pointer.get("parameters") != self._model_parameters()
                or not pointer.get("training_fingerprint")
            ):
                return None
            prefix = (self.settings.model_dir / pointer["model_prefix"]).resolve()
            model_root = self.settings.model_dir.resolve()
            if model_root not in prefix.parents:
                return None
            metadata = json.loads(_metadata_path(prefix).read_text(encoding="utf-8"))
            if not isinstance(metadata, dict):
                return None
            if (
                metadata.get("training_fingerprint") != pointer["training_fingerprint"]
                or metadata.get("parameters") != self._model_parameters()
                or metadata.get("catboost_version")
                != self._dependency_identity().get("catboost")
            ):
                return None
            model_files = metadata.get("model_files")
            hashes = metadata.get("sha256")
            if not isinstance(model_files, dict) or set(model_files) != {"p10", "p50", "p90"}:
                return None
            if not isinstance(hashes, dict):
                return None
            verified_hashes: dict[str, str] = {}
            for quantile, filename in model_files.items():
                if filename != f"{prefix.name}.{quantile}.cbm":
                    return None
                model_path = prefix.parent / filename
                expected_hash = hashes.get(filename)
                if (
                    not isinstance(expected_hash, str)
                    or not model_path.is_file()
                    or _sha256(model_path) != expected_hash
                ):
                    return None
                verified_hashes[quantile] = expected_hash

            cutoff_values: dict[str, str] = {}
            request_origin = pd.Timestamp(origin)
            if request_origin.tzinfo is None:
                return None
            request_origin = request_origin.tz_convert("UTC")
            for field in ("training_cutoff", "calibration_cutoff"):
                value = metadata.get(field)
                if not value:
                    return None
                cutoff = pd.Timestamp(value)
                if cutoff.tzinfo is None or cutoff.tz_convert("UTC") > request_origin:
                    return None
                cutoff_values[field] = cutoff.tz_convert("UTC").isoformat()
            return {
                "training_fingerprint": pointer["training_fingerprint"],
                **cutoff_values,
                "model_file_hashes": verified_hashes,
            }
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def train(
        self,
        *,
        mode: str | None = None,
        train_start: date = DEFAULT_TRAIN_START,
        train_end: date = DEFAULT_TRAIN_END,
        calibration_start: date = DEFAULT_CALIBRATION_START,
        calibration_end: date = DEFAULT_CALIBRATION_END,
        iterations: int | None = None,
        refresh: bool = False,
    ) -> Path:
        """Build leakage-safe train/calibration rows and save a fingerprinted model."""
        selected_mode = mode or self.settings.mode
        if selected_mode not in ("demo", "competition"):
            raise ValueError("mode must be demo or competition")
        if self.offline and selected_mode != "demo":
            raise ValueError("offline synthetic weather is only allowed in demo mode")
        if calibration_start <= train_end:
            raise ValueError("calibration origins must follow the training origins")
        history = self._load_history()
        if history.empty:
            raise ValueError("insufficient_history_for_training")
        earliest = history["hour_start"].min()
        latest = history["available_at"].max()
        if (
            train_start == DEFAULT_TRAIN_START
            and train_end == DEFAULT_TRAIN_END
            and calibration_start == DEFAULT_CALIBRATION_START
            and calibration_end == DEFAULT_CALIBRATION_END
        ):
            earliest_local_day = (earliest + pd.Timedelta(hours=5)).date()
            latest_local_day = (latest + pd.Timedelta(hours=5)).date()
            if earliest_local_day > train_start:
                # Short fixture/demo histories still get a chronological, held-out
                # split. The production date ranges remain the defaults whenever
                # the supplied history covers them.
                if (latest_local_day - earliest_local_day).days < 6:
                    raise ValueError("insufficient_history_for_training_and_calibration")
                train_start = earliest_local_day + timedelta(days=1)
                calibration_end = latest_local_day - timedelta(days=3)
                calibration_start = calibration_end - timedelta(days=2)
                train_end = calibration_start - timedelta(days=1)
        first_origin = pd.Timestamp(datetime.combine(train_start, time.min, ALMATY)).tz_convert("UTC")
        if earliest >= first_origin:
            raise ValueError("insufficient_history_for_training_origins")
        calibration_origin = pd.Timestamp(datetime.combine(calibration_start, time.min, ALMATY)).tz_convert("UTC")
        if latest < calibration_origin:
            raise ValueError("insufficient_history_for_calibration_cutoff")

        training_snapshots = self._daily_snapshots(
            history, train_start, train_end, selected_mode, refresh=refresh
        )
        calibration_snapshots = self._daily_snapshots(
            history, calibration_start, calibration_end, selected_mode, refresh=refresh
        )
        if not training_snapshots or not calibration_snapshots:
            raise ValueError("insufficient_weather_history_for_training_and_calibration")
        training_cutoff = datetime.combine(calibration_start, time.min, ALMATY).astimezone(UTC)
        calibration_cutoff = datetime.combine(
            calibration_end + timedelta(days=2), time.min, ALMATY
        ).astimezone(UTC)
        rows = training_rows(history, training_snapshots, training_cutoff)
        calibration_rows_frame = training_rows(
            history, calibration_snapshots, calibration_cutoff
        )
        if rows.empty:
            raise ValueError("insufficient_leakage_safe_training_rows")
        if calibration_rows_frame.empty:
            raise ValueError("insufficient_historical_calibration_rows")

        baseline_strategy, baseline_scores, baseline_calibrations = self._select_calibration_baseline(
            history, calibration_rows_frame, calibration_snapshots
        )
        baseline_pointer = {
            "schema_version": 1,
            "mode": selected_mode,
            "weather_source": self._snapshot_identity,
            "source_fingerprint": self._source_identity(),
            "strategy": baseline_strategy,
            "calibration_scores": baseline_scores,
            "baseline_calibrations": baseline_calibrations,
            "calibration_cutoff": pd.to_datetime(
                calibration_rows_frame["target_end"], utc=True
            ).max().isoformat(),
        }
        self._write_json(self._baseline_selection_path(selected_mode), baseline_pointer)

        parameters = self._model_parameters(iterations)
        model = ForecastModel(**parameters)
        fingerprint = model.fingerprint_for(rows, calibration_rows_frame, parameters)
        model_dir = self.settings.model_dir / "trained"
        prefix = model_dir / f"forecast-{selected_mode}-{self._snapshot_identity}-{fingerprint[:16]}"
        metadata_path = _metadata_path(prefix)
        loaded: ForecastModel | None = None
        if not refresh and metadata_path.is_file():
            try:
                cached = ForecastModel.load(prefix)
                if cached.training_fingerprint == fingerprint:
                    loaded = cached
            except (OSError, ValueError, RuntimeError):
                loaded = None
        if loaded is None:
            model.fit(rows, calibration_rows_frame)
            model.save(prefix)
        else:
            model = loaded
        pointer = {
            "schema_version": 1,
            "mode": selected_mode,
            "weather_source": self._snapshot_identity,
            "source_fingerprint": self._source_identity(),
            "training_fingerprint": fingerprint,
            "model_prefix": str(prefix.relative_to(self.settings.model_dir.resolve())),
            "training_cutoff": model.training_cutoff.isoformat() if model.training_cutoff is not None else None,
            "calibration_cutoff": model.calibration_cutoff.isoformat() if model.calibration_cutoff is not None else None,
            "training_rows": int(model.training_row_count),
            "parameters": parameters,
            "baseline_strategy": baseline_strategy,
            "baseline_calibration_scores": baseline_scores,
            "baseline_calibration_cutoff": (
                pd.to_datetime(calibration_rows_frame["target_end"], utc=True).max().isoformat()
                if not calibration_rows_frame.empty
                else None
            ),
        }
        self.settings.model_dir.mkdir(parents=True, exist_ok=True)
        self._write_json(self._active_model_path(selected_mode), pointer)
        return prefix

    def _active_model_path(self, mode: str) -> Path:
        return self.settings.model_dir / f"active-{mode}-{self._snapshot_identity}.json"

    def _baseline_selection_path(self, mode: str) -> Path:
        return self.settings.model_dir / f"baseline-{mode}-{self._snapshot_identity}.json"

    def _selected_baseline(
        self, mode: str, origin: datetime | pd.Timestamp | None = None
    ) -> str:
        return str(self._baseline_selection(mode, origin)["strategy"])

    def _baseline_selection(
        self, mode: str, origin: datetime | pd.Timestamp | None
    ) -> dict[str, Any]:
        default = {
            "strategy": "persistence",
            "calibration_cutoff": None,
            "calibration_scores": {},
            "residual_calibration": fit_residual_calibration(
                pd.DataFrame(columns=["turbine_id", "target", "prediction"])
            ),
            "status": "unavailable_uncalibrated_persistence",
        }
        if origin is None:
            return default
        path = self._baseline_selection_path(mode)
        if not path.is_file():
            return default
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            calibration_cutoff = value.get("calibration_cutoff")
            scores = value.get("calibration_scores")
            if (
                value.get("source_fingerprint") != self._source_identity()
                or value.get("weather_source") != self._snapshot_identity
                or value.get("mode") != mode
                or value.get("strategy") not in ("persistence", "seasonal", "power_curve")
                or not isinstance(scores, dict)
                or not calibration_cutoff
            ):
                return default
            cutoff = pd.Timestamp(calibration_cutoff)
            request_origin = pd.Timestamp(origin)
            if (
                cutoff.tzinfo is None
                or request_origin.tzinfo is None
                or cutoff.tz_convert("UTC") > request_origin.tz_convert("UTC")
            ):
                return default
            from wind_forecast.models import select_baseline

            strategy = str(value["strategy"])
            if select_baseline(scores) != strategy:
                return default
            selected_score = scores.get(strategy)
            if not isinstance(selected_score, dict) or int(selected_score.get("n", 0)) < 1:
                return default
            mae = selected_score.get("mae")
            if mae is None or not np.isfinite(float(mae)) or float(mae) < 0:
                return default
            calibrations = value.get("baseline_calibrations")
            residual_calibration = (
                calibrations.get(strategy)
                if isinstance(calibrations, dict)
                else None
            )
            if (
                not isinstance(residual_calibration, dict)
                or residual_calibration.get("status") != "calibrated"
            ):
                residual_calibration = fit_residual_calibration(
                    pd.DataFrame(columns=["turbine_id", "target", "prediction"])
                )
            return {
                "strategy": strategy,
                "calibration_cutoff": cutoff.tz_convert("UTC").isoformat(),
                "calibration_scores": scores,
                "residual_calibration": residual_calibration,
                "status": "selected_from_held_out_labels",
            }
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass
        return default

    def _select_calibration_baseline(
        self,
        history: pd.DataFrame,
        calibration: pd.DataFrame,
        snapshots: list[tuple[RunRequest, WeatherSnapshot]],
    ) -> tuple[
        str,
        dict[str, dict[str, float | int | None]],
        dict[str, dict[str, Any]],
    ]:
        from wind_forecast.models import select_baseline

        errors: dict[str, list[float]] = {
            "persistence": [], "seasonal": [], "power_curve": []
        }
        residual_rows: dict[str, list[pd.DataFrame]] = {
            strategy: [] for strategy in errors
        }
        for request, snapshot in snapshots:
            origin_rows = calibration.loc[
                pd.to_datetime(calibration["forecast_origin"], utc=True)
                == pd.Timestamp(request.origin)
            ]
            if origin_rows.empty:
                continue
            features = self.feature_pipeline.build(history, snapshot, request)
            baseline = BaselineModel.fit(history, request.origin)
            labels = origin_rows[["turbine_id", "valid_time", "target"]]
            for strategy in errors:
                predicted = baseline.predict(features, strategy).rows[
                    ["turbine_id", "valid_time", "p50"]
                ]
                joined = predicted.merge(
                    labels, on=["turbine_id", "valid_time"], how="inner", validate="one_to_one"
                )
                valid = joined["target"].notna()
                if valid.any():
                    residual_rows[strategy].append(
                        joined.loc[valid, ["turbine_id", "target", "p50"]].rename(
                            columns={"p50": "prediction"}
                        )
                    )
                    errors[strategy].extend(
                        np.abs(
                            joined.loc[valid, "p50"].to_numpy(dtype=float)
                            - joined.loc[valid, "target"].to_numpy(dtype=float)
                        ).tolist()
                    )
        scores: dict[str, dict[str, float | int | None]] = {}
        for strategy, values in errors.items():
            scores[strategy] = {
                "mae": float(np.mean(values)) if values else None,
                "n": len(values),
            }
        calibrations: dict[str, dict[str, Any]] = {}
        empty = pd.DataFrame(columns=["turbine_id", "target", "prediction"])
        for strategy, frames in residual_rows.items():
            rows = pd.concat(frames, ignore_index=True) if frames else empty
            calibrations[strategy] = fit_residual_calibration(rows)
        return select_baseline(scores), scores, calibrations

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def _load_active_model(
        self, mode: str, origin: datetime | pd.Timestamp | None = None
    ) -> ForecastModel | None:
        path = self._active_model_path(mode)
        if not path.is_file():
            return None
        try:
            pointer = json.loads(path.read_text(encoding="utf-8"))
            if (
                pointer.get("source_fingerprint") != self._source_identity()
                or pointer.get("mode") != mode
                or pointer.get("weather_source") != self._snapshot_identity
                or pointer.get("parameters") != self._model_parameters()
            ):
                return None
            prefix = (self.settings.model_dir / pointer["model_prefix"]).resolve()
            if self.settings.model_dir.resolve() not in prefix.parents:
                return None
            model = ForecastModel.load(prefix)
            if model.training_fingerprint != pointer.get("training_fingerprint"):
                return None
            if origin is not None:
                if model.training_cutoff is not None and model.training_cutoff > pd.Timestamp(origin):
                    return None
                if model.calibration_cutoff is not None and model.calibration_cutoff > pd.Timestamp(origin):
                    return None
            return model
        except (OSError, KeyError, TypeError, ValueError, RuntimeError, json.JSONDecodeError):
            return None

    def _ensure_model(
        self, mode: str, origin: datetime | pd.Timestamp | None = None
    ) -> ForecastModel | None:
        model = self._load_active_model(mode, origin)
        if model is not None:
            return model
        self.last_training_error = None
        try:
            self.train(mode=mode)
            return self._load_active_model(mode, origin)
        except Exception as exc:
            self.last_training_error = type(exc).__name__
            return None

    @staticmethod
    def _weather_provenance_class(provenance: Mapping[str, Any]) -> str:
        status = provenance.get("provenance_status")
        if status == "synthetic":
            return "synthetic"
        if status == "verified" and provenance.get("competition_valid") is True:
            return "verified"
        return "unverified"

    def _backtest_identity(
        self,
        mode: str,
        snapshots: list[tuple[RunRequest, WeatherSnapshot]],
    ) -> dict[str, str]:
        snapshot_rows = [
            {
                "origin": pd.Timestamp(request.origin).tz_convert("UTC").isoformat(),
                "fingerprint": snapshot.fingerprint,
                "provenance_class": self._weather_provenance_class(snapshot.provenance),
            }
            for request, snapshot in snapshots
        ]
        classes = {row["provenance_class"] for row in snapshot_rows}
        provenance_class = next(iter(classes)) if len(classes) == 1 else (
            "mixed" if classes else "none"
        )
        return {
            "data_source_fingerprint": self._source_identity(),
            "mode": mode,
            "weather_source": self._snapshot_identity,
            "weather_provenance_class": provenance_class,
            "weather_snapshot_set_fingerprint": _json_hash(snapshot_rows),
        }

    @staticmethod
    def _unavailable_backtest_metrics() -> dict[str, Any]:
        return {
            "schema_version": 1,
            "metric_status": "unavailable_no_labels",
            "evaluation_period": None,
            "models": [],
            "coverage": {},
        }

    def _past_backtest_metrics(
        self,
        origin: datetime | pd.Timestamp | None = None,
        mode: str | None = None,
        provenance: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        metrics_path = self.settings.model_dir / "backtest" / "metrics.json"
        if not metrics_path.is_file():
            return self._unavailable_backtest_metrics()
        try:
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            period = metrics.get("evaluation_period")
            coverage = metrics.get("coverage")
            identity = coverage.get("identity") if isinstance(coverage, dict) else None
            selected_mode = mode or self.settings.mode
            current_provenance_class = self._weather_provenance_class(
                provenance or ({"provenance_status": "synthetic"} if self.offline else {})
            )
            if (
                not isinstance(period, dict)
                or not isinstance(identity, dict)
                or coverage.get("evidence_scope") != "historical_backtest"
                or identity.get("data_source_fingerprint") != self._source_identity()
                or identity.get("mode") != selected_mode
                or identity.get("weather_source") != self._snapshot_identity
                or identity.get("weather_provenance_class") != current_provenance_class
                or coverage.get("evidence_class")
                != ("competition" if selected_mode == "competition" else "demo_only")
                or (selected_mode == "demo" and coverage.get("competition_valid") is not False)
                or (selected_mode == "competition" and coverage.get("competition_valid") is not True)
            ):
                raise ValueError("missing evaluation period")
            if origin is not None:
                evaluation_end = pd.Timestamp(period.get("end"))
                request_origin = pd.Timestamp(origin)
                if (
                    evaluation_end.tzinfo is None
                    or request_origin.tzinfo is None
                    or evaluation_end.tz_convert("UTC") > request_origin.tz_convert("UTC")
                ):
                    raise ValueError("backtest metrics follow forecast origin")
            return metrics
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return self._unavailable_backtest_metrics()

    def run(self, request: RunRequest, refresh: bool = False) -> RunResult:
        """Run one request with shared data, model, agent, and artifact services."""
        parsed = parse_request(
            pd.Timestamp(request.origin).isoformat(), request.horizon, request.mode
        )
        if self.offline and parsed.mode != "demo":
            raise ValueError("offline synthetic weather is only allowed in demo mode")
        if self.weather_fixture is not None and parsed.mode != "demo":
            raise ValueError("weather fixture is only allowed in demo mode")
        all_history = self._load_history()
        history = all_history.loc[
            all_history["available_at"].le(pd.Timestamp(parsed.origin))
        ].copy()
        baseline: BaselineModel | None = None

        def _predict_baseline(rows: pd.DataFrame, snapshot: WeatherSnapshot, req: RunRequest):
            nonlocal baseline
            features = self.feature_pipeline.build(rows, snapshot, req)
            if baseline is None:
                baseline = BaselineModel.fit(rows, req.origin)
            selection = self._baseline_selection(req.mode, req.origin)
            strategy = str(selection["strategy"])
            residual_calibration = selection["residual_calibration"]
            baseline.residual_calibrations[strategy] = residual_calibration
            prediction = baseline.predict(features, strategy)
            data_quality = self._data_quality(rows, req.origin)
            calibration_status = str(residual_calibration.get("status", "unavailable"))
            prediction.diagnostics.update(
                {
                    "model_parameters": {"strategy": strategy},
                    "seed": None,
                    "training_cutoff": req.origin.isoformat(),
                    "training_rows": len(rows),
                    "calibration_cutoff": selection["calibration_cutoff"],
                    "model_training_rows_fingerprint": self._source_identity(),
                    "feature_schema": list(FEATURE_COLUMNS),
                    "config_fingerprint": _json_hash(
                        {"mode": req.mode, "model": strategy, "weather_source": self._snapshot_identity}
                    ),
                    "dependency_versions": {},
                    "data_quality": data_quality,
                    "degraded": bool(data_quality["stale_observations"]),
                    "calibration": {
                        "status": calibration_status,
                        "method": (
                            "held_out_baseline_residuals"
                            if calibration_status == "calibrated"
                            else "unavailable_uncalibrated_bounds"
                        ),
                        "strategy": strategy,
                        "selection_status": selection["status"],
                        "cutoff": selection["calibration_cutoff"],
                        "sample_count": int(residual_calibration.get("n", 0)),
                        "baseline_selection_scores": selection["calibration_scores"],
                    },
                    "metrics": self._past_backtest_metrics(
                        req.origin, req.mode, snapshot.provenance
                    ),
                }
            )
            return prediction

        predictor = _ApplicationPredictor(self, parsed)
        agent = ForecastAgent(
            self.weather_provider,
            predictor,
            self.analyzer,
            self.store,
            baseline_predictor=_predict_baseline,
        )
        # ForecastAgent owns the fetch/provenance gate and stores the exact snapshot.
        # This call deliberately passes the original shared history, never predictions
        # from earlier simulation origins.
        return agent.run(parsed, history, refresh=refresh)

    def _model_diagnostics(
        self, model: ForecastModel, history: pd.DataFrame, request: RunRequest
    ) -> dict[str, Any]:
        data_quality = self._data_quality(history, request.origin)
        return {
            "model_parameters": dict(model.parameters),
            "seed": model.parameters.get("random_seed"),
            "training_cutoff": model.training_cutoff.isoformat() if model.training_cutoff is not None else None,
            "training_rows": model.training_row_count,
            "calibration_cutoff": model.calibration_cutoff.isoformat() if model.calibration_cutoff is not None else None,
            "model_training_rows_fingerprint": model.training_fingerprint,
            "feature_schema": list(FEATURE_COLUMNS),
            "config_fingerprint": _json_hash(
                {"mode": request.mode, "parameters": model.parameters, "weather_source": self._snapshot_identity}
            ),
            "dependency_versions": {},
            "data_quality": data_quality,
            "degraded": bool(data_quality["stale_observations"]),
        }

    @staticmethod
    def _data_quality(history: pd.DataFrame, cutoff: datetime | pd.Timestamp | None) -> dict[str, Any]:
        available = (
            pd.to_datetime(history["available_at"], utc=True, errors="coerce")
            if not history.empty and "available_at" in history
            else pd.Series(dtype="datetime64[ns, UTC]")
        )
        cutoff_stamp = pd.Timestamp(cutoff) if cutoff is not None else None
        if cutoff_stamp is not None:
            if cutoff_stamp.tzinfo is None:
                cutoff_stamp = cutoff_stamp.tz_localize("UTC")
            else:
                cutoff_stamp = cutoff_stamp.tz_convert("UTC")
            available = available.loc[available.le(cutoff_stamp)]
        latest = available.max() if not available.empty else None
        age = (
            (cutoff_stamp - latest).total_seconds() / 3600
            if cutoff_stamp is not None and latest is not None and pd.notna(latest)
            else None
        )
        return {
            "history_rows": int(len(history)),
            "latest_available_at": latest.isoformat().replace("+00:00", "Z") if latest is not None and pd.notna(latest) else None,
            "observation_age_hours": age,
            "stale_observations": age is not None and age > 24,
        }

    def backtest(
        self,
        *,
        mode: str | None = None,
        start: date = DEFAULT_BACKTEST_START,
        end: date = DEFAULT_BACKTEST_END,
        history_start: date = DEFAULT_TRAIN_START,
        iterations: int | None = None,
        calibration_days: int = 15,
        refresh: bool = False,
    ) -> Path:
        """Run daily 48-hour rolling evaluation and persist metrics/predictions."""
        selected_mode = mode or self.settings.mode
        if selected_mode not in ("demo", "competition"):
            raise ValueError("mode must be demo or competition")
        if self.offline and selected_mode != "demo":
            raise ValueError("offline synthetic weather is only allowed in demo mode")
        if self.weather_fixture is not None:
            raise ValueError("a single weather fixture supports run, not training or backtest ranges")
        history = self._load_history()
        snapshots = self._daily_snapshots(
            history, history_start, end, selected_mode, refresh=refresh
        )
        origins = [request.origin for request in self._daily_requests(start, end, selected_mode)]
        result = rolling_backtest(
            history,
            snapshots,
            origins,
            self.settings.model_dir / "backtest-folds",
            model_parameters=self._model_parameters(iterations),
            calibration_days=calibration_days,
        )
        metrics_path = Path(result["metrics_path"])
        weather_verified = bool(snapshots) and all(
            snapshot.provenance.get("provenance_status") == "verified"
            and snapshot.provenance.get("competition_valid") is True
            for _, snapshot in snapshots
        )
        metric_identity = self._backtest_identity(selected_mode, snapshots)
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        coverage = metrics.setdefault("coverage", {})
        coverage.update(
            {
                "identity": metric_identity,
                "evidence_scope": "historical_backtest",
                "evidence_class": (
                    "competition"
                    if selected_mode == "competition" and weather_verified
                    else "demo_only"
                ),
                "forecast_target_metric_status": "unavailable_no_labels",
            }
        )
        if not weather_verified:
            metrics["metric_status"] = "unavailable_unverified_weather"
            metrics["models"] = []
        coverage["competition_valid"] = selected_mode == "competition" and weather_verified
        self._write_json(metrics_path, metrics)
        return metrics_path

    def simulate(
        self,
        start: date,
        end: date,
        *,
        mode: str | None = None,
        refresh: bool = False,
    ) -> list[RunResult]:
        """Run inclusive daily local-midnight forecasts and index every outcome."""
        selected_mode = mode or self.settings.mode
        if self.offline and selected_mode != "demo":
            raise ValueError("offline synthetic weather is only allowed in demo mode")
        requests = self._daily_requests(start, end, selected_mode)
        simulation_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        directory = self.settings.run_dir / "simulations" / simulation_id
        directory.mkdir(parents=True, exist_ok=False)
        entries: list[dict[str, Any]] = []
        results: list[RunResult] = []
        for request in requests:
            record: dict[str, Any] = {
                "origin": pd.Timestamp(request.origin).isoformat().replace("+00:00", "Z"),
                "status": "failed",
                "run_id": None,
                "directory": None,
                "error": None,
            }
            try:
                result = self.run(request, refresh=refresh)
                results.append(result)
                record.update(
                    {
                        "status": result.status,
                        "run_id": result.run_id,
                        "directory": result.directory.name,
                        "error": None,
                    }
                )
                if result.status == "failed":
                    record["error"] = "run_status_failed"
            except Exception as exc:
                record["error"] = f"{type(exc).__name__}:{self._safe_error_code(exc)}"
            entries.append(record)
        index = {
            "schema_version": 1,
            "simulation_id": simulation_id,
            "mode": selected_mode,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "horizon": 48,
            "origins": entries,
            "failed_origins": sum(item["status"] == "failed" for item in entries),
        }
        index_path = directory / "index.json"
        self._write_json(index_path, index)
        self.last_simulation_index = index_path
        return results

    @staticmethod
    def _safe_error_code(exc: Exception) -> str:
        del exc
        return "execution_error"

    def latest(self) -> RunResult | None:
        """Return the current successful/degraded run from the latest pointer."""
        pointer_path = self.settings.run_dir / "latest.json"
        if not pointer_path.is_file():
            return None
        try:
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
            run_id = str(pointer["run_id"])
            directory = (self.settings.run_dir / str(pointer["directory"])).resolve()
            if directory.parent != self.settings.run_dir.resolve() or not directory.is_dir():
                return None
            return RunResult(run_id, directory, pointer["status"], False)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def read_run(self, run_id: str) -> dict[str, Any]:
        """Read persisted artifacts using stable dictionary names consumed by UI."""
        if not run_id or Path(run_id).name != run_id:
            raise ValueError("invalid run id")
        directory = (self.settings.run_dir / run_id).resolve()
        if directory.parent != self.settings.run_dir.resolve() or not directory.is_dir():
            raise FileNotFoundError("run artifacts do not exist")
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        metrics = json.loads((directory / "metrics.json").read_text(encoding="utf-8"))
        events_path = directory / "events.jsonl"
        events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line]
        report = (directory / "report.md").read_text(encoding="utf-8")
        forecast_path = directory / "forecast.csv"
        forecast = pd.read_csv(forecast_path) if forecast_path.is_file() else pd.DataFrame()
        for column in ("forecast_origin", "valid_time", "weather_issued_at"):
            if column in forecast:
                forecast[column] = pd.to_datetime(forecast[column], utc=True, errors="coerce")
        return {
            "forecast": forecast,
            "manifest": manifest,
            "metrics": metrics,
            "events": events,
            "report": report,
        }
