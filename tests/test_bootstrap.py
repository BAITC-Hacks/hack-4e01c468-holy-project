from __future__ import annotations

import ast
import inspect
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from wind_forecast.config import Settings


def _settings(root, *, api_key: str = "") -> Settings:
    return Settings(
        root_dir=root,
        data_dir=root / "data",
        cache_dir=root / "cache",
        model_dir=root / "models",
        run_dir=root / "runs",
        fixture_dir=root / "fixtures",
        openai_api_key=api_key,
        mode="demo",
    )


def test_build_runtime_keeps_explicit_weather_and_analyzer(tmp_path):
    from wind_forecast.bootstrap import build_runtime

    weather = object()
    analyzer = object()

    runtime = build_runtime(
        _settings(tmp_path), weather_provider=weather, analyzer=analyzer
    )

    assert runtime.weather_provider is weather
    assert runtime.analyzer is analyzer


def test_configured_gfs_provider_is_selected_without_fetching(tmp_path):
    from wind_forecast.bootstrap import build_runtime
    from wind_forecast.infrastructure.weather.noaa_gfs import NoaaGfsWeatherProvider

    settings = replace(_settings(tmp_path), weather_provider="noaa_gfs")

    runtime = build_runtime(settings)

    assert isinstance(runtime.weather_provider, NoaaGfsWeatherProvider)
    assert runtime.weather_provider.cache_dir == settings.cache_dir


def test_weather_injection_precedes_configured_gfs_provider(tmp_path):
    from wind_forecast.bootstrap import build_runtime

    weather = object()
    settings = replace(_settings(tmp_path), weather_provider="noaa_gfs")

    runtime = build_runtime(settings, weather_provider=weather)

    assert runtime.weather_provider is weather


def test_offline_and_fixture_sources_precede_configured_gfs_provider(tmp_path):
    from wind_forecast.bootstrap import build_runtime
    from wind_forecast.infrastructure.weather.demo import (
        FixtureWeatherProvider,
        SyntheticWeatherProvider,
    )

    settings = replace(_settings(tmp_path), weather_provider="noaa_gfs")
    offline = build_runtime(settings, offline=True)
    fixture_path = Path(__file__).parent / "fixtures/weather/synthetic_48h.json"
    fixture = build_runtime(settings, weather_fixture=fixture_path)

    assert isinstance(offline.weather_provider, SyntheticWeatherProvider)
    assert isinstance(fixture.weather_provider, FixtureWeatherProvider)


def test_offline_runtime_fetches_synthetic_demo_weather_without_openai(
    tmp_path, monkeypatch
):
    import wind_forecast.bootstrap as wiring
    from wind_forecast.contracts import parse_request

    def forbidden_openai(*args, **kwargs):
        raise AssertionError("offline runtime constructed OpenAI")

    monkeypatch.setattr(wiring, "OpenAIAnalyzer", forbidden_openai)
    runtime = wiring.build_runtime(
        _settings(tmp_path, api_key="test-sentinel"), offline=True
    )

    snapshot = runtime.weather_provider.fetch(
        parse_request("2026-02-01T00:00:00+05:00", 48, "demo")
    )

    assert len(snapshot.rows) == 96
    assert snapshot.provenance["competition_valid"] is False
    assert runtime.analyzer({}, {"continue"}).action == "continue"


def test_offline_and_fixed_fixture_sources_cannot_be_combined(tmp_path):
    from wind_forecast.bootstrap import build_runtime

    with pytest.raises(ValueError, match="cannot be used together"):
        build_runtime(
            _settings(tmp_path), offline=True, weather_fixture=tmp_path / "fixture.json"
        )


def test_malformed_weather_fixture_is_rejected_during_composition(tmp_path):
    from wind_forecast.bootstrap import build_runtime

    fixture = tmp_path / "broken.json"
    fixture.write_text("{broken", encoding="utf-8")

    with pytest.raises(ValueError, match="fixture"):
        build_runtime(_settings(tmp_path), weather_fixture=fixture)


def test_fixed_fixture_keeps_utc_valid_times_and_requested_leads(tmp_path):
    from wind_forecast.bootstrap import build_runtime
    from wind_forecast.contracts import parse_request

    fixture = Path(__file__).parent / "fixtures/weather/synthetic_48h.json"
    runtime = build_runtime(_settings(tmp_path), weather_fixture=fixture)
    snapshot = runtime.weather_provider.fetch(
        parse_request("2026-02-01T00:00:00+05:00", 24, "demo")
    )
    times = snapshot.rows["valid_time"]

    assert times.min().isoformat() == "2026-01-31T19:00:00+00:00"
    assert times.max().isoformat() == "2026-02-01T18:00:00+00:00"
    assert set(snapshot.rows["lead_hours"]) == set(range(24))
    assert len(snapshot.rows) == 48


def test_each_runtime_owns_fresh_pipelines_and_store(tmp_path):
    from wind_forecast.bootstrap import build_runtime

    settings = _settings(tmp_path)
    first = build_runtime(settings, offline=True)
    second = build_runtime(settings, offline=True)

    assert first.data_pipeline is not second.data_pipeline
    assert first.feature_pipeline is not second.feature_pipeline
    assert first.store is not second.store
    assert first.store.root == (tmp_path / "runs").resolve()


def test_application_facade_keeps_injections_and_runs_offline_fixture(tmp_path):
    from tests.test_e2e import _settings as e2e_settings
    from wind_forecast.contracts import parse_request
    from wind_forecast.service import Application

    weather = object()
    analyzer = object()
    injected = Application(
        _settings(tmp_path / "injected"),
        weather_provider=weather,
        analyzer=analyzer,
    )
    assert injected.weather_provider is weather
    assert injected.analyzer is analyzer

    app = Application(
        e2e_settings(tmp_path / "offline"),
        offline=True,
        model_parameters={"iterations": 2},
    )
    result = app.run(parse_request("2026-02-01T00:00:00+05:00", 24, "demo"))
    saved = app.read_run(result.run_id)

    assert len(saved["forecast"]) == 48
    assert saved["manifest"]["competition_valid"] is False


@pytest.mark.parametrize(
    "modules",
    [
        ("wind_forecast.bootstrap", "wind_forecast.service"),
        ("wind_forecast.service", "wind_forecast.bootstrap"),
    ],
)
def test_bootstrap_and_service_import_in_either_order(modules):
    code = "\n".join(f"import {module}" for module in modules)
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr


def test_ports_do_not_import_outward_adapters_or_clients():
    import wind_forecast.application.ports as ports

    tree = ast.parse(inspect.getsource(ports))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    forbidden = (
        "wind_forecast.infrastructure",
        "wind_forecast.bootstrap",
        "wind_forecast.service",
        "requests",
        "openai",
    )

    assert not any(
        name == prefix or name.startswith(prefix + ".")
        for name in imported
        for prefix in forbidden
    )
