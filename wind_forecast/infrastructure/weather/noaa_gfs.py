"""Point-extracted, provenance-checked weather from NOAA's public GFS archive.

Only NOAA's public S3 bucket is contacted. Large global GRIB files are never
downloaded: this provider fetches their small indexes and individual bounded
GRIB message ranges, then retains only point values and audit evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd
import requests

from wind_forecast.contracts import RunRequest, WeatherSnapshot
from wind_forecast.weather import (
    TURBINE_COORDINATES,
    WEATHER_COLUMNS,
    WEATHER_VARIABLES,
    canonical_hash,
    validate_weather,
    weather_fingerprint,
)

NOAA_GFS_BUCKET_URL = "https://noaa-gfs-bdp-pds.s3.amazonaws.com/"
DATASET_REGISTRY_URL = "https://registry.opendata.aws/noaa-gfs-bdp-pds/"
WEATHER_MODEL = "noaa_gfs_0p25"
MAX_GRIB_MESSAGE_BYTES = 5 * 1024 * 1024
MAX_INDEX_BYTES = 2 * 1024 * 1024
MAX_LIST_BYTES = 2 * 1024 * 1024
MAX_CACHE_BYTES = 10 * 1024 * 1024
MAX_HTTP_ATTEMPTS = 3
HTTP_TIMEOUT = (5, 30)
_CACHE_SCHEMA_VERSION = 1
_POLICY_VERSION = "point-ranges-v1"
_CONTENT_RANGE_RE = re.compile(r"^bytes (\d+)-(\d+)/(\d+)$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class _FieldSpec:
    index_name: str
    index_level: str
    decoded_short_names: tuple[str, ...]
    type_of_level: str
    level: int
    grib_units: str
    output_variable: str


_FIELD_SPECS: tuple[_FieldSpec, ...] = (
    _FieldSpec(
        "UGRD", "10 m above ground", ("10u", "u"), "heightAboveGround", 10, "m s**-1", "u10"
    ),
    _FieldSpec(
        "VGRD", "10 m above ground", ("10v", "v"), "heightAboveGround", 10, "m s**-1", "v10"
    ),
    # In ecCodes GFS 100 m components decode as u/v with level=100, not 100u/100v.
    _FieldSpec("UGRD", "100 m above ground", ("u",), "heightAboveGround", 100, "m s**-1", "u100"),
    _FieldSpec("VGRD", "100 m above ground", ("v",), "heightAboveGround", 100, "m s**-1", "v100"),
    _FieldSpec(
        "TMP", "2 m above ground", ("2t", "t"), "heightAboveGround", 2, "K", "temperature_k"
    ),
    _FieldSpec(
        "PRES", "surface", ("sp", "pres"), "surface", 0, "Pa", "surface_pressure_pa"
    ),
    _FieldSpec("GUST", "surface", ("gust",), "surface", 0, "m s**-1", "gust"),
)


@dataclass(frozen=True)
class _ObjectMetadata:
    key: str
    etag: str
    size: int
    last_modified: datetime

    def evidence(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "etag": self.etag,
            "size": self.size,
            "last_modified": _iso(self.last_modified),
        }


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: Any, code: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(code)
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(code)
    return parsed.astimezone(timezone.utc)


def _etag(value: str) -> str:
    return value.strip().removeprefix("W/").strip().strip('"')


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _origin(request: RunRequest) -> datetime:
    try:
        value = pd.Timestamp(request.origin)
    except (TypeError, ValueError) as exc:
        raise ValueError("noaa_gfs_request_invalid") from exc
    if value.tzinfo is None or value.minute or value.second or value.microsecond:
        raise ValueError("noaa_gfs_request_invalid")
    if request.horizon not in (24, 48):
        raise ValueError("noaa_gfs_request_invalid")
    return value.tz_convert("UTC").to_pydatetime()


def _selected_run(origin: datetime) -> datetime:
    safe_time = origin - timedelta(hours=12)
    cycle_hour = (safe_time.hour // 6) * 6
    return safe_time.replace(hour=cycle_hour, minute=0, second=0, microsecond=0)


def _prefix(run: datetime) -> str:
    return f"gfs.{run:%Y%m%d}/{run:%H}/atmos/gfs.t{run:%H}z.pgrb2.0p25.f"


def _forecast_key(run: datetime, lead: int) -> str:
    return f"{_prefix(run)}{lead:03d}"


def _make_message(body: bytes) -> None:
    """Validate one complete GRIB1/2 message and its self-reported length."""
    if len(body) < 12 or body[:4] != b"GRIB" or body[-4:] != b"7777":
        raise ValueError("noaa_gfs_grib_message_invalid")
    if body[7] == 2 and len(body) >= 20:
        declared_length = int.from_bytes(body[8:16], "big")
    elif body[7] == 1:
        declared_length = int.from_bytes(body[4:7], "big")
    else:
        raise ValueError("noaa_gfs_grib_message_invalid")
    if declared_length != len(body) or len(body) > MAX_GRIB_MESSAGE_BYTES:
        raise ValueError("noaa_gfs_grib_message_invalid")


class _EcCodesDecoder:
    """Small adapter over ecCodes' public in-memory message API."""

    def decode(
        self, message: bytes, coordinates: tuple[tuple[str, float, float], ...]
    ) -> dict[str, Any]:
        try:
            import eccodes
        except ImportError as exc:  # pragma: no cover - exercised in deployments without ecCodes
            raise ValueError("noaa_gfs_eccodes_unavailable") from exc

        handle = None
        try:
            handle = eccodes.codes_new_from_message(message)
            get = eccodes.codes_get
            decoded: dict[str, Any] = {
                "short_name": str(get(handle, "shortName")),
                "type_of_level": str(get(handle, "typeOfLevel")),
                "level": float(get(handle, "level")),
                "units": str(get(handle, "units")),
                "data_date": int(get(handle, "dataDate")),
                "data_time": int(get(handle, "dataTime")),
                "validity_date": int(get(handle, "validityDate")),
                "validity_time": int(get(handle, "validityTime")),
                "forecast_time": int(get(handle, "forecastTime")),
                "step_units": get(handle, "stepUnits"),
                "points": [],
            }
            for _turbine_id, latitude, longitude in coordinates:
                nearest = eccodes.codes_grib_find_nearest(handle, latitude, longitude)
                if not nearest:
                    raise ValueError("noaa_gfs_grid_point_missing")
                point = nearest[0]
                if isinstance(point, dict):
                    point_lat, point_lon, point_value = point["lat"], point["lon"], point["value"]
                else:  # Compatibility with ecCodes versions returning a flat tuple.
                    point_lat, point_lon, point_value = point[0], point[1], point[2]
                decoded["points"].append(
                    {
                        "latitude": float(point_lat),
                        "longitude": float(point_lon),
                        "value": float(point_value),
                    }
                )
            return decoded
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("noaa_gfs_grib_decode_failed") from exc
        finally:
            if handle is not None:
                eccodes.codes_release(handle)


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

        objects = self._list_required_objects(run, origin, request.horizon)
        evidence = [item.evidence() for item in objects]
        if cached is not None and self._same_objects(cached.provenance.get("objects"), evidence):
            return cached

        snapshot = self._build_snapshot(request, run, objects)
        self._validate_snapshot(snapshot, request, run)
        self._store_cache(request, run, snapshot)
        return snapshot

    def _request_bytes(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        max_bytes: int,
        expected_etag: str | None = None,
        expected_range: tuple[int, int, int] | None = None,
        expected_size: int | None = None,
        error_code: str,
    ) -> bytes:
        last_error: Exception | None = None
        for attempt in range(MAX_HTTP_ATTEMPTS):
            response = None
            try:
                response = self.transport.get(
                    url,
                    params=params,
                    headers=headers or {},
                    timeout=HTTP_TIMEOUT,
                    stream=True,
                )
                status = int(getattr(response, "status_code", 200))
                if status in (429, 500, 502, 503, 504):
                    self._close(response)
                    if attempt + 1 < MAX_HTTP_ATTEMPTS:
                        time.sleep(0.05 * (attempt + 1))
                        continue
                    raise ValueError(error_code)
                if expected_range is not None and status != 206:
                    # In particular, reject HTTP 200 before reading a full GRIB file.
                    raise ValueError("noaa_gfs_range_response_invalid")
                if expected_range is None and status != 200:
                    raise ValueError(error_code)

                response_headers = getattr(response, "headers", {}) or {}
                if (
                    expected_etag is not None
                    and _etag(str(response_headers.get("ETag", ""))) != expected_etag
                ):
                    code = (
                        "noaa_gfs_range_response_invalid"
                        if expected_range is not None
                        else "noaa_gfs_object_version_mismatch"
                    )
                    raise ValueError(code)
                if expected_range is not None:
                    start, end, total = expected_range
                    match = _CONTENT_RANGE_RE.fullmatch(
                        str(response_headers.get("Content-Range", ""))
                    )
                    if (
                        match is None
                        or tuple(int(value) for value in match.groups()) != (start, end, total)
                    ):
                        raise ValueError("noaa_gfs_range_response_invalid")

                body = self._read_bounded(response, max_bytes)
                if (
                    expected_range is not None
                    and len(body) != expected_range[1] - expected_range[0] + 1
                ):
                    raise ValueError("noaa_gfs_range_response_invalid")
                if expected_size is not None and len(body) != expected_size:
                    raise ValueError(error_code)
                return body
            except requests.RequestException as exc:
                last_error = exc
                if attempt + 1 < MAX_HTTP_ATTEMPTS:
                    time.sleep(0.05 * (attempt + 1))
                    continue
                raise ValueError(error_code) from exc
            finally:
                if response is not None:
                    self._close(response)
        raise ValueError(error_code) from last_error

    @staticmethod
    def _close(response: Any) -> None:
        close = getattr(response, "close", None)
        if callable(close):
            close()

    @staticmethod
    def _read_bounded(response: Any, maximum: int) -> bytes:
        chunks: list[bytes] = []
        total = 0
        iterator = getattr(response, "iter_content", None)
        if callable(iterator):
            for chunk in iterator(chunk_size=64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > maximum:
                    raise ValueError("noaa_gfs_response_too_large")
                chunks.append(bytes(chunk))
        else:
            body = getattr(response, "content", b"")
            if not isinstance(body, bytes) or len(body) > maximum:
                raise ValueError("noaa_gfs_response_too_large")
            chunks.append(body)
        return b"".join(chunks)

    def _list_required_objects(
        self, run: datetime, origin: datetime, horizon: int
    ) -> list[_ObjectMetadata]:
        prefix = _prefix(run)
        continuation: str | None = None
        seen_tokens: set[str] = set()
        listed: dict[str, _ObjectMetadata] = {}
        for _page in range(20):
            params: dict[str, Any] = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
            if continuation is not None:
                params["continuation-token"] = continuation
            body = self._request_bytes(
                NOAA_GFS_BUCKET_URL,
                params=params,
                max_bytes=MAX_LIST_BYTES,
                error_code="noaa_gfs_listing_invalid",
            )
            try:
                root = ET.fromstring(body)
            except ET.ParseError as exc:
                raise ValueError("noaa_gfs_listing_invalid") from exc
            contents = [node for node in root.iter() if _local_name(node.tag) == "Contents"]
            for content in contents:
                values: dict[str, str] = {}
                for child in content:
                    values[_local_name(child.tag)] = (child.text or "").strip()
                key = values.get("Key", "")
                try:
                    obj = _ObjectMetadata(
                        key=key,
                        etag=_etag(values["ETag"]),
                        size=int(values["Size"]),
                        last_modified=_parse_utc(
                            values["LastModified"], "noaa_gfs_listing_invalid"
                        ),
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError("noaa_gfs_listing_invalid") from exc
                if (
                    not key.startswith(prefix)
                    or not obj.etag
                    or obj.size <= 0
                    or key in listed
                ):
                    raise ValueError("noaa_gfs_listing_invalid")
                listed[key] = obj

            truncated_values = [
                (node.text or "").strip().lower()
                for node in root.iter()
                if _local_name(node.tag) == "IsTruncated"
            ]
            if truncated_values != ["true"]:
                if truncated_values != ["false"]:
                    raise ValueError("noaa_gfs_listing_invalid")
                break
            tokens = [
                (node.text or "").strip()
                for node in root.iter()
                if _local_name(node.tag) == "NextContinuationToken"
            ]
            if len(tokens) != 1 or not tokens[0] or tokens[0] in seen_tokens:
                raise ValueError("noaa_gfs_listing_invalid")
            continuation = tokens[0]
            seen_tokens.add(continuation)
        else:
            raise ValueError("noaa_gfs_listing_invalid")

        expected: list[_ObjectMetadata] = []
        first_lead = int((origin - run).total_seconds() // 3600)
        for lead in range(first_lead, first_lead + horizon):
            grib_key = _forecast_key(run, lead)
            for key in (grib_key, f"{grib_key}.idx"):
                metadata = listed.get(key)
                if metadata is None:
                    raise ValueError("noaa_gfs_object_missing")
                if metadata.last_modified < run or metadata.last_modified > origin:
                    raise ValueError("noaa_gfs_object_time_invalid")
                expected.append(metadata)
        return expected

    @staticmethod
    def _same_objects(cached: Any, current: list[dict[str, Any]]) -> bool:
        if not isinstance(cached, list) or len(cached) != len(current):
            return False
        return sorted(cached, key=lambda item: item.get("key", "")) == sorted(
            current, key=lambda item: item.get("key", "")
        )

    def _read_index(
        self, metadata: _ObjectMetadata, grib_object_size: int
    ) -> tuple[bytes, list[dict[str, Any]]]:
        url = NOAA_GFS_BUCKET_URL + quote(metadata.key, safe="/")
        body = self._request_bytes(
            url,
            headers={"If-Match": f'"{metadata.etag}"'},
            max_bytes=MAX_INDEX_BYTES,
            expected_etag=metadata.etag,
            expected_size=metadata.size,
            error_code="noaa_gfs_index_response_invalid",
        )
        return body, self._parse_index(body, grib_object_size)

    @staticmethod
    def _parse_index(body: bytes, object_size: int) -> list[dict[str, Any]]:
        try:
            text = body.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError("noaa_gfs_index_invalid") from exc
        lines = text.splitlines()
        if not lines or len(lines) > 10000:
            raise ValueError("noaa_gfs_index_invalid")
        records: list[dict[str, Any]] = []
        previous_number = 0
        previous_offset = -1
        for line in lines:
            if not line or len(line) > 4096:
                raise ValueError("noaa_gfs_index_invalid")
            parts = line.split(":", 5)
            if len(parts) != 6:
                raise ValueError("noaa_gfs_index_invalid")
            try:
                number, offset = int(parts[0]), int(parts[1])
            except ValueError as exc:
                raise ValueError("noaa_gfs_index_invalid") from exc
            if (
                number <= previous_number
                or offset <= previous_offset
                or offset < 0
                or offset >= object_size
                or not parts[2].startswith("d=")
                or not parts[3]
                or not parts[4]
            ):
                raise ValueError("noaa_gfs_index_offset_invalid")
            records.append(
                {
                    "number": number,
                    "offset": offset,
                    "reference_time": parts[2][2:],
                    "name": parts[3],
                    "level": parts[4],
                    "description": parts[5],
                }
            )
            previous_number, previous_offset = number, offset
        if records[0]["offset"] != 0:
            raise ValueError("noaa_gfs_index_offset_invalid")
        return records

    @staticmethod
    def _select_index_records(
        records: list[dict[str, Any]], run: datetime, object_size: int
    ) -> dict[tuple[str, str], tuple[int, int, dict[str, Any]]]:
        selected: dict[tuple[str, str], dict[str, Any]] = {}
        for record in records:
            if record["reference_time"] != run.strftime("%Y%m%d%H"):
                raise ValueError("noaa_gfs_index_run_mismatch")
            pair = (record["name"], record["level"])
            if pair in {(field.index_name, field.index_level) for field in _FIELD_SPECS}:
                if pair in selected:
                    raise ValueError("noaa_gfs_index_duplicate_field")
                selected[pair] = record
        wanted = {(field.index_name, field.index_level) for field in _FIELD_SPECS}
        if set(selected) != wanted:
            raise ValueError("noaa_gfs_index_field_missing")
        offsets = [record["offset"] for record in records]
        boundaries = {
            offset: offsets[index + 1] if index + 1 < len(offsets) else object_size
            for index, offset in enumerate(offsets)
        }
        result: dict[tuple[str, str], tuple[int, int, dict[str, Any]]] = {}
        for pair, record in selected.items():
            start = record["offset"]
            end = boundaries[start]
            if end <= start or end - start > MAX_GRIB_MESSAGE_BYTES:
                raise ValueError("noaa_gfs_index_offset_invalid")
            result[pair] = (start, end - 1, record)
        return result

    def _read_range(
        self,
        metadata: _ObjectMetadata,
        start: int,
        end: int,
    ) -> bytes:
        url = NOAA_GFS_BUCKET_URL + quote(metadata.key, safe="/")
        body = self._request_bytes(
            url,
            headers={
                "Range": f"bytes={start}-{end}",
                "If-Match": f'"{metadata.etag}"',
            },
            max_bytes=MAX_GRIB_MESSAGE_BYTES,
            expected_etag=metadata.etag,
            expected_range=(start, end, metadata.size),
            error_code="noaa_gfs_range_response_invalid",
        )
        _make_message(body)
        return body

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
        objects: list[_ObjectMetadata],
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
            index_bytes, index_records = self._read_index(index_metadata, grib_metadata.size)
            index_sha256 = hashlib.sha256(index_bytes).hexdigest()
            indexed = self._select_index_records(index_records, run, grib_metadata.size)
            by_output: dict[str, dict[str, Any]] = {}
            for field in _FIELD_SPECS:
                start, end, index_record = indexed[(field.index_name, field.index_level)]
                message = self._read_range(grib_metadata, start, end)
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
