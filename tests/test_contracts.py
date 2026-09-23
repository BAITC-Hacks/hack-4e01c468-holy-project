from pathlib import Path

import pytest

from wind_forecast.config import Settings
from wind_forecast.contracts import parse_request


def test_zoned_origin_and_lead_contract() -> None:
    request = parse_request("2026-02-01T00:00:00+05:00", 48)

    assert request.origin.isoformat() == "2026-01-31T19:00:00+00:00"
    assert request.horizon == 48
    assert request.mode == "competition"


@pytest.mark.parametrize(
    ("origin", "horizon", "mode"),
    [
        ("2026-02-01T00:00:00", 48, "competition"),
        ("2026-02-01T00:30:00+05:00", 48, "competition"),
        ("2026-02-01T00:00:00+05:00", 25, "competition"),
        ("2026-02-01T00:00:00+05:00", 24.0, "competition"),
        ("2026-02-01T00:00:00+05:00", 24, "invalid"),
    ],
)
def test_invalid_request(origin: str, horizon: int, mode: str) -> None:
    with pytest.raises(ValueError):
        parse_request(origin, horizon, mode)


def test_settings_load_allowlisted_values_and_resolve_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-must-not-appear")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "none")
    monkeypatch.setenv("HTTP_TIMEOUT_SECONDS", "7")
    monkeypatch.setenv("RUN_MODE", "demo")
    monkeypatch.setenv("WEATHER_FIXTURE_DIR", "fixtures/weather")
    monkeypatch.setenv("DATA_DIR", "custom-data")
    monkeypatch.setenv("CACHE_DIR", "custom-cache")
    monkeypatch.setenv("MODEL_DIR", "custom-models")
    monkeypatch.setenv("RUN_DIR", "custom-runs")

    settings = Settings.from_env(root_dir=tmp_path)

    assert settings.openai_api_key == "test-key-must-not-appear"
    assert settings.openai_model == "test-model"
    assert settings.openai_reasoning_effort == "none"
    assert settings.http_timeout_seconds == 7
    assert settings.mode == "demo"
    assert settings.fixture_dir == tmp_path / "fixtures/weather"
    assert settings.data_dir == tmp_path / "custom-data"
    assert settings.cache_dir == tmp_path / "custom-cache"
    assert settings.model_dir == tmp_path / "custom-models"
    assert settings.run_dir == tmp_path / "custom-runs"
    assert "test-key-must-not-appear" not in repr(settings)


def test_settings_defaults_use_project_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in (
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "OPENAI_REASONING_EFFORT",
        "HTTP_TIMEOUT_SECONDS",
        "RUN_MODE",
        "WEATHER_FIXTURE_DIR",
        "DATA_DIR",
        "CACHE_DIR",
        "MODEL_DIR",
        "RUN_DIR",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env(root_dir=tmp_path)

    project_root = tmp_path
    assert settings.data_dir == project_root
    assert settings.cache_dir == project_root / ".cache/wind"
    assert settings.model_dir == project_root / "artifacts/models"
    assert settings.run_dir == project_root / "artifacts/runs"
    assert settings.fixture_dir == project_root / "tests/fixtures/weather"
    assert settings.http_timeout_seconds == 20
    assert settings.mode == "competition"
