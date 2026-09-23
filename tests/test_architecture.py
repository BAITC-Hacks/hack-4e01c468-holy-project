"""Boundary checks for the incremental architecture migration."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from wind_forecast.contracts import WeatherSnapshot, parse_request


_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "weather" / "synthetic_48h.json"
_FIXTURE_FINGERPRINT = "962778f58a4327a0411588e54e208b01394a788016f50742c21f234b1dc63edb"


def test_weather_policy_preserves_the_checked_in_snapshot_fingerprint() -> None:
    from wind_forecast.application.forecasting.weather_policy import (
        validate_weather as application_validate_weather,
        weather_fingerprint as application_weather_fingerprint,
    )
    from wind_forecast.agent import _snapshot_fingerprint as agent_snapshot_fingerprint
    from wind_forecast.artifacts import _snapshot_fingerprint as artifact_snapshot_fingerprint
    from wind_forecast.weather import (
        validate_weather as compatibility_validate_weather,
        weather_fingerprint as compatibility_weather_fingerprint,
    )

    fixture = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    snapshot = WeatherSnapshot(
        rows=pd.DataFrame(fixture["rows"]),
        raw_responses=[],
        fingerprint=fixture["fingerprint"],
        provenance=fixture["provenance"],
    )
    request = parse_request("2026-02-01T00:00:00+05:00", 48, mode="demo")

    application_validate_weather(snapshot, request)

    assert application_weather_fingerprint(snapshot.rows, snapshot.provenance) == _FIXTURE_FINGERPRINT
    assert compatibility_validate_weather is application_validate_weather
    assert compatibility_weather_fingerprint is application_weather_fingerprint
    assert agent_snapshot_fingerprint(snapshot) == _FIXTURE_FINGERPRINT
    assert artifact_snapshot_fingerprint(snapshot) == _FIXTURE_FINGERPRINT


def test_quality_gate_is_reexported_from_the_application_policy() -> None:
    from wind_forecast.agent import quality_gate as compatibility_quality_gate
    from wind_forecast.application.forecasting.quality import quality_gate

    assert compatibility_quality_gate is quality_gate


def test_policy_features_and_storage_import_without_provider_or_agent_modules() -> None:
    code = """
import importlib.abc
import sys

blocked = ("wind_forecast.agent", "wind_forecast.weather", "wind_forecast.infrastructure")

class BoundaryGuard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == item or fullname.startswith(item + ".") for item in blocked):
            raise ImportError("forbidden outward dependency: " + fullname)
        return None

sys.meta_path.insert(0, BoundaryGuard())
for module in (
    "wind_forecast.application.forecasting.quality",
    "wind_forecast.application.forecasting.weather_policy",
    "wind_forecast.features",
    "wind_forecast.artifacts",
):
    __import__(module)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
