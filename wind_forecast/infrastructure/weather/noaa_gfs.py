"""NOAA GFS adapter for the project's point-in-time weather contract."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from wind_forecast.contracts import RunRequest, WeatherSnapshot
from wind_forecast.weather import (
    TURBINE_COORDINATES,
    WEATHER_COLUMNS,
    canonical_hash,
    validate_weather,
    weather_fingerprint,
)

from .noaa_gfs_archive import NoaaGfsArchive, ObjectMetadata
from .noaa_gfs_codec import EcCodesDecoder, validate_grib_message
from .noaa_gfs_common import (
    CACHE_SCHEMA_VERSION as _CACHE_SCHEMA_VERSION,
    DATASET_REGISTRY_URL,
    FIELD_SPECS as _FIELD_SPECS,
    FieldSpec as _FieldSpec,
    MAX_CACHE_BYTES,
    MAX_GRIB_MESSAGE_BYTES,
    NOAA_GFS_BUCKET_URL,
    POLICY_VERSION as _POLICY_VERSION,
    WEATHER_MODEL,
    forecast_key as _forecast_key,
    iso as _iso,
    parse_utc as _parse_utc,
    request_origin as _origin,
    selected_run as _selected_run,
)

# Kept private aliases for focused tests of the default decoder and GRIB envelope.
_EcCodesDecoder = EcCodesDecoder
_make_message = validate_grib_message
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _same_objects(cached: Any, current: list[dict[str, Any]]) -> bool:
    if not isinstance(cached, list) or len(cached) != len(current):
        return False
    return sorted(cached, key=lambda item: item.get("key", "")) == sorted(
        current, key=lambda item: item.get("key", "")
    )


class NoaaGfsWeatherProvider:
    """Fetch exact GFS forecast messages for the existing two-site contract.

    The S3 ``LastModified`` values are the public archive publication bound
    used for both ``issued_at`` and ``available_at``. They are not represented
    as the exact time NCEP released a model cycle.
    """

    source_identity = "noaa-gfs-0p25-v1"

    def __init__(
        self,
        cache_dir: Path,
        transport: Any = None,
        decoder: Any = None,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.transport = transport if transport is not None else requests.Session()
        self.decoder = decoder if decoder is not None else _EcCodesDecoder()
        self.archive = NoaaGfsArchive(self.transport)

    @staticmethod
    def _cache_identity(request: RunRequest, run: datetime) -> dict[str, Any]:
        return {
            "source_identity": NoaaGfsWeatherProvider.source_identity,
            "policy_version": _POLICY_VERSION,
            "origin": _iso(_origin(request)),
            "horizon": request.horizon,
            "mode": request.mode,
            "run": _iso(run),
            "model": WEATHER_MODEL,
            "coordinates": [list(item) for item in TURBINE_COORDINATES],
            "fields": [
                {
                    "index_name": field.index_name,
                    "index_level": field.index_level,
                    "output": field.output_variable,
                }
                for field in _FIELD_SPECS
            ],
        }

    def _cache_paths(self, cache_key: str) -> list[Path]:
        folder = self.cache_dir / "noaa-gfs"
        if not folder.exists():
            return []
        found: list[tuple[str, Path]] = []
        for path in folder.glob(f"{cache_key}.*.json"):
            try:
                if path.stat().st_size > MAX_CACHE_BYTES:
                    continue
                payload = json.loads(path.read_text(encoding="utf-8"))
                retrieved_at = str(payload.get("retrieved_at", ""))
                found.append((retrieved_at, path))
            except (OSError, json.JSONDecodeError, AttributeError):
                continue
        return [path for _, path in sorted(found, reverse=True)]

    def read_cached(self, request: RunRequest) -> WeatherSnapshot | None:
        """Return a validated point extract without making any network call."""
        try:
            run = _selected_run(_origin(request))
            identity = self._cache_identity(request, run)
            cache_key = canonical_hash(identity)
        except (TypeError, ValueError):
            return None
        for path in self._cache_paths(cache_key):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                checksum = payload.pop("cache_payload_sha256")
                if checksum != canonical_hash(payload):
                    continue
                if (
                    payload.get("schema_version") != _CACHE_SCHEMA_VERSION
                    or payload.get("cache_key") != cache_key
                    or payload.get("cache_identity") != identity
                    or not isinstance(payload.get("rows"), list)
                    or not isinstance(payload.get("raw_responses"), list)
                    or not isinstance(payload.get("provenance"), dict)
                ):
                    continue
                _parse_utc(payload.get("retrieved_at"), "noaa_gfs_cache_timestamp_invalid")
                rows = pd.DataFrame.from_records(payload["rows"], columns=list(WEATHER_COLUMNS))
                for column in ("valid_time", "initialized_at", "issued_at", "available_at"):
                    rows[column] = pd.to_datetime(rows[column], utc=True, errors="raise")
                fingerprint = weather_fingerprint(rows, payload["provenance"])
                if payload.get("fingerprint") != fingerprint:
                    continue
                snapshot = WeatherSnapshot(
                    rows=rows,
                    raw_responses=payload["raw_responses"],
                    fingerprint=fingerprint,
                    provenance=payload["provenance"],
                )
                self._validate_snapshot(snapshot, request, run)
                return snapshot
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError, OverflowError):
                continue
        return None

    def fetch(self, request: RunRequest, refresh: bool = False) -> WeatherSnapshot:
        """Fetch a point extract, reusing only cache versions already proven."""
        origin = _origin(request)
        run = _selected_run(origin)
        cached = self.read_cached(request)
        if cached is not None and not refresh:
            return cached

        objects = self.archive.list_required_objects(run, origin, request.horizon)
        evidence = [item.evidence() for item in objects]
        if cached is not None and _same_objects(cached.provenance.get("objects"), evidence):
            return cached

        snapshot = self._build_snapshot(request, run, objects)
        self._validate_snapshot(snapshot, request, run)
        self._store_cache(request, run, snapshot)
        return snapshot

    def _decode_field(
        self,
        message: bytes,
        field: _FieldSpec,
        run: datetime,
        valid_time: datetime,
        lead: int,
    ) -> dict[str, Any]:
        try:
            decoded = self.decoder.decode(message, TURBINE_COORDINATES)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("noaa_gfs_grib_decode_failed") from exc
        if not isinstance(decoded, dict):
            raise ValueError("noaa_gfs_grib_decode_failed")
        expected_run = (int(run.strftime("%Y%m%d")), int(run.strftime("%H%M")))
        expected_valid = (int(valid_time.strftime("%Y%m%d")), int(valid_time.strftime("%H%M")))
        try:
            actual_run = (int(decoded["data_date"]), int(decoded["data_time"]))
            actual_valid = (int(decoded["validity_date"]), int(decoded["validity_time"]))
            actual_step = int(decoded["forecast_time"])
            level = float(decoded["level"])
            points = decoded["points"]
            step_units = decoded.get("step_units", 1)
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("noaa_gfs_grib_metadata_invalid") from exc
        if actual_run != expected_run:
            raise ValueError("noaa_gfs_grib_run_mismatch")
        if step_units not in (1, "1", "h", "hour", "hours") or actual_step != lead:
            raise ValueError("noaa_gfs_grib_step_mismatch")
        if actual_valid != expected_valid:
            raise ValueError("noaa_gfs_grib_valid_time_mismatch")
        if (
            decoded.get("short_name") not in field.decoded_short_names
            or decoded.get("type_of_level") != field.type_of_level
            or not math.isfinite(level)
            or level != field.level
        ):
            raise ValueError("noaa_gfs_grib_field_mismatch")
        if decoded.get("units") != field.grib_units:
            raise ValueError("noaa_gfs_grib_units_mismatch")
        if not isinstance(points, (list, tuple)) or len(points) != len(TURBINE_COORDINATES):
            raise ValueError("noaa_gfs_grid_point_missing")

        normalized_points: list[dict[str, float]] = []
        for point in points:
            if not isinstance(point, dict):
                raise ValueError("noaa_gfs_grid_point_missing")
            try:
                latitude = float(point["latitude"])
                longitude = float(point["longitude"])
                value = float(point["value"])
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise ValueError("noaa_gfs_grid_point_missing") from exc
            if (
                not all(math.isfinite(item) for item in (latitude, longitude, value))
                or not -90 <= latitude <= 90
                or not -180 <= longitude <= 180
            ):
                raise ValueError("noaa_gfs_grid_point_invalid")
            normalized_points.append(
                {"latitude": latitude, "longitude": longitude, "value": value}
            )
        return {
            "metadata": {
                "short_name": decoded["short_name"],
                "type_of_level": decoded["type_of_level"],
                "level": level,
                "units": decoded["units"],
                "data_date": actual_run[0],
                "data_time": actual_run[1],
                "validity_date": actual_valid[0],
                "validity_time": actual_valid[1],
                "forecast_time": actual_step,
                "step_units": step_units,
            },
            "points": normalized_points,
        }

    def _build_snapshot(
        self,
        request: RunRequest,
        run: datetime,
        objects: list[ObjectMetadata],
    ) -> WeatherSnapshot:
        origin = _origin(request)
        object_by_key = {item.key: item for item in objects}
        records: list[dict[str, Any]] = []
        field_evidence: list[dict[str, Any]] = []
        first_lead = int((origin - run).total_seconds() // 3600)
        for lead in range(first_lead, first_lead + request.horizon):
            valid_time = run + timedelta(hours=lead)
            grib_key = _forecast_key(run, lead)
            grib_metadata = object_by_key[grib_key]
            index_metadata = object_by_key[f"{grib_key}.idx"]
            index_bytes, index_records = self.archive.read_index(index_metadata, grib_metadata.size)
            index_sha256 = hashlib.sha256(index_bytes).hexdigest()
            indexed = self.archive.select_index_records(index_records, run, grib_metadata.size)
            by_output: dict[str, dict[str, Any]] = {}
            for field in _FIELD_SPECS:
                start, end, index_record = indexed[(field.index_name, field.index_level)]
                message = self.archive.read_range(grib_metadata, start, end)
                decoded = self._decode_field(message, field, run, valid_time, lead)
                by_output[field.output_variable] = decoded
                field_evidence.append(
                    {
                        "key": grib_key,
                        "lead_hours": lead,
                        "index_record": index_record["number"],
                        "index_offset": start,
                        "byte_end": end,
                        "field": field.index_name,
                        "level": field.index_level,
                        "index_sha256": index_sha256,
                        "sha256": hashlib.sha256(message).hexdigest(),
                        "decoded": decoded["metadata"],
                        "grid_points": decoded["points"],
                    }
                )
                del message

            for turbine_index, (turbine_id, _latitude, _longitude) in enumerate(
                TURBINE_COORDINATES
            ):
                u10 = by_output["u10"]["points"][turbine_index]["value"]
                v10 = by_output["v10"]["points"][turbine_index]["value"]
                u100 = by_output["u100"]["points"][turbine_index]["value"]
                v100 = by_output["v100"]["points"][turbine_index]["value"]
                temperature_c = (
                    by_output["temperature_k"]["points"][turbine_index]["value"] - 273.15
                )
                pressure_hpa = (
                    by_output["surface_pressure_pa"]["points"][turbine_index]["value"]
                    / 100.0
                )
                gust = by_output["gust"]["points"][turbine_index]["value"]
                row = {
                    "turbine_id": turbine_id,
                    "valid_time": pd.Timestamp(valid_time),
                    "initialized_at": pd.Timestamp(run),
                    "issued_at": pd.Timestamp(max(item.last_modified for item in objects)),
                    "available_at": pd.Timestamp(max(item.last_modified for item in objects)),
                    "lead_hours": lead - first_lead,
                    "weather_model": WEATHER_MODEL,
                    "wind_speed_10m": math.hypot(u10, v10),
                    "wind_speed_100m": math.hypot(u100, v100),
                    "wind_direction_10m": (
                        math.degrees(math.atan2(-u10, -v10)) + 360.0
                    )
                    % 360.0,
                    "wind_gusts_10m": gust,
                    "temperature_2m": temperature_c,
                    "surface_pressure": pressure_hpa,
                }
                records.append(row)

        rows = pd.DataFrame.from_records(records, columns=list(WEATHER_COLUMNS))
        availability = max(item.last_modified for item in objects)
        retrieved_at = _iso(datetime.now(timezone.utc))
        provenance = {
            "provider": "noaa-gfs-public-s3-range",
            "source_identity": self.source_identity,
            "source_url": NOAA_GFS_BUCKET_URL,
            "dataset_registry_url": DATASET_REGISTRY_URL,
            "weather_model": WEATHER_MODEL,
            "provenance_status": "verified",
            "competition_valid": True,
            "initialized_at": _iso(run),
            "issued_at": _iso(availability),
            "available_at": _iso(availability),
            "issuance_semantics": "public_archive_object_publication_bound",
            "issuance_semantics_note": (
                "issued_at is the conservative maximum S3 LastModified over required GRIB and "
                "index objects; it is not the exact NCEP model release instant."
            ),
            "availability_evidence": "S3 LastModified for every required forecast object and index",
            "run": _iso(run),
            "model_lead_hours": list(range(first_lead, first_lead + request.horizon)),
            "horizon_hours": request.horizon,
            "origin": _iso(origin),
            "retrieved_at": retrieved_at,
            "coordinate_sampling": "nearest_grid_point",
            "turbine_coordinates": [
                {"turbine_id": name, "latitude": lat, "longitude": lon}
                for name, lat, lon in TURBINE_COORDINATES
            ],
            "units": {
                "wind_speed": "m/s",
                "wind_speed_unit": "ms",
                "wind_direction": "degrees",
                "temperature": "°C",
                "surface_pressure": "hPa",
                "wind_speed_10m_grib": "m s**-1",
                "wind_speed_100m_grib": "m s**-1",
                "temperature_2m_grib": "K",
                "surface_pressure_grib": "Pa",
                "wind_gusts_10m_grib": "m s**-1",
            },
            "objects": [item.evidence() for item in objects],
            "field_ranges": field_evidence,
            "policy_version": _POLICY_VERSION,
        }
        raw_responses = [
            {
                "objects": provenance["objects"],
                "field_ranges": field_evidence,
                "archive_publication_bound": _iso(availability),
            }
        ]
        fingerprint = weather_fingerprint(rows, provenance)
        return WeatherSnapshot(rows, raw_responses, fingerprint, provenance)

    def _validate_snapshot(
        self, snapshot: WeatherSnapshot, request: RunRequest, run: datetime
    ) -> None:
        provenance = snapshot.provenance
        if (
            provenance.get("source_identity") != self.source_identity
            or provenance.get("source_url") != NOAA_GFS_BUCKET_URL
            or provenance.get("dataset_registry_url") != DATASET_REGISTRY_URL
            or provenance.get("weather_model") != WEATHER_MODEL
            or provenance.get("origin") != _iso(_origin(request))
            or provenance.get("horizon_hours") != request.horizon
            or provenance.get("policy_version") != _POLICY_VERSION
            or provenance.get("coordinate_sampling") != "nearest_grid_point"
            or provenance.get("issuance_semantics") != "public_archive_object_publication_bound"
            or provenance.get("provenance_status") != "verified"
            or provenance.get("competition_valid") is not True
        ):
            raise ValueError("noaa_gfs_provenance_invalid")
        if provenance.get("initialized_at") != _iso(run):
            raise ValueError("noaa_gfs_grib_run_mismatch")
        objects = provenance.get("objects")
        ranges = provenance.get("field_ranges")
        if not isinstance(objects, list) or len(objects) != request.horizon * 2:
            raise ValueError("noaa_gfs_provenance_invalid")
        if not isinstance(ranges, list) or len(ranges) != request.horizon * len(_FIELD_SPECS):
            raise ValueError("noaa_gfs_provenance_invalid")

        origin = _origin(request)
        first_lead = int((origin - run).total_seconds() // 3600)
        expected_object_keys = {
            key
            for lead in range(first_lead, first_lead + request.horizon)
            for key in (_forecast_key(run, lead), f"{_forecast_key(run, lead)}.idx")
        }
        observed_object_keys: set[str] = set()
        object_times: list[datetime] = []
        for item in objects:
            if not isinstance(item, dict):
                raise ValueError("noaa_gfs_provenance_invalid")
            key, etag, size = item.get("key"), item.get("etag"), item.get("size")
            if (
                not isinstance(key, str)
                or not isinstance(etag, str)
                or not etag
                or not isinstance(size, int)
                or isinstance(size, bool)
                or size <= 0
                or key in observed_object_keys
            ):
                raise ValueError("noaa_gfs_provenance_invalid")
            last_modified = _parse_utc(item.get("last_modified"), "noaa_gfs_provenance_invalid")
            if last_modified < run or last_modified > origin:
                raise ValueError("noaa_gfs_object_time_invalid")
            observed_object_keys.add(key)
            object_times.append(last_modified)
        if observed_object_keys != expected_object_keys:
            raise ValueError("noaa_gfs_provenance_invalid")
        expected_model_leads = list(range(first_lead, first_lead + request.horizon))
        if provenance.get("model_lead_hours") != expected_model_leads:
            raise ValueError("noaa_gfs_provenance_invalid")
        object_by_key = {item["key"]: item for item in objects}
        availability = max(object_times)
        if (
            _parse_utc(provenance.get("issued_at"), "noaa_gfs_provenance_invalid")
            != availability
            or _parse_utc(
                provenance.get("available_at"), "noaa_gfs_provenance_invalid"
            )
            != availability
        ):
            raise ValueError("noaa_gfs_provenance_invalid")

        spec_by_index = {(field.index_name, field.index_level): field for field in _FIELD_SPECS}
        observed_ranges: dict[tuple[int, str, str], dict[str, Any]] = {}
        for item in ranges:
            if not isinstance(item, dict):
                raise ValueError("noaa_gfs_provenance_invalid")
            try:
                lead = int(item["lead_hours"])
                name, level_text = str(item["field"]), str(item["level"])
                offset, byte_end = int(item["index_offset"]), int(item["byte_end"])
                spec = spec_by_index[(name, level_text)]
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise ValueError("noaa_gfs_provenance_invalid") from exc
            range_key = (lead, name, level_text)
            if (
                range_key in observed_ranges
                or not first_lead <= lead < first_lead + request.horizon
                or item.get("key") != _forecast_key(run, lead)
                or offset < 0
                or byte_end < offset
                or byte_end >= object_by_key[item["key"]]["size"]
                or byte_end - offset + 1 > MAX_GRIB_MESSAGE_BYTES
                or not isinstance(item.get("index_record"), int)
                or item["index_record"] <= 0
                or not _SHA256_RE.fullmatch(str(item.get("sha256", "")))
                or not _SHA256_RE.fullmatch(str(item.get("index_sha256", "")))
            ):
                raise ValueError("noaa_gfs_provenance_invalid")
            decoded = item.get("decoded")
            points = item.get("grid_points")
            if not isinstance(decoded, dict) or not isinstance(points, list) or len(points) != 2:
                raise ValueError("noaa_gfs_provenance_invalid")
            valid_time = run + timedelta(hours=lead)
            try:
                decoded_matches = (
                    decoded.get("short_name") in spec.decoded_short_names
                    and decoded.get("type_of_level") == spec.type_of_level
                    and float(decoded.get("level")) == spec.level
                    and decoded.get("units") == spec.grib_units
                    and int(decoded.get("data_date")) == int(run.strftime("%Y%m%d"))
                    and int(decoded.get("data_time")) == int(run.strftime("%H%M"))
                    and int(decoded.get("validity_date")) == int(valid_time.strftime("%Y%m%d"))
                    and int(decoded.get("validity_time")) == int(valid_time.strftime("%H%M"))
                    and int(decoded.get("forecast_time")) == lead
                    and decoded.get("step_units") in (1, "1", "h", "hour", "hours")
                )
            except (TypeError, ValueError, OverflowError):
                decoded_matches = False
            if not decoded_matches:
                raise ValueError("noaa_gfs_provenance_invalid")
            for point in points:
                if not isinstance(point, dict):
                    raise ValueError("noaa_gfs_provenance_invalid")
                try:
                    lat = float(point["latitude"])
                    lon = float(point["longitude"])
                    value = float(point["value"])
                except (KeyError, TypeError, ValueError, OverflowError) as exc:
                    raise ValueError("noaa_gfs_provenance_invalid") from exc
                if not (
                    all(math.isfinite(number) for number in (lat, lon, value))
                    and -90 <= lat <= 90
                    and -180 <= lon <= 180
                ):
                    raise ValueError("noaa_gfs_provenance_invalid")
            observed_ranges[range_key] = item
        expected_ranges = {
            (lead, field.index_name, field.index_level)
            for lead in range(first_lead, first_lead + request.horizon)
            for field in _FIELD_SPECS
        }
        if set(observed_ranges) != expected_ranges:
            raise ValueError("noaa_gfs_provenance_invalid")

        point_values = {
            (lead, name, level): [float(point["value"]) for point in item["grid_points"]]
            for (lead, name, level), item in observed_ranges.items()
        }
        for row in snapshot.rows.to_dict(orient="records"):
            offset = int(row["lead_hours"])
            model_lead = first_lead + offset
            turbine_index = 0 if row["turbine_id"] == "turbine_1" else 1
            u10 = point_values[(model_lead, "UGRD", "10 m above ground")][turbine_index]
            v10 = point_values[(model_lead, "VGRD", "10 m above ground")][turbine_index]
            u100 = point_values[(model_lead, "UGRD", "100 m above ground")][turbine_index]
            v100 = point_values[(model_lead, "VGRD", "100 m above ground")][turbine_index]
            expected_values = {
                "wind_speed_10m": math.hypot(u10, v10),
                "wind_direction_10m": (
                    math.degrees(math.atan2(-u10, -v10)) + 360.0
                )
                % 360.0,
                "wind_speed_100m": math.hypot(u100, v100),
                "wind_gusts_10m": point_values[(model_lead, "GUST", "surface")][turbine_index],
                "temperature_2m": (
                    point_values[(model_lead, "TMP", "2 m above ground")][turbine_index]
                    - 273.15
                ),
                "surface_pressure": (
                    point_values[(model_lead, "PRES", "surface")][turbine_index] / 100.0
                ),
            }
            for column, expected_value in expected_values.items():
                if not math.isclose(
                    float(row[column]), expected_value, rel_tol=1e-10, abs_tol=1e-10
                ):
                    raise ValueError("noaa_gfs_cache_extract_mismatch")

        expected_fp = weather_fingerprint(snapshot.rows, provenance)
        if snapshot.fingerprint != expected_fp:
            raise ValueError("noaa_gfs_fingerprint_invalid")
        validate_weather(snapshot, request)

    def _store_cache(
        self, request: RunRequest, run: datetime, snapshot: WeatherSnapshot
    ) -> None:
        identity = self._cache_identity(request, run)
        cache_key = canonical_hash(identity)
        rows = snapshot.rows.copy()
        for column in ("valid_time", "initialized_at", "issued_at", "available_at"):
            rows[column] = rows[column].map(lambda value: _iso(pd.Timestamp(value).to_pydatetime()))
        body: dict[str, Any] = {
            "schema_version": _CACHE_SCHEMA_VERSION,
            "cache_key": cache_key,
            "cache_identity": identity,
            "retrieved_at": snapshot.provenance["retrieved_at"],
            "rows": rows.to_dict(orient="records"),
            "raw_responses": snapshot.raw_responses,
            "fingerprint": snapshot.fingerprint,
            "provenance": snapshot.provenance,
        }
        body["cache_payload_sha256"] = canonical_hash(body)
        folder = self.cache_dir / "noaa-gfs"
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / f"{cache_key}.{snapshot.fingerprint}.json"
        if destination.exists():
            existing = self.read_cached(request)
            if existing is not None and existing.fingerprint == snapshot.fingerprint:
                return
        fd, temporary_name = tempfile.mkstemp(prefix=".gfs-", suffix=".tmp", dir=folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(
                    body,
                    stream,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                stream.flush()
                os.fsync(stream.fileno())
            if destination.exists():
                os.replace(temporary_name, destination)
            else:
                try:
                    os.link(temporary_name, destination)
                except FileExistsError:
                    pass
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
