"""Synthetic and fixed-fixture weather sources for demo runs."""

import json
from pathlib import Path
from typing import Any

import pandas as pd

from wind_forecast.contracts import RunRequest, WeatherSnapshot
from wind_forecast.weather import make_synthetic_weather_snapshot, weather_fingerprint


class SyntheticWeatherProvider:
    """Explicit deterministic provider for offline demo runs."""

    def fetch(self, request: RunRequest, refresh: bool = False) -> WeatherSnapshot:
        del refresh
        return make_synthetic_weather_snapshot(request)


class FixtureWeatherProvider:
    """Read one recorded weather fixture without changing its valid times."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path).expanduser().resolve()
        try:
            self.payload: dict[str, Any] = json.loads(
                self.path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("weather fixture could not be read as JSON") from exc
        if self.payload.get("mode") != "demo" or not isinstance(
            self.payload.get("rows"), list
        ):
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
