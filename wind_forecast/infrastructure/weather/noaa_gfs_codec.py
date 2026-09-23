"""ecCodes decoding and strict GRIB message envelope checks."""

from __future__ import annotations

from typing import Any

from wind_forecast.weather import TURBINE_COORDINATES

from .noaa_gfs_common import MAX_GRIB_MESSAGE_BYTES


def validate_grib_message(body: bytes) -> None:
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


class EcCodesDecoder:
    """Small adapter over ecCodes' public in-memory message API."""

    def decode(
        self, message: bytes, coordinates: tuple[tuple[str, float, float], ...]
    ) -> dict[str, Any]:
        try:
            import eccodes
        except ImportError as exc:  # pragma: no cover - deployment without ecCodes
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
                    point_lat, point_lon, point_value = (
                        point["lat"],
                        point["lon"],
                        point["value"],
                    )
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
