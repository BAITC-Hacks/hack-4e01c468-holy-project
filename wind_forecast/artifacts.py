"""Atomic, content-addressed storage for forecast runs and audit artifacts."""

from __future__ import annotations

import hashlib
import json
import math
import numbers
import os
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Iterator

import numpy as np
import pandas as pd

from wind_forecast.agent import quality_gate
from wind_forecast.contracts import Prediction, RunRequest, RunResult, WeatherSnapshot
from wind_forecast.weather import weather_fingerprint

_FORECAST_COLUMNS = [
    "run_id",
    "forecast_origin",
    "turbine_id",
    "valid_time",
    "lead_hours",
    "p10",
    "p50",
    "p90",
    "weather_issued_at",
    "weather_model",
    "run_status",
]
_STATUSES = {"success", "degraded", "failed"}
_ACTIONS = {
    "continue", "retry_weather", "use_cached_weather", "use_baseline", "abort", "finalize"
}
_EVENT_OUTCOMES = {"success", "failed", "skipped", "rejected"}
_FINGERPRINT_FIELDS = {
    "data_contents", "canonical_weather", "config", "feature_schema",
    "model_training_rows", "predictor_config", "predictor_model",
    "predictor_identity", "request",
}
_MODEL_FIELDS = {
    "name", "parameters", "seed", "training_cutoff", "training_rows",
    "calibration_cutoff", "dependency_versions",
}


def _utc_stamp(value: Any) -> str | None:
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        raise ValueError("artifact_timestamp_must_be_zoned")
    return stamp.tz_convert("UTC").isoformat().replace("+00:00", "Z")


def _jsonable(value: Any) -> Any:
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return _utc_stamp(value)
    if isinstance(value, np.generic):
        return _jsonable(value.item())
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    try:
        missing = pd.isna(value)
        if isinstance(missing, (bool, np.bool_)) and missing:
            return None
    except (TypeError, ValueError):
        pass
    raise TypeError(f"not_json_serializable:{type(value).__name__}")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _fingerprint_value(value: Any) -> Any:
    """Serialize malformed input timestamps for a failure fingerprint only."""
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        stamp = pd.Timestamp(value)
        if stamp.tzinfo is not None:
            stamp = stamp.tz_convert("UTC")
            return stamp.isoformat().replace("+00:00", "Z")
        return stamp.isoformat()
    if isinstance(value, np.generic):
        return _fingerprint_value(value.item())
    if isinstance(value, bool):
        return value
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    if isinstance(value, str):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _fingerprint_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_fingerprint_value(item) for item in value]
    try:
        missing = pd.isna(value)
        if isinstance(missing, (bool, np.bool_)) and missing:
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def _frame_fingerprint(frame: pd.DataFrame) -> str:
    columns = sorted(str(column) for column in frame.columns)
    records = []
    for raw in frame.to_dict(orient="records"):
        row = {str(key): _fingerprint_value(value) for key, value in raw.items()}
        records.append(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False))
    records.sort()
    return _sha256(
        json.dumps(
            {"columns": columns, "rows": records},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _redact_secret_fields(value: Any) -> Any:
    secret_fragments = ("api_key", "authorization", "password", "secret", "access_token")
    if isinstance(value, dict):
        return {
            str(key): _redact_secret_fields(item)
            for key, item in value.items()
            if not any(fragment in str(key).lower() for fragment in secret_fragments)
        }
    if isinstance(value, (list, tuple, set)):
        return [_redact_secret_fields(item) for item in value]
    return value


def _snapshot_fingerprint(snapshot: WeatherSnapshot) -> str:
    try:
        return weather_fingerprint(snapshot.rows, snapshot.provenance)
    except Exception:
        rows = snapshot.rows
        if isinstance(rows, pd.DataFrame):
            row_identity: Any = {"dataframe": _frame_fingerprint(rows)}
        else:
            row_identity = _fingerprint_value(rows)
        provenance = {
            key: value
            for key, value in snapshot.provenance.items()
            if key not in {"retrieved_at", "generationtime_ms", "cache_hit", "http_status"}
        }
        return _sha256(
            json.dumps(
                {
                    "invalid_snapshot": row_identity,
                    "source_fingerprint": snapshot.fingerprint,
                    "provenance": _fingerprint_value(provenance),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        )


class ArtifactStore:
    """Persist complete runs under one root, with reuse and atomic publication."""

    def __init__(self, root: Path, lock_timeout_seconds: float = 30.0) -> None:
        self.root = Path(root).expanduser().resolve()
        self.lock_timeout_seconds = lock_timeout_seconds

    def persist(
        self,
        request: RunRequest,
        prediction: Prediction | None,
        snapshot: WeatherSnapshot | None,
        metrics: dict[str, Any],
        events: list[dict[str, Any]],
        report: str,
        manifest: dict[str, Any],
    ) -> RunResult:
        supplied = dict(manifest or {})
        status = supplied.get("status", "success")
        if status not in _STATUSES:
            raise ValueError("artifact_run_status_invalid")
        if status != "failed":
            if prediction is None or snapshot is None:
                raise ValueError("artifact_success_requires_prediction_and_weather")
            prediction = quality_gate(prediction, snapshot, request)
        else:
            prediction = None

        self.root.mkdir(parents=True, exist_ok=True)
        scope = {
            "forecast_origin": _utc_stamp(request.origin),
            "horizon": request.horizon,
            "mode": request.mode,
        }
        normalized_manifest = self._normalize_manifest(
            supplied, request, prediction, snapshot, status
        )
        request_fingerprint = self._request_fingerprint(scope, normalized_manifest)
        normalized_manifest["fingerprints"]["request"] = request_fingerprint
        lock_key = _sha256(_canonical_json(scope).encode("utf-8"))

        with self._lock(lock_key):
            if status != "failed":
                existing = self._find_matching(
                    request_fingerprint, scope, request, snapshot
                )
                if existing is not None:
                    return RunResult(
                        existing["run_id"], existing["directory"], existing["status"], True
                    )

            parent = self._latest_for_scope(scope)
            normalized_manifest["parent_run_id"] = parent["run_id"] if parent else None
            run_id = self._new_run_id(scope, request_fingerprint)
            normalized_manifest["run_id"] = run_id
            normalized_manifest["created_at"] = self._now()
            final_directory = self.root / run_id
            if final_directory.exists():
                run_id = f"{run_id}-{uuid.uuid4().hex[:8]}"
                normalized_manifest["run_id"] = run_id
                final_directory = self.root / run_id

            staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=self.root))
            try:
                files: dict[str, str] = {}
                if prediction is not None and snapshot is not None:
                    forecast_text = self._forecast_csv(
                        run_id, request, prediction, snapshot, status
                    )
                    files["forecast.csv"] = self._write_text(staging, "forecast.csv", forecast_text)

                metrics_payload = self._metrics_payload(metrics)
                metrics_text = self._pretty_json(metrics_payload)
                files["metrics.json"] = self._write_text(staging, "metrics.json", metrics_text)

                weather_payload = {
                    "schema_version": 1,
                    "raw_responses": snapshot.raw_responses if snapshot is not None else [],
                    "snapshot_fingerprint": _snapshot_fingerprint(snapshot) if snapshot is not None else None,
                }
                weather_text = self._pretty_json(weather_payload)
                files["weather.json"] = self._write_text(staging, "weather.json", weather_text)

                files["report.md"] = self._write_text(staging, "report.md", str(report).rstrip() + "\n")

                stored_events = self._events_with_run_id(events, run_id)
                persist_start = time.perf_counter()
                persist_event = self._persist_event(stored_events)
                if persist_event is None:
                    persist_event = self._append_persist_event(stored_events, run_id)
                persist_event["timestamp"] = self._now()
                persist_event["duration_ms"] = max(0, int((time.perf_counter() - persist_start) * 1000))
                persist_event["outcome"] = "success"
                events_text = "".join(
                    json.dumps(_jsonable(event), ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
                    for event in stored_events
                )
                files["events.jsonl"] = self._write_text(staging, "events.jsonl", events_text)

                normalized_manifest["files"] = files
                manifest_text = self._pretty_json(normalized_manifest)
                self._write_text(staging, "manifest.json", manifest_text)

                os.replace(staging, final_directory)
                if status != "failed":
                    self._write_latest(run_id, status, final_directory)
            except Exception:
                if staging.exists():
                    self._remove_tree(staging)
                raise

        return RunResult(run_id, final_directory, status, False)

    def check_for_updates(
        self,
        request: RunRequest,
        snapshot: WeatherSnapshot,
        fingerprints: dict[str, str],
    ) -> RunResult | None:
        """Find a complete prior run with matching inputs and a still-valid forecast.

        The agent validates current weather before calling this method. The
        stored forecast is checksum-checked and passed through the quality gate
        again before a hit is returned.
        """
        expected = {
            key: value
            for key, value in fingerprints.items()
            if key in _FINGERPRINT_FIELDS and isinstance(value, str) and value
        }
        required = {
            "data_contents", "canonical_weather", "predictor_config", "predictor_model"
        }
        if not required.issubset(expected):
            return None

        scope = {
            "forecast_origin": _utc_stamp(request.origin),
            "horizon": request.horizon,
            "mode": request.mode,
        }
        candidates: list[tuple[str, Path, dict[str, Any]]] = []
        if not self.root.is_dir():
            return None
        for directory in self.root.glob("run-*"):
            manifest_path = directory / "manifest.json"
            if not manifest_path.is_file():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            current_scope = {
                "forecast_origin": manifest.get("forecast_origin"),
                "horizon": manifest.get("horizon"),
                "mode": manifest.get("mode"),
            }
            stored_fingerprints = manifest.get("fingerprints", {})
            if (
                current_scope == scope
                and manifest.get("status") in {"success", "degraded"}
                and all(stored_fingerprints.get(key) == value for key, value in expected.items())
            ):
                candidates.append((str(manifest.get("created_at", "")), directory, manifest))

        for _, directory, manifest in sorted(candidates, reverse=True, key=lambda row: row[0]):
            try:
                if not self._stored_run_artifacts_are_valid(
                    directory, manifest, request, snapshot
                ):
                    continue
                run_id = str(manifest.get("run_id", directory.name))
                self._record_update_check(scope, run_id, expected)
                return RunResult(
                    run_id,
                    directory,
                    manifest["status"],
                    True,
                )
            except (OSError, ValueError, KeyError, TypeError, pd.errors.ParserError):
                continue
        return None

    def record_reuse_events(
        self, events: list[dict[str, Any]], reused_run_id: str
    ) -> str:
        """Durably store the current invocation trace beside immutable reused runs."""
        if not isinstance(reused_run_id, str) or not reused_run_id:
            raise ValueError("artifact_reuse_run_id_invalid")

        invocation_id = uuid.uuid4().hex
        reuse_root = self.root / "reuse-events"
        reuse_root.mkdir(parents=True, exist_ok=True)
        normalized = self._reuse_events_with_identity(
            events, invocation_id, reused_run_id
        )
        content = "".join(_canonical_json(event) + "\n" for event in normalized)
        final_path = reuse_root / f"{invocation_id}.jsonl"
        fd, temporary_name = tempfile.mkstemp(prefix=".staging-", dir=reuse_root)
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, final_path)
            directory_fd = os.open(reuse_root, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()
        return invocation_id

    @classmethod
    def _reuse_events_with_identity(
        cls,
        events: list[dict[str, Any]],
        invocation_id: str,
        reused_run_id: str,
    ) -> list[dict[str, Any]]:
        normalized = cls._events_with_run_id(events, reused_run_id)
        result = []
        for event in normalized:
            event.pop("run_id", None)
            event["details"] = event.get("details", {})
            event["invocation_id"] = invocation_id
            event["reused_run_id"] = reused_run_id
            result.append(event)
        return result

    def _record_update_check(
        self,
        scope: dict[str, Any],
        run_id: str,
        fingerprints: dict[str, str],
    ) -> None:
        """Append a durable record when a prior forecast safely satisfies a run."""
        self.root.mkdir(parents=True, exist_ok=True)
        lock_key = _sha256(_canonical_json(scope).encode("utf-8"))
        entry = {
            "schema_version": 1,
            "state": "check_for_updates",
            "action": "continue",
            "reason": "matching_forecast_reused",
            "reuse_outcome": "hit",
            "run_id": run_id,
            "fingerprints": fingerprints,
            "timestamp": self._now(),
        }
        with self._lock(lock_key):
            with (self.root / "update_checks.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(_canonical_json(entry) + "\n")
                stream.flush()
                os.fsync(stream.fileno())

    def _normalize_manifest(
        self,
        manifest: dict[str, Any],
        request: RunRequest,
        prediction: Prediction | None,
        snapshot: WeatherSnapshot | None,
        status: str,
    ) -> dict[str, Any]:
        result = dict(manifest)
        fingerprints = {
            key: value
            for key, value in dict(result.get("fingerprints") or {}).items()
            if key in _FINGERPRINT_FIELDS
        }
        fingerprints.setdefault("data_contents", "")
        fingerprints["canonical_weather"] = _snapshot_fingerprint(snapshot) if snapshot is not None else None
        fingerprints.setdefault("config", "")
        fingerprints.setdefault("feature_schema", "")
        fingerprints.setdefault("model_training_rows", "")
        model = {
            key: _redact_secret_fields(value)
            for key, value in dict(result.get("model") or {}).items()
            if key in _MODEL_FIELDS
        }
        normalized = {
            "schema_version": 1,
            "run_id": "",
            "forecast_origin": _utc_stamp(request.origin),
            "horizon": request.horizon,
            "mode": request.mode,
            "status": status,
            "competition_valid": bool(
                status != "failed"
                and request.mode == "competition"
                and snapshot is not None
                and snapshot.provenance.get("provenance_status") == "verified"
            ),
            "created_at": self._now(),
            "parent_run_id": None,
            "fingerprints": fingerprints,
            "model": model,
            "weather_provenance": _redact_secret_fields(
                _fingerprint_value(snapshot.provenance) if snapshot is not None else {}
            ),
            "data_quality": _redact_secret_fields(dict(result.get("data_quality") or {})),
            "calibration": _redact_secret_fields(_jsonable(result.get("calibration") or {})),
            "files": {},
        }
        normalized["model"].setdefault("name", prediction.model_name if prediction is not None else "unavailable")
        return normalized

    @staticmethod
    def _request_fingerprint(scope: dict[str, Any], manifest: dict[str, Any]) -> str:
        identity = {
            "scope": scope,
            "fingerprints": manifest["fingerprints"],
            "model": manifest["model"],
        }
        return _sha256(_canonical_json(identity).encode("utf-8"))

    def _find_matching(
        self,
        request_fingerprint: str,
        scope: dict[str, Any],
        request: RunRequest,
        snapshot: WeatherSnapshot,
    ) -> dict[str, Any] | None:
        matches = []
        for directory in self.root.glob("run-*"):
            manifest_path = directory / "manifest.json"
            if not manifest_path.is_file():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(manifest, dict):
                continue
            fingerprints = manifest.get("fingerprints")
            if not isinstance(fingerprints, dict):
                continue
            fp = fingerprints.get("request")
            current_scope = {
                "forecast_origin": manifest.get("forecast_origin"),
                "horizon": manifest.get("horizon"),
                "mode": manifest.get("mode"),
            }
            if (
                fp == request_fingerprint
                and current_scope == scope
                and manifest.get("status") in {"success", "degraded"}
            ):
                try:
                    valid = self._stored_run_artifacts_are_valid(
                        directory, manifest, request, snapshot
                    )
                except (OSError, ValueError, KeyError, TypeError, pd.errors.ParserError):
                    valid = False
                if valid:
                    matches.append(
                        {
                            "run_id": manifest.get("run_id", directory.name),
                            "directory": directory,
                            "status": manifest["status"],
                            "created_at": manifest.get("created_at", ""),
                        }
                    )
        return max(matches, key=lambda match: match["created_at"]) if matches else None

    @staticmethod
    def _stored_run_artifacts_are_valid(
        directory: Path,
        manifest: dict[str, Any],
        request: RunRequest,
        snapshot: WeatherSnapshot,
    ) -> bool:
        """Verify all required file hashes and the exact stored forecast grid."""
        files = manifest.get("files")
        required_files = {
            "forecast.csv", "metrics.json", "events.jsonl", "weather.json", "report.md",
        }
        if not isinstance(files, dict) or set(files) != required_files:
            return False
        for name, expected_hash in files.items():
            if not isinstance(expected_hash, str):
                return False
            if _sha256((directory / name).read_bytes()) != expected_hash:
                return False

        frame = pd.read_csv(directory / "forecast.csv")
        if not set(_FORECAST_COLUMNS).issubset(frame.columns):
            return False
        expected_run_id = str(manifest.get("run_id", directory.name))
        if not frame["run_id"].astype(str).eq(expected_run_id).all():
            return False
        if not frame["forecast_origin"].astype(str).eq(_utc_stamp(request.origin)).all():
            return False
        if not frame["run_status"].astype(str).eq(str(manifest.get("status"))).all():
            return False

        model = manifest.get("model")
        model_name = model.get("name", "unavailable") if isinstance(model, dict) else "unavailable"
        prediction = Prediction(
            rows=frame.loc[:, ["turbine_id", "valid_time", "lead_hours", "p10", "p50", "p90"]],
            model_name=str(model_name),
            diagnostics={},
        )
        validated = quality_gate(prediction, snapshot, request)
        gate = validated.diagnostics["quality_gate"]
        return gate["quantile_correction_count"] == 0 and gate["clipping_count"] == 0

    def _latest_for_scope(self, scope: dict[str, Any]) -> dict[str, Any] | None:
        matches = []
        for directory in self.root.glob("run-*"):
            path = directory / "manifest.json"
            if not path.is_file():
                continue
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            current_scope = {
                "forecast_origin": manifest.get("forecast_origin"),
                "horizon": manifest.get("horizon"),
                "mode": manifest.get("mode"),
            }
            if current_scope == scope:
                matches.append(
                    {
                        "run_id": manifest.get("run_id", directory.name),
                        "created_at": manifest.get("created_at", ""),
                        "status": manifest.get("status", "failed"),
                    }
                )
        return max(matches, key=lambda match: match["created_at"]) if matches else None

    @staticmethod
    def _new_run_id(scope: dict[str, Any], fingerprint: str) -> str:
        origin = pd.Timestamp(scope["forecast_origin"]).strftime("%Y%m%dT%H%M%SZ")
        return f"run-{origin}-h{scope['horizon']}-{fingerprint[:12]}"

    @contextmanager
    def _lock(self, key: str) -> Iterator[None]:
        lock_dir = self.root / ".locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / f"{key}.lock"
        deadline = time.monotonic() + self.lock_timeout_seconds
        fd: int | None = None
        while fd is None:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.write(fd, f"pid={os.getpid()}\nstarted={self._now()}\n".encode("utf-8"))
                os.fsync(fd)
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("artifact_lock_timeout")
                time.sleep(0.025)
        try:
            yield
        finally:
            os.close(fd)
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _forecast_csv(
        run_id: str,
        request: RunRequest,
        prediction: Prediction,
        snapshot: WeatherSnapshot,
        status: str,
    ) -> str:
        weather_lookup: dict[tuple[str, pd.Timestamp], tuple[Any, Any]] = {}
        for row in snapshot.rows.to_dict(orient="records"):
            key = (row["turbine_id"], pd.Timestamp(row["valid_time"]).tz_convert("UTC"))
            weather_lookup[key] = (row.get("issued_at"), row.get("weather_model"))

        origin = _utc_stamp(request.origin)
        records = []
        for row in prediction.rows.to_dict(orient="records"):
            valid_time = pd.Timestamp(row["valid_time"]).tz_convert("UTC")
            issued_at, weather_model = weather_lookup[(row["turbine_id"], valid_time)]
            records.append(
                {
                    "run_id": run_id,
                    "forecast_origin": origin,
                    "turbine_id": row["turbine_id"],
                    "valid_time": _utc_stamp(valid_time),
                    "lead_hours": int(row["lead_hours"]),
                    "p10": float(row["p10"]),
                    "p50": float(row["p50"]),
                    "p90": float(row["p90"]),
                    "weather_issued_at": _utc_stamp(issued_at) if issued_at is not None else "",
                    "weather_model": str(weather_model or ""),
                    "run_status": status,
                }
            )
        frame = pd.DataFrame.from_records(records, columns=_FORECAST_COLUMNS)
        buffer = StringIO()
        frame.to_csv(buffer, index=False, lineterminator="\n")
        return buffer.getvalue()

    @staticmethod
    def _metrics_payload(metrics: dict[str, Any] | None) -> dict[str, Any]:
        source = metrics if isinstance(metrics, dict) else {}
        status = source.get("metric_status")
        if status is None:
            status = "available" if source.get("models") else "unavailable_no_labels"
        return {
            "schema_version": 1,
            "metric_status": status,
            "evaluation_period": _jsonable(source.get("evaluation_period")),
            "models": _redact_secret_fields(_jsonable(source.get("models") or [])),
            "coverage": _redact_secret_fields(_jsonable(source.get("coverage") or {})),
        }

    @staticmethod
    def _events_with_run_id(events: list[dict[str, Any]], run_id: str) -> list[dict[str, Any]]:
        normalized = []
        for item in events:
            action = item.get("action", "continue")
            outcome = item.get("outcome", "success")
            if action not in _ACTIONS or outcome not in _EVENT_OUTCOMES:
                raise ValueError("artifact_event_schema_invalid")
            try:
                duration = float(item.get("duration_ms", 0))
            except (TypeError, ValueError) as exc:
                raise ValueError("artifact_event_duration_invalid") from exc
            if not math.isfinite(duration) or duration < 0:
                raise ValueError("artifact_event_duration_invalid")
            timestamp = _utc_stamp(item.get("timestamp", ArtifactStore._now()))
            if timestamp is None:
                raise ValueError("artifact_event_timestamp_invalid")
            event = {
                "schema_version": 1,
                "run_id": run_id,
                "sequence": len(normalized) + 1,
                "state": str(item.get("state", "unknown")),
                "action": action,
                "reason": str(item.get("reason", ""))[:2000],
                "timestamp": timestamp,
                "duration_ms": int(duration),
                "outcome": outcome,
            }
            if isinstance(item.get("details"), dict):
                event["details"] = _redact_secret_fields(_jsonable(item["details"]))
            normalized.append(event)
        return normalized

    @staticmethod
    def _persist_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
        return next((event for event in reversed(events) if event.get("state") == "persist_run"), None)

    @staticmethod
    def _append_persist_event(events: list[dict[str, Any]], run_id: str) -> dict[str, Any]:
        event = {
            "schema_version": 1,
            "sequence": len(events) + 1,
            "state": "persist_run",
            "action": "continue",
            "reason": "write immutable run artifacts",
            "timestamp": ArtifactStore._now(),
            "duration_ms": 0,
            "outcome": "success",
            "run_id": run_id,
        }
        events.append(event)
        return event

    @staticmethod
    def _write_text(directory: Path, name: str, content: str) -> str:
        path = directory / name
        with path.open("w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        return _sha256(path.read_bytes())

    @staticmethod
    def _pretty_json(value: Any) -> str:
        return json.dumps(
            _jsonable(value), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
        ) + "\n"

    def _write_latest(self, run_id: str, status: str, directory: Path) -> None:
        pointer = {
            "schema_version": 1,
            "run_id": run_id,
            "directory": directory.name,
            "status": status,
            "updated_at": self._now(),
        }
        fd, temp_name = tempfile.mkstemp(prefix=".latest-", suffix=".tmp", dir=self.root)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(self._pretty_json(pointer))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, self.root / "latest.json")
        finally:
            if temp_path.exists():
                temp_path.unlink()

    @staticmethod
    def _remove_tree(path: Path) -> None:
        for child in path.iterdir():
            if child.is_dir() and not child.is_symlink():
                ArtifactStore._remove_tree(child)
            else:
                child.unlink()
        path.rmdir()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
