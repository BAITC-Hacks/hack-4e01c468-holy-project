"""Stable constants and value helpers shared by the NOAA GFS adapter modules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from wind_forecast.contracts import RunRequest

NOAA_GFS_BUCKET_URL = "https://noaa-gfs-bdp-pds.s3.amazonaws.com/"
DATASET_REGISTRY_URL = "https://registry.opendata.aws/noaa-gfs-bdp-pds/"
WEATHER_MODEL = "noaa_gfs_0p25"
MAX_GRIB_MESSAGE_BYTES = 5 * 1024 * 1024
MAX_INDEX_BYTES = 2 * 1024 * 1024
MAX_LIST_BYTES = 2 * 1024 * 1024
MAX_CACHE_BYTES = 10 * 1024 * 1024
MAX_HTTP_ATTEMPTS = 3
HTTP_TIMEOUT = (5, 30)
CACHE_SCHEMA_VERSION = 1
POLICY_VERSION = "point-ranges-v1"


@dataclass(frozen=True)
class FieldSpec:
    """One exact NOAA index/GRIB field and its normalized weather name."""

    index_name: str
    index_level: str
    decoded_short_names: tuple[str, ...]
    type_of_level: str
    level: int
    grib_units: str
    output_variable: str


FIELD_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec(
        "UGRD", "10 m above ground", ("10u", "u"), "heightAboveGround", 10, "m s**-1", "u10"
    ),
    FieldSpec(
        "VGRD", "10 m above ground", ("10v", "v"), "heightAboveGround", 10, "m s**-1", "v10"
    ),
    # In ecCodes GFS 100 m components decode as u/v with level=100, not 100u/100v.
    FieldSpec(
        "UGRD", "100 m above ground", ("u",), "heightAboveGround", 100, "m s**-1", "u100"
    ),
    FieldSpec(
        "VGRD", "100 m above ground", ("v",), "heightAboveGround", 100, "m s**-1", "v100"
    ),
    FieldSpec(
        "TMP", "2 m above ground", ("2t", "t"), "heightAboveGround", 2, "K", "temperature_k"
    ),
    FieldSpec("PRES", "surface", ("sp", "pres"), "surface", 0, "Pa", "surface_pressure_pa"),
    FieldSpec("GUST", "surface", ("gust",), "surface", 0, "m s**-1", "gust"),
)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_utc(value: Any, code: str) -> datetime:
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


def normalize_etag(value: str) -> str:
    return value.strip().removeprefix("W/").strip().strip('"')


def local_xml_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def request_origin(request: RunRequest) -> datetime:
    try:
        value = pd.Timestamp(request.origin)
    except (TypeError, ValueError) as exc:
        raise ValueError("noaa_gfs_request_invalid") from exc
    if value.tzinfo is None or value.minute or value.second or value.microsecond:
        raise ValueError("noaa_gfs_request_invalid")
    if request.horizon not in (24, 48):
        raise ValueError("noaa_gfs_request_invalid")
    return value.tz_convert("UTC").to_pydatetime()


def selected_run(origin: datetime) -> datetime:
    safe_time = origin - timedelta(hours=12)
    cycle_hour = (safe_time.hour // 6) * 6
    return safe_time.replace(hour=cycle_hour, minute=0, second=0, microsecond=0)


def prefix(run: datetime) -> str:
    return f"gfs.{run:%Y%m%d}/{run:%H}/atmos/gfs.t{run:%H}z.pgrb2.0p25.f"


def forecast_key(run: datetime, lead: int) -> str:
    return f"{prefix(run)}{lead:03d}"
