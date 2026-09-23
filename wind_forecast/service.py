"""Application services shared by the command line and Streamlit UI."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from wind_forecast.agent import ForecastAgent
from wind_forecast.artifacts import ArtifactStore
from wind_forecast.config import Settings
from wind_forecast.contracts import Decision, RunRequest, RunResult, WeatherSnapshot, parse_request
from wind_forecast.data import DataPipeline, validate_hourly_history
from wind_forecast.evaluation import rolling_backtest
from wind_forecast.features import FEATURE_COLUMNS, FeaturePipeline, training_rows
from wind_forecast.llm import OpenAIAnalyzer
from wind_forecast.models import (
    DEFAULT_MODEL_PARAMETERS,
    BaselineModel,
    ForecastModel,
    _metadata_path,
)
from wind_forecast.weather import (
    WeatherProvider,
    make_synthetic_weather_snapshot,
    validate_weather,
    weather_fingerprint,
)

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


class _SyntheticWeatherProvider:
    """Explicit deterministic provider for offline demo runs."""

    def fetch(self, request: RunRequest, refresh: bool = False) -> WeatherSnapshot:
        del refresh
        return make_synthetic_weather_snapshot(request)


class _FixtureWeatherProvider:
    """Read one recorded weather fixture without changing its valid times."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path).expanduser().resolve()
        try:
            self.payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("weather fixture could not be read as JSON") from exc
        if self.payload.get("mode") != "demo" or not isinstance(self.payload.get("rows"), list):
            raise ValueError("weather fixture must contain demo rows")

    def fetch(self, request: RunRequest, refresh: bool = False) -> WeatherSnapshot:
        del refresh
        frame = pd.DataFrame.from_records(self.payload["rows"])
        for column in ("valid_time", "initialized_at", "issued_at", "available_at"):
            if column in frame:
                frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce")
        expected_start = pd.Timestamp(request.origin).tz_convert("UTC")
        if frame.empty or frame["valid_time"].min() != expected_start:
            raise ValueError("weather fixture origin does not match the requested origin")
        last_time = expected_start + pd.Timedelta(hours=request.horizon - 1)
        if frame["valid_time"].max() < last_time:
            raise ValueError("weather fixture does not cover the requested horizon")
        frame = frame.loc[frame["valid_time"] <= last_time].copy()
        frame["lead_hours"] = (
            (frame["valid_time"] - expected_start).dt.total_seconds() / 3600
        ).astype("int64")
        provenance = dict(self.payload.get("provenance") or {})
        fingerprint = weather_fingerprint(frame, provenance)
        return WeatherSnapshot(
            rows=frame,
            raw_responses=[],
            fingerprint=fingerprint,
            provenance=provenance,
        )


class _DeterministicAnalyzer:
    """Select only safe agent actions and never makes an external request."""

    def __call__(self, diagnostics: dict[str, Any], allowed: set[str]) -> Decision:
        if "use_baseline" in allowed and diagnostics.get("baseline_available") is True:
            return Decision("use_baseline", "deterministic_fallback:model_unavailable")
        if "finalize" in allowed:
            return Decision("finalize", "deterministic_fallback:validated_forecast")
        if "continue" in allowed:
            return Decision("continue", "deterministic_fallback:continue_validated_run")
        return Decision("abort", "deterministic_fallback:no_safe_action")


@dataclass(frozen=True)
class _PreparedSource:
    paths: dict[str, Path]
    fingerprints: dict[str, str]
    identity: str
    is_hourly: bool = False


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
        if self.offline and self.weather_fixture is not None:
            raise ValueError("--offline and --weather-fixture cannot be used together")
        if weather_provider is not None:
            self.weather_provider = weather_provider
        elif self.offline:
            self.weather_provider = _SyntheticWeatherProvider()
        elif self.weather_fixture is not None:
            self.weather_provider = _FixtureWeatherProvider(self.weather_fixture)
        else:
            self.weather_provider = WeatherProvider(settings.cache_dir)
        self.analyzer = analyzer or (
            _DeterministicAnalyzer()
            if self.offline or not settings.openai_api_key
            else OpenAIAnalyzer(settings)
        )
        self.model_parameters = {**DEFAULT_MODEL_PARAMETERS, **dict(model_parameters or {})}
        self.data_pipeline = DataPipeline()
        self.feature_pipeline = FeaturePipeline()
        self.store = ArtifactStore(settings.run_dir)
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
        manifest_path.write_text(
            json.dumps(
                {"schema_version": 1, "source_fingerprints": source.fingerprints},
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        cached_manifest = {
            "schema_version": 1,
            "source_fingerprints": source.fingerprints,
            "prepared_sha256": _sha256(destination / "hourly.csv"),
        }
        manifest_path.write_text(
            json.dumps(cached_manifest, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
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

        baseline_strategy, baseline_scores = self._select_calibration_baseline(
            history, calibration_rows_frame, calibration_snapshots
        )
        baseline_pointer = {
            "schema_version": 1,
            "mode": selected_mode,
            "weather_source": self._snapshot_identity,
            "source_fingerprint": self._source_identity(),
            "strategy": baseline_strategy,
            "calibration_scores": baseline_scores,
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
        path = self._baseline_selection_path(mode)
        if not path.is_file():
            return "persistence"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if (
                value.get("source_fingerprint") == self._source_identity()
                and value.get("weather_source") == self._snapshot_identity
                and value.get("mode") == mode
                and value.get("strategy") in ("persistence", "seasonal", "power_curve")
            ):
                calibration_cutoff = value.get("calibration_cutoff")
                if origin is not None and calibration_cutoff:
                    if pd.Timestamp(calibration_cutoff) > pd.Timestamp(origin):
                        return "persistence"
                return value["strategy"]
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass
        return "persistence"

    def _select_calibration_baseline(
        self,
        history: pd.DataFrame,
        calibration: pd.DataFrame,
        snapshots: list[tuple[RunRequest, WeatherSnapshot]],
    ) -> tuple[str, dict[str, dict[str, float | int | None]]]:
        from wind_forecast.models import select_baseline

        errors: dict[str, list[float]] = {
            "persistence": [], "seasonal": [], "power_curve": []
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
        return select_baseline(scores), scores

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(path)

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

    def _past_backtest_metrics(
        self, origin: datetime | pd.Timestamp | None = None
    ) -> dict[str, Any]:
        metrics_path = self.settings.model_dir / "backtest" / "metrics.json"
        if not metrics_path.is_file():
            return {
                "schema_version": 1,
                "metric_status": "unavailable_no_labels",
                "evaluation_period": None,
                "models": [],
                "coverage": {},
            }
        try:
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            if not metrics.get("evaluation_period"):
                raise ValueError("missing evaluation period")
            if origin is not None:
                evaluation_end = pd.Timestamp(metrics["evaluation_period"].get("end"))
                if evaluation_end.tzinfo is None or evaluation_end > pd.Timestamp(origin):
                    raise ValueError("backtest metrics follow forecast origin")
            return metrics
        except (OSError, ValueError, json.JSONDecodeError):
            return {
                "schema_version": 1,
                "metric_status": "unavailable_no_labels",
                "evaluation_period": None,
                "models": [],
                "coverage": {},
            }

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
        model = self._ensure_model(parsed.mode, parsed.origin)
        history = all_history.loc[
            all_history["available_at"].le(pd.Timestamp(parsed.origin))
        ].copy()
        baseline: BaselineModel | None = None

        def _feature_frame(rows: pd.DataFrame, snapshot: WeatherSnapshot, req: RunRequest) -> pd.DataFrame:
            return self.feature_pipeline.build(rows, snapshot, req)

        def _predict(rows: pd.DataFrame, snapshot: WeatherSnapshot, req: RunRequest):
            features = _feature_frame(rows, snapshot, req)
            if model is None:
                raise RuntimeError("forecast_model_unavailable")
            prediction = model.predict(features)
            prediction.diagnostics.update(self._model_diagnostics(model, history, req))
            prediction.diagnostics["metrics"] = self._past_backtest_metrics(req.origin)
            return prediction

        def _predict_baseline(rows: pd.DataFrame, snapshot: WeatherSnapshot, req: RunRequest):
            nonlocal baseline
            features = _feature_frame(rows, snapshot, req)
            if baseline is None:
                baseline = BaselineModel.fit(rows, req.origin)
            strategy = self._selected_baseline(req.mode, req.origin)
            prediction = baseline.predict(features, strategy)
            data_quality = self._data_quality(rows, req.origin)
            prediction.diagnostics.update(
                {
                    "model_parameters": {"strategy": strategy},
                    "seed": None,
                    "training_cutoff": req.origin.isoformat(),
                    "training_rows": len(rows),
                    "calibration_cutoff": None,
                    "model_training_rows_fingerprint": self._source_identity(),
                    "feature_schema": list(FEATURE_COLUMNS),
                    "config_fingerprint": _json_hash(
                        {"mode": req.mode, "model": strategy, "weather_source": self._snapshot_identity}
                    ),
                    "dependency_versions": {},
                    "data_quality": data_quality,
                    "degraded": bool(data_quality["stale_observations"]),
                    "metrics": self._past_backtest_metrics(req.origin),
                }
            )
            return prediction

        agent = ForecastAgent(
            self.weather_provider,
            _predict,
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
        latest = pd.to_datetime(history["available_at"], utc=True).max() if not history.empty else None
        cutoff_stamp = pd.Timestamp(cutoff) if cutoff is not None else None
        age = (
            max(0.0, (cutoff_stamp - latest).total_seconds() / 3600)
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
            self.settings.model_dir / "backtest" / "models",
            model_parameters=self._model_parameters(iterations),
            calibration_days=calibration_days,
        )
        metrics_path = Path(result["metrics_path"])
        weather_verified = bool(snapshots) and all(
            snapshot.provenance.get("provenance_status") == "verified"
            and snapshot.provenance.get("competition_valid") is True
            for _, snapshot in snapshots
        )
        if not weather_verified:
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            metrics["metric_status"] = "unavailable_unverified_weather"
            metrics["models"] = []
            metrics.setdefault("coverage", {})["competition_valid"] = False
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
