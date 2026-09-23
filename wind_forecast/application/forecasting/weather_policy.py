"""Pure weather provenance, validation, and canonical fingerprint rules."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from wind_forecast.contracts import RunRequest, WeatherSnapshot

WEATHER_VARIABLES: tuple[str, ...] = (
    "wind_speed_10m",
    "wind_speed_100m",
    "wind_direction_10m",
    "wind_gusts_10m",
    "temperature_2m",
    "surface_pressure",
)
WEATHER_COLUMNS: tuple[str, ...] = (
    "turbine_id",
    "valid_time",
    "initialized_at",
    "issued_at",
    "available_at",
    "lead_hours",
    "weather_model",
    *WEATHER_VARIABLES,
)
TURBINE_IDS: tuple[str, str] = ("turbine_1", "turbine_2")
_TIME_COLUMNS = ("valid_time", "initialized_at", "issued_at", "available_at")
_VOLATILE_PROVENANCE_KEYS = {
    "retrieved_at",
    "generationtime_ms",
    "http_status",
    "request_id",
    "request_duration_ms",
    "cache_path",
}
_UNITS = {
    "wind_speed": "m/s",
    "wind_speed_unit": "ms",
    "wind_direction": "degrees",
    "temperature": "°C",
    "surface_pressure": "hPa",
}


def _as_utc(value: Any, field: str) -> pd.Timestamp:
    """Parse an explicitly zoned value and normalize it to UTC."""
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"invalid_{field}: expected an ISO timestamp") from exc
    elif isinstance(value, (datetime, pd.Timestamp)):
        parsed = value.to_pydatetime() if isinstance(value, pd.Timestamp) else value
    else:
        raise ValueError(f"invalid_{field}: expected a zoned timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"invalid_{field}: timestamp must include a timezone")
    return pd.Timestamp(parsed.astimezone(timezone.utc))


def _optional_utc(value: Any, field: str) -> pd.Timestamp | None:
    if value is None or pd.isna(value):
        return None
    return _as_utc(value, field)


def _iso(value: Any, field: str) -> str | None:
    parsed = _optional_utc(value, field)
    return parsed.isoformat().replace("+00:00", "Z") if parsed is not None else None


def _request_origin(request: RunRequest) -> pd.Timestamp:
    return _as_utc(request.origin, "forecast_origin")


def canonical_hash(payload: Any) -> str:
    """Hash JSON-compatible values using deterministic UTF-8 JSON."""
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def weather_fingerprint(rows: pd.DataFrame, provenance: dict[str, Any]) -> str:
    """Return the stable content fingerprint used for canonical weather identity."""
    if not isinstance(rows, pd.DataFrame):
        raise ValueError("invalid_weather_rows: expected a DataFrame")
    missing = [column for column in WEATHER_COLUMNS if column not in rows.columns]
    if missing:
        raise ValueError(f"missing_weather_columns: {', '.join(missing)}")

    canonical_rows: list[dict[str, Any]] = []
    ordered = rows.loc[:, list(WEATHER_COLUMNS)].copy()
    if not ordered.empty:
        ordered["valid_time"] = ordered["valid_time"].map(
            lambda value: _iso(value, "valid_time")
        )
        ordered = ordered.sort_values(["turbine_id", "valid_time"], kind="stable")
    for record in ordered.to_dict(orient="records"):
        normalized: dict[str, Any] = {}
        for column in WEATHER_COLUMNS:
            value = record[column]
            if column in _TIME_COLUMNS:
                normalized[column] = _iso(value, column)
            elif column == "lead_hours":
                normalized[column] = int(value) if pd.notna(value) else None
            elif column in WEATHER_VARIABLES:
                normalized[column] = float(value) if pd.notna(value) else None
            else:
                normalized[column] = str(value) if pd.notna(value) else None
        canonical_rows.append(normalized)

    stable_provenance = {
        key: value
        for key, value in provenance.items()
        if key not in _VOLATILE_PROVENANCE_KEYS
    }
    return canonical_hash({"rows": canonical_rows, "provenance": stable_provenance})


def _reason(code: str, detail: str = "") -> ValueError:
    suffix = f": {detail}" if detail else ""
    return ValueError(f"{code}{suffix}")


def validate_weather(snapshot: WeatherSnapshot, request: RunRequest) -> None:
    """Validate exact two-turbine coverage and provenance for a run.

    Demo mode accepts a clearly labeled synthetic, assumed, unknown, or
    hindcast snapshot when its requested horizon is complete. Competition
    mode accepts only verified provenance with a proven issue/availability
    timestamp no later than the forecast origin.
    """
    origin = _request_origin(request)
    status = snapshot.provenance.get("provenance_status", "unknown")
    if request.mode == "competition" and (
        status != "verified" or snapshot.provenance.get("competition_valid") is not True
    ):
        raise _reason(
            "unverified_weather_provenance",
            f"status={status}; competition requires verified source availability",
        )
    if request.mode not in ("competition", "demo"):
        raise _reason("invalid_run_mode")

    rows = snapshot.rows
    if not isinstance(rows, pd.DataFrame):
        raise _reason("invalid_weather_rows", "expected a DataFrame")
    missing = [column for column in WEATHER_COLUMNS if column not in rows.columns]
    if missing:
        raise _reason("missing_weather_columns", ", ".join(missing))
    if rows.empty:
        raise _reason("weather_coverage_incomplete", "no weather rows")

    unit_info = snapshot.provenance.get("units", {})
    if not isinstance(unit_info, dict):
        raise _reason("weather_units_invalid")
    if unit_info.get("wind_speed_unit") not in ("ms", "m/s"):
        raise _reason("weather_units_invalid", "wind speed must be m/s")
    if unit_info.get("wind_direction") != _UNITS["wind_direction"]:
        raise _reason("weather_units_invalid", "wind direction must be degrees")
    if unit_info.get("temperature") != _UNITS["temperature"]:
        raise _reason("weather_units_invalid", "temperature must be °C")
    if unit_info.get("surface_pressure") != _UNITS["surface_pressure"]:
        raise _reason("weather_units_invalid", "surface pressure must be hPa")

    try:
        valid_times = rows["valid_time"].map(lambda value: _as_utc(value, "valid_time"))
    except ValueError as exc:
        raise _reason("weather_timestamp_invalid", str(exc)) from exc
    expected_times = pd.date_range(origin, periods=request.horizon, freq="h")
    expected_grid = {
        (turbine_id, valid_time)
        for turbine_id in TURBINE_IDS
        for valid_time in expected_times
    }
    if not rows["turbine_id"].isin(TURBINE_IDS).all():
        raise _reason("weather_turbine_invalid")
    grid = list(zip(rows["turbine_id"].astype(str), valid_times, strict=True))
    if len(grid) != len(set(grid)):
        raise _reason("weather_duplicate_hour")
    if set(grid) != expected_grid:
        raise _reason("weather_coverage_incomplete", "expected exactly two turbines × horizon")

    try:
        numeric_leads = pd.to_numeric(rows["lead_hours"], errors="raise")
        lead_values = numeric_leads.to_numpy(dtype="float64")
        if not all(
            math.isfinite(value)
            and value.is_integer()
            and 0 <= value < request.horizon
            for value in lead_values
        ):
            raise ValueError("lead hours must be finite in-range integers")
        leads = numeric_leads.astype("int64")
    except (TypeError, ValueError, OverflowError) as exc:
        raise _reason("weather_lead_invalid") from exc
    lead_by_key = {
        (turbine_id, valid_time): lead
        for turbine_id, valid_time, lead in zip(
            rows["turbine_id"].astype(str), valid_times, leads, strict=True
        )
    }
    for turbine_id in TURBINE_IDS:
        if [lead_by_key[(turbine_id, value)] for value in expected_times] != list(
            range(request.horizon)
        ):
            raise _reason("weather_lead_invalid")

    if rows["weather_model"].isna().any() or (rows["weather_model"].astype(str).str.len() == 0).any():
        raise _reason("weather_model_missing")
    if rows["weather_model"].astype(str).nunique() != 1:
        raise _reason("weather_model_mismatch")
    if snapshot.provenance.get("weather_model") not in (None, rows["weather_model"].iloc[0]):
        raise _reason("weather_model_mismatch")
    for column in WEATHER_VARIABLES:
        values = pd.to_numeric(rows[column], errors="coerce")
        if values.isna().any() or not values.map(math.isfinite).all():
            raise _reason("weather_value_missing", column)
        if column in ("wind_speed_10m", "wind_speed_100m", "wind_gusts_10m") and (values < 0).any():
            raise _reason("weather_value_out_of_range", column)
        if column == "wind_direction_10m" and ((values < 0) | (values > 360)).any():
            raise _reason("weather_value_out_of_range", column)
        if column == "surface_pressure" and (values <= 0).any():
            raise _reason("weather_value_out_of_range", column)

    row_provenance: dict[str, list[pd.Timestamp | None]] = {}
    for field in ("initialized_at", "issued_at", "available_at"):
        parsed: list[pd.Timestamp | None] = []
        try:
            parsed = [_optional_utc(value, field) for value in rows[field]]
        except ValueError as exc:
            raise _reason("weather_timestamp_invalid", str(exc)) from exc
        future = [value for value in parsed if value is not None and value > origin]
        if future:
            code = {
                "initialized_at": "future_weather_initialization",
                "issued_at": "future_weather_issued_at",
                "available_at": "future_weather_available_at",
            }[field]
            raise _reason(code)
        if request.mode == "competition" and any(value is None for value in parsed):
            raise _reason("unverified_weather_provenance", f"missing {field}")
        row_provenance[field] = parsed

    snapshot_provenance: dict[str, pd.Timestamp | None] = {}
    for field in ("initialized_at", "issued_at", "available_at"):
        raw_value = snapshot.provenance.get(field)
        try:
            parsed = _optional_utc(raw_value, field)
        except ValueError as exc:
            raise _reason("weather_timestamp_invalid", str(exc)) from exc
        if parsed is not None and parsed > origin:
            code = {
                "initialized_at": "future_weather_initialization",
                "issued_at": "future_weather_issued_at",
                "available_at": "future_weather_available_at",
            }[field]
            raise _reason(code)
        if request.mode == "competition" and parsed is None:
            raise _reason("unverified_weather_provenance", f"missing {field}")
        snapshot_provenance[field] = parsed

    for field in ("initialized_at", "issued_at", "available_at"):
        if any(value != snapshot_provenance[field] for value in row_provenance[field]):
            raise _reason("weather_provenance_mismatch", field)

    ordered_fields = ("initialized_at", "issued_at", "available_at")
    for index in range(len(rows)):
        known_row_times = [
            row_provenance[field][index]
            for field in ordered_fields
            if row_provenance[field][index] is not None
        ]
        if any(left > right for left, right in zip(known_row_times, known_row_times[1:])):
            raise _reason("weather_provenance_order_invalid")
    known_snapshot_times = [
        snapshot_provenance[field]
        for field in ordered_fields
        if snapshot_provenance[field] is not None
    ]
    if any(left > right for left, right in zip(known_snapshot_times, known_snapshot_times[1:])):
        raise _reason("weather_provenance_order_invalid")
