"""Convert persisted application results into safe transport DTOs."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

RUN_ID_PATTERN = re.compile(
    r"^run-\d{8}T\d{6}Z-h(?:24|48)-[0-9a-f]{12}(?:-[0-9a-f]{8})?$"
)
_PRIVATE_KEY_PARTS = (
    "key",
    "secret",
    "password",
    "credential",
    "authorization",
    "token",
)
_PATH_KEY_PATTERN = re.compile(r"(?:^|_)(?:path|directory|dir)$", re.IGNORECASE)
_LOCAL_PATH_PATTERN = re.compile(
    r"(?<![:\w])(?:/home|/tmp|/Users|/root|/var|/etc|/mnt|/opt|/srv|/private|/workspace)"
    r"(?:/[^\s,;\])}]+)*"
)
_WINDOWS_PATH_PATTERN = re.compile(r"\b[A-Za-z]:\\(?:[^\s,;\])}]+\\?)*")
_SECRET_VALUE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:sk-[A-Za-z0-9_-]{16,}|Bearer\s+[A-Za-z0-9._~+/=-]{8,})",
    re.IGNORECASE,
)
_RUN_VIEW_FIELDS = (
    "run_id",
    "status",
    "reused",
    "manifest",
    "metrics",
    "events",
    "report",
    "forecast",
)


def valid_run_id(value: str) -> bool:
    """Return whether a value matches the artifact store's generated IDs."""
    return bool(RUN_ID_PATTERN.fullmatch(value))


def run_view(result: Any, data: Mapping[str, Any]) -> dict[str, Any]:
    """Serialize one exact run result without exposing local runtime details."""
    run_id = getattr(result, "run_id", None)
    status = getattr(result, "status", None)
    if not isinstance(run_id, str) or not valid_run_id(run_id):
        raise ValueError("invalid_run_id")
    if status not in {"success", "degraded", "failed"}:
        raise ValueError("invalid_run_status")

    forecast = data.get("forecast")
    if isinstance(forecast, pd.DataFrame):
        forecast = forecast.to_dict(orient="records")
    elif isinstance(forecast, pd.Series):
        forecast = forecast.to_frame().T.to_dict(orient="records")
    if not isinstance(forecast, list):
        forecast = []

    manifest = data.get("manifest", {})
    metrics = data.get("metrics", {})
    events = data.get("events", [])
    report = data.get("report", "")
    view = {
        "run_id": run_id,
        "status": status,
        "reused": bool(getattr(result, "reused", False)),
        "manifest": _clean(manifest if isinstance(manifest, Mapping) else {}),
        "metrics": _clean(metrics if isinstance(metrics, Mapping) else {}),
        "events": _clean(events if isinstance(events, list) else []),
        "report": _clean(report if isinstance(report, str) else ""),
        "forecast": _clean(forecast),
    }
    return {key: view[key] for key in _RUN_VIEW_FIELDS}


def _clean(value: Any, *, key: str | None = None) -> Any:
    """Recursively make values JSON-safe while dropping secrets and paths."""
    if key is not None:
        normalized_key = key.casefold()
        if any(part in normalized_key for part in _PRIVATE_KEY_PARTS):
            return _DROP
        if normalized_key in {"path", "directory", "dir"} or _PATH_KEY_PATTERN.search(
            normalized_key
        ):
            return _DROP

    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, Path):
        return _DROP
    if isinstance(value, pd.Timestamp):
        return _timestamp(value.to_pydatetime())
    if isinstance(value, datetime):
        return _timestamp(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, np.generic):
        return _clean(value.item(), key=key)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, str):
        return _clean_string(value)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for child_key, child_value in value.items():
            name = str(child_key)
            cleaned = _clean(child_value, key=name)
            if cleaned is not _DROP:
                result[name] = cleaned
        return result
    if isinstance(value, (list, tuple, set)):
        return [item for item in (_clean(child) for child in value) if item is not _DROP]
    if isinstance(value, np.ndarray):
        return _clean(value.tolist())
    try:
        missing = pd.isna(value)
        if isinstance(missing, (bool, np.bool_)) and missing:
            return None
    except (TypeError, ValueError):
        pass
    return None


class _DropValue:
    pass


_DROP = _DropValue()


def _timestamp(value: datetime) -> str | None:
    if value.tzinfo is None or value.utcoffset() is None:
        return None
    normalized = value.astimezone(timezone.utc).isoformat(timespec="auto")
    return normalized.replace("+00:00", "Z")


def _clean_string(value: str) -> str:
    candidate = value.strip()
    if "T" in candidate and (candidate.endswith("Z") or "+" in candidate[10:] or "-" in candidate[10:]):
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except ValueError:
            pass
        else:
            if parsed.tzinfo is not None:
                return _timestamp(parsed) or ""
    cleaned = _LOCAL_PATH_PATTERN.sub("[local path]", value)
    cleaned = _WINDOWS_PATH_PATTERN.sub("[local path]", cleaned)
    return _SECRET_VALUE_PATTERN.sub("[redacted secret]", cleaned)
