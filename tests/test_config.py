from __future__ import annotations

import pytest

from wind_forecast.config import Settings


def test_weather_provider_defaults_to_openmeteo(tmp_path, monkeypatch):
    monkeypatch.delenv("WEATHER_PROVIDER", raising=False)

    settings = Settings.from_env(tmp_path)

    assert settings.weather_provider == "openmeteo"


@pytest.mark.parametrize("provider", ["openmeteo", "noaa_gfs"])
def test_weather_provider_accepts_supported_configuration(tmp_path, monkeypatch, provider):
    monkeypatch.setenv("WEATHER_PROVIDER", provider)

    settings = Settings.from_env(tmp_path)

    assert settings.weather_provider == provider


def test_weather_provider_rejects_unknown_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("WEATHER_PROVIDER", "unverified-source")

    with pytest.raises(ValueError, match="WEATHER_PROVIDER"):
        Settings.from_env(tmp_path)


def test_automatic_model_training_defaults_on(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTO_TRAIN_MODEL", raising=False)

    settings = Settings.from_env(tmp_path)

    assert settings.auto_train_model is True


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1", True), ("true", True), ("yes", True), ("on", True),
     ("0", False), ("false", False), ("no", False), ("off", False)],
)
def test_automatic_model_training_parses_explicit_boolean_values(
    tmp_path, monkeypatch, value, expected
):
    monkeypatch.setenv("AUTO_TRAIN_MODEL", value)

    settings = Settings.from_env(tmp_path)

    assert settings.auto_train_model is expected


def test_automatic_model_training_rejects_ambiguous_value(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTO_TRAIN_MODEL", "sometimes")

    with pytest.raises(ValueError, match="AUTO_TRAIN_MODEL"):
        Settings.from_env(tmp_path)
