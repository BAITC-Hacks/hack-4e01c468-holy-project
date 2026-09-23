"""Point-in-time weather retrieval, validation, caching, and demo fixtures.

Open-Meteo's Single Runs ``run`` parameter is a model initialization time. It
does not establish when the forecast became public. This module therefore
leaves ``issued_at`` and ``available_at`` null unless a source proves them;
archived values without that evidence are demo-only.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import requests

from wind_forecast.contracts import RunRequest, WeatherSnapshot


OPEN_METEO_SINGLE_RUNS_URL = "https://single-runs-api.open-meteo.com/v1/forecast"
WEATHER_MODEL = "ecmwf_ifs"
TURBINE_COORDINATES: tuple[tuple[str, float, float], ...] = (
    ("turbine_1", 43.645150, 78.535604),
    ("turbine_2", 43.643198, 78.538828),
)
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
    """Return a content fingerprint independent of row and HTTP order.

    Retrieval timestamps, response generation time, and HTTP metadata are
    intentionally excluded. Forecast values and stable provenance remain in
    the hash so changed data or source status creates a new snapshot identity.
    """
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
        for turbine_id, _, _ in TURBINE_COORDINATES
        for valid_time in expected_times
    }
    if not rows["turbine_id"].isin(["turbine_1", "turbine_2"]).all():
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
    for turbine_id in ("turbine_1", "turbine_2"):
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

class WeatherProvider:
    """Retrieve and cache two-site Open-Meteo Single Runs snapshots.

    ``transport`` is an injectable requests-compatible session, allowing all
    tests to use deterministic fake responses without making network requests.
    Cache entries are immutable and contain untouched response JSON plus
    separate retrieval/request metadata.
    """

    def __init__(
        self,
        cache_dir: Path,
        transport: requests.Session | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.transport = transport if transport is not None else requests.Session()

    @staticmethod
    def _run_for_origin(origin: pd.Timestamp) -> pd.Timestamp:
        # Use a cycle at least 12 hours before origin for the demo archive.
        # This is only a query selection rule; it is not proof of publication.
        safe_point = origin - pd.Timedelta(hours=12)
        floored_hour = (safe_point.hour // 6) * 6
        return safe_point.replace(hour=floored_hour, minute=0, second=0, microsecond=0)

    @staticmethod
    def _parameters(run: pd.Timestamp, horizon: int) -> dict[str, str | int]:
        forecast_hours = max(72, horizon + 18)
        return {
            "latitude": ",".join(f"{latitude:.6f}" for _, latitude, _ in TURBINE_COORDINATES),
            "longitude": ",".join(f"{longitude:.6f}" for _, _, longitude in TURBINE_COORDINATES),
            "models": WEATHER_MODEL,
            "run": run.strftime("%Y-%m-%dT%H:%M"),
            "forecast_hours": forecast_hours,
            "hourly": ",".join(WEATHER_VARIABLES),
            "wind_speed_unit": "ms",
            "timezone": "UTC",
        }

    def _cache_key(self, parameters: dict[str, str | int]) -> str:
        identity = {
            "provider": "open-meteo-single-runs",
            "model": WEATHER_MODEL,
            "coordinates": [list(row[1:]) for row in TURBINE_COORDINATES],
            "run": parameters["run"],
            "variables": list(WEATHER_VARIABLES),
            "units": _UNITS,
            "parameters": parameters,
        }
        return canonical_hash(identity)

    def _cache_paths(self, key: str) -> list[Path]:
        folder = self.cache_dir / "open-meteo"
        if not folder.exists():
            return []
        candidates: list[tuple[str, Path]] = []
        for path in folder.glob(f"{key}.*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                retrieved_at = str(data.get("retrieved_at", ""))
                candidates.append((retrieved_at, path))
            except (OSError, json.JSONDecodeError, AttributeError):
                continue
        return [path for _, path in sorted(candidates, reverse=True)]

    def _store_cache(
        self,
        key: str,
        parameters: dict[str, str | int],
        raw_responses: list[dict[str, Any]],
        retrieved_at: str,
        provenance: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], str]:
        body = {
            "schema_version": 1,
            "cache_key": key,
            "retrieved_at": retrieved_at,
            "request_metadata": {
                "endpoint": OPEN_METEO_SINGLE_RUNS_URL,
                "parameters": parameters,
            },
            "snapshot_provenance": provenance,
            "raw_responses": raw_responses,
        }
        response_hash = canonical_hash(raw_responses)
        folder = self.cache_dir / "open-meteo"
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / f"{key}.{response_hash}.json"
        if not destination.exists():
            fd, temporary_name = tempfile.mkstemp(prefix=".weather-", suffix=".tmp", dir=folder)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(body, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(temporary_name, destination)
                except FileExistsError:
                    pass
            finally:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass
        return raw_responses, body["retrieved_at"]

    @staticmethod
    def _read_cache(
        path: Path,
        expected_key: str,
        expected_parameters: dict[str, str | int],
        origin: pd.Timestamp,
        run: pd.Timestamp,
    ) -> tuple[list[dict[str, Any]], str]:
        data = json.loads(path.read_text(encoding="utf-8"))
        metadata = data.get("request_metadata", {})
        provenance = data.get("snapshot_provenance", {})
        if (
            data.get("schema_version") != 1
            or data.get("cache_key") != expected_key
            or metadata.get("endpoint") != OPEN_METEO_SINGLE_RUNS_URL
            or metadata.get("parameters") != expected_parameters
            or not isinstance(data.get("raw_responses"), list)
            or not isinstance(provenance, dict)
        ):
            raise ValueError("weather_cache_invalid")
        try:
            _as_utc(data.get("retrieved_at"), "retrieved_at")
        except ValueError as exc:
            raise ValueError("weather_cache_timestamp_invalid") from exc
        for field, reason_code in (
            ("initialized_at", "future_weather_initialization"),
            ("issued_at", "future_weather_issued_at"),
            ("available_at", "future_weather_available_at"),
        ):
            value = _optional_utc(provenance.get(field), field)
            if value is not None and value > origin:
                raise _reason(reason_code, "cached provenance is after forecast origin")
        cached_run = _optional_utc(provenance.get("initialized_at"), "initialized_at")
        if cached_run != run or provenance.get("issued_at") is not None or provenance.get("available_at") is not None:
            raise ValueError("weather_cache_provenance_mismatch")
        return data["raw_responses"], str(data.get("retrieved_at", ""))

    @staticmethod
    def _archive_status(run: pd.Timestamp) -> str:
        # Open-Meteo documents its earlier ECMWF IFS Cycle 49R1 archive as
        # hindcasts, with Cycle 50R1 runs starting on 2026-05-12. Later runs
        # still have no per-run release evidence here.
        cutoff = pd.Timestamp("2026-05-12T06:00:00Z")
        return "hindcast" if run < cutoff else "unknown"

    @staticmethod
    def _make_snapshot(
        raw_responses: list[dict[str, Any]],
        request: RunRequest,
        run: pd.Timestamp,
        retrieved_at: str,
        parameters: dict[str, str | int],
    ) -> WeatherSnapshot:
        origin = _request_origin(request)
        response_objects: list[dict[str, Any]] = []
        for response in raw_responses:
            if isinstance(response, list):
                response_objects.extend(item for item in response if isinstance(item, dict))
            elif isinstance(response, dict):
                response_objects.append(response)
        if len(response_objects) != len(TURBINE_COORDINATES):
            raise _reason("weather_response_invalid", "expected two coordinate responses")

        expected_times = pd.date_range(origin, periods=request.horizon, freq="h")
        records: list[dict[str, Any]] = []
        observed_units: dict[str, list[str]] = {variable: [] for variable in WEATHER_VARIABLES}
        for (turbine_id, _, _), response in zip(TURBINE_COORDINATES, response_objects, strict=True):
            hourly = response.get("hourly")
            if not isinstance(hourly, dict) or not isinstance(hourly.get("time"), list):
                raise _reason("weather_response_invalid", "missing hourly forecast")
            times = hourly["time"]
            parsed_times: list[pd.Timestamp] = []
            seen_times: set[pd.Timestamp] = set()
            for index, value in enumerate(times):
                try:
                    parsed = pd.Timestamp(value)
                    # API request explicitly fixes timezone=UTC; Open-Meteo emits
                    # ISO timestamps without an offset for this setting.
                    if parsed.tzinfo is None:
                        parsed = parsed.tz_localize("UTC")
                    else:
                        parsed = parsed.tz_convert("UTC")
                except (TypeError, ValueError) as exc:
                    raise _reason("weather_response_invalid", "invalid hourly timestamp") from exc
                if parsed in seen_times:
                    raise _reason("weather_response_invalid", "duplicate hourly timestamp")
                seen_times.add(parsed)
                parsed_times.append(parsed)
            index_by_time = {timestamp: index for index, timestamp in enumerate(parsed_times)}
            if not times or next(iter(index_by_time)) != run:
                raise _reason("weather_cache_run_mismatch", "response does not start at requested run")
            if any(timestamp not in index_by_time for timestamp in expected_times):
                raise _reason("weather_coverage_incomplete", f"{turbine_id} response does not cover horizon")

            units = response.get("hourly_units", {})
            if not isinstance(units, dict):
                raise _reason("weather_response_invalid", "invalid hourly_units")
            for variable in WEATHER_VARIABLES:
                values = hourly.get(variable)
                if not isinstance(values, list) or len(values) != len(times):
                    raise _reason("weather_variable_missing", variable)
                observed_units[variable].append(str(units.get(variable, "unknown")))
            for lead, valid_time in enumerate(expected_times):
                offset = index_by_time[valid_time]
                record: dict[str, Any] = {
                    "turbine_id": turbine_id,
                    "valid_time": valid_time,
                    "initialized_at": run,
                    # The API exposes initialization, not release/availability.
                    "issued_at": None,
                    "available_at": None,
                    "lead_hours": lead,
                    "weather_model": WEATHER_MODEL,
                }
                for variable in WEATHER_VARIABLES:
                    try:
                        value = hourly[variable][offset]
                        record[variable] = None if value is None else float(value)
                    except (TypeError, ValueError, OverflowError) as exc:
                        raise _reason("weather_response_invalid", f"invalid {variable}") from exc
                records.append(record)

        rows = pd.DataFrame.from_records(records, columns=list(WEATHER_COLUMNS))
        def same_unit(variable: str) -> str:
            values = set(observed_units[variable])
            return next(iter(values)) if len(values) == 1 else "mixed"

        speed_units = {
            same_unit("wind_speed_10m"),
            same_unit("wind_speed_100m"),
            same_unit("wind_gusts_10m"),
        }
        direction_unit = same_unit("wind_direction_10m")
        temperature_unit = same_unit("temperature_2m")
        pressure_unit = same_unit("surface_pressure")
        normalized_speed_unit = "ms" if speed_units.issubset({"m/s", "ms"}) else "invalid"
        normalized_direction_unit = "degrees" if direction_unit in ("°", "degrees") else direction_unit
        normalized_temperature_unit = "°C" if temperature_unit in ("°C", "Celsius") else temperature_unit
        normalized_pressure_unit = "hPa" if pressure_unit in ("hPa", "hectopascal") else pressure_unit
        provenance = {
            "provider": "open-meteo-single-runs",
            "source_url": OPEN_METEO_SINGLE_RUNS_URL,
            "weather_model": WEATHER_MODEL,
            "provenance_status": WeatherProvider._archive_status(run),
            "competition_valid": False,
            "initialized_at": run.isoformat().replace("+00:00", "Z"),
            "issued_at": None,
            "available_at": None,
            "model_lead_hours": [
                int((valid_time - run).total_seconds() // 3600) for valid_time in expected_times
            ],
            "run": parameters["run"],
            "retrieved_at": retrieved_at,
            "units": {
                "wind_speed": "m/s",
                "wind_speed_unit": normalized_speed_unit,
                "wind_speed_10m": same_unit("wind_speed_10m"),
                "wind_speed_100m": same_unit("wind_speed_100m"),
                "wind_gusts_10m": same_unit("wind_gusts_10m"),
                "wind_direction": normalized_direction_unit,
                "temperature": normalized_temperature_unit,
                "surface_pressure": normalized_pressure_unit,
            },
            "request_metadata": {
                "endpoint": OPEN_METEO_SINGLE_RUNS_URL,
                "parameters": parameters,
            },
        }
        fingerprint = weather_fingerprint(rows, provenance)
        return WeatherSnapshot(
            rows=rows,
            raw_responses=copy.deepcopy(raw_responses),
            fingerprint=fingerprint,
            provenance=provenance,
        )

    def _request_raw(
        self, parameters: dict[str, str | int]
    ) -> list[dict[str, Any]]:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self.transport.get(
                    OPEN_METEO_SINGLE_RUNS_URL,
                    params=parameters,
                    timeout=20,
                )
                status = int(getattr(response, "status_code", 200))
                if status == 429 or status >= 500:
                    raise _RetryableWeatherError(f"HTTP {status}")
                if status >= 400:
                    raise _reason("weather_http_error", f"HTTP {status}")
                try:
                    payload = response.json()
                except (ValueError, json.JSONDecodeError) as exc:
                    raise _reason("weather_response_invalid", "response is not JSON") from exc
                if isinstance(payload, dict):
                    return [payload]
                if isinstance(payload, list) and all(isinstance(item, dict) for item in payload):
                    return payload
                raise _reason("weather_response_invalid", "expected a JSON object or object list")
            except _RetryableWeatherError as exc:
                last_error = exc
            except requests.RequestException as exc:
                last_error = exc
            except ValueError:
                raise
            if attempt < 2:
                time.sleep(2**attempt)
        raise _reason(
            "weather_request_failed",
            f"maximum of three attempts exhausted ({type(last_error).__name__ if last_error else 'unknown error'})",
        ) from last_error

    def fetch(self, request: RunRequest, refresh: bool = False) -> WeatherSnapshot:
        """Load a valid cached snapshot or fetch and immutably cache a new one."""
        origin = _request_origin(request)
        run = self._run_for_origin(origin)
        parameters = self._parameters(run, request.horizon)
        cache_key = self._cache_key(parameters)
        cached_error: ValueError | None = None

        if not refresh:
            for path in self._cache_paths(cache_key):
                try:
                    raw, retrieved_at = self._read_cache(
                        path,
                        cache_key,
                        parameters,
                        origin,
                        run,
                    )
                    snapshot = self._make_snapshot(raw, request, run, retrieved_at, parameters)
                    validate_weather(snapshot, request)
                    return snapshot
                except (OSError, json.JSONDecodeError, ValueError) as exc:
                    cached_error = exc if isinstance(exc, ValueError) else _reason("weather_cache_invalid")
                    if str(cached_error).startswith("future_weather_"):
                        raise cached_error

        raw_responses = self._request_raw(parameters)
        retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        snapshot = self._make_snapshot(raw_responses, request, run, retrieved_at, parameters)
        raw_responses, retrieved_at = self._store_cache(
            cache_key,
            parameters,
            raw_responses,
            retrieved_at,
            snapshot.provenance,
        )
        try:
            validate_weather(snapshot, request)
        except ValueError:
            # A completed archival response is useful for a demo even if its
            # release provenance cannot support competition evidence.
            if request.mode == "demo":
                raise
            raise
        return snapshot

    def read_cached(self, request: RunRequest) -> WeatherSnapshot | None:
        """Return a valid cached snapshot without making a network request.

        Cache rows are rebuilt from their original responses and pass the same
        provenance and coverage checks as a fetched snapshot. Invalid entries
        are ignored so callers can safely offer a cache-only fallback.
        """
        origin = _request_origin(request)
        run = self._run_for_origin(origin)
        parameters = self._parameters(run, request.horizon)
        cache_key = self._cache_key(parameters)

        for path in self._cache_paths(cache_key):
            try:
                raw, retrieved_at = self._read_cache(
                    path,
                    cache_key,
                    parameters,
                    origin,
                    run,
                )
                snapshot = self._make_snapshot(raw, request, run, retrieved_at, parameters)
                validate_weather(snapshot, request)
                return snapshot
            except (OSError, json.JSONDecodeError, ValueError):
                continue
        return None


class _RetryableWeatherError(Exception):
    """Internal marker for 429/5xx responses eligible for a bounded retry."""


def make_synthetic_weather_snapshot(
    request: RunRequest,
    seed: int = 17,
) -> WeatherSnapshot:
    """Create a deterministic, explicitly synthetic offline demo snapshot.

    This helper accepts any valid zoned origin and 24/48-hour request. Its
    generated rows are convenient for UI/demo development, carry no fabricated
    issue or release time, and always fail competition provenance validation.
    """
    origin = _request_origin(request)
    rows: list[dict[str, Any]] = []
    seed_offset = (int(seed) % 19) / 100
    for turbine_index, (turbine_id, _, _) in enumerate(TURBINE_COORDINATES):
        for lead, valid_time in enumerate(pd.date_range(origin, periods=request.horizon, freq="h")):
            phase = 2 * math.pi * (valid_time.hour + lead / 24) / 24
            wind_speed = 6.4 + turbine_index * 0.35 + 1.7 * math.sin(phase) + seed_offset
            rows.append(
                {
                    "turbine_id": turbine_id,
                    "valid_time": valid_time,
                    "initialized_at": None,
                    "issued_at": None,
                    "available_at": None,
                    "lead_hours": lead,
                    "weather_model": "synthetic-demo",
                    "wind_speed_10m": wind_speed,
                    "wind_speed_100m": wind_speed * 1.28,
                    "wind_direction_10m": float((205 + lead * 7 + turbine_index * 11) % 360),
                    "wind_gusts_10m": wind_speed * 1.22,
                    "temperature_2m": -4.0 + 4.5 * math.sin(phase - 0.7),
                    "surface_pressure": 1012.0 + 5.0 * math.cos(phase / 2),
                }
            )
    frame = pd.DataFrame.from_records(rows, columns=list(WEATHER_COLUMNS))
    provenance = {
        "provider": "local-synthetic-generator",
        "source_url": None,
        "weather_model": "synthetic-demo",
        "provenance_status": "synthetic",
        "competition_valid": False,
        "initialized_at": None,
        "issued_at": None,
        "available_at": None,
        "seed": int(seed),
        "units": dict(_UNITS),
    }
    return WeatherSnapshot(
        rows=frame,
        raw_responses=[],
        fingerprint=weather_fingerprint(frame, provenance),
        provenance=provenance,
    )


def write_synthetic_weather_fixture(
    path: Path,
    request: RunRequest,
    seed: int = 17,
) -> Path:
    """Write a reproducible demo JSON fixture for any requested origin."""
    snapshot = make_synthetic_weather_snapshot(request, seed=seed)
    serializable_rows: list[dict[str, Any]] = []
    for record in snapshot.rows.to_dict(orient="records"):
        serializable_rows.append(
            {
                key: (_iso(value, key) if key in _TIME_COLUMNS else value)
                for key, value in record.items()
            }
        )
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "mode": "demo",
        "fingerprint": snapshot.fingerprint,
        "provenance": snapshot.provenance,
        "rows": serializable_rows,
    }
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return destination
