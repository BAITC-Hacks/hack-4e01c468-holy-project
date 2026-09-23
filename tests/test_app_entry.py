from __future__ import annotations

from types import SimpleNamespace

import pytest

import app


def test_dashboard_entrypoint_opts_into_offline_weather_only_explicitly(monkeypatch) -> None:
    settings = SimpleNamespace(mode="demo")
    constructed = []
    rendered = []

    monkeypatch.setenv("DEMO_OFFLINE", "1")
    monkeypatch.setattr("wind_forecast.config.Settings.from_env", lambda: settings)
    monkeypatch.setattr(
        "wind_forecast.service.Application",
        lambda actual_settings, **kwargs: constructed.append((actual_settings, kwargs)) or "service",
    )
    monkeypatch.setattr(app, "render", rendered.append)

    app.main()

    assert constructed == [(settings, {"offline": True})]
    assert rendered == ["service"]


def test_dashboard_entrypoint_does_not_select_offline_weather_from_demo_mode_alone(
    monkeypatch,
) -> None:
    settings = SimpleNamespace(mode="demo")
    constructed = []

    monkeypatch.delenv("DEMO_OFFLINE", raising=False)
    monkeypatch.setattr("wind_forecast.config.Settings.from_env", lambda: settings)
    monkeypatch.setattr(
        "wind_forecast.service.Application",
        lambda actual_settings, **kwargs: constructed.append(kwargs) or "service",
    )
    monkeypatch.setattr(app, "render", lambda service: None)

    app.main()

    assert constructed == [{"offline": False}]


def test_dashboard_entrypoint_refuses_offline_weather_in_competition_mode(monkeypatch) -> None:
    settings = SimpleNamespace(mode="competition")
    constructed = []

    monkeypatch.setenv("DEMO_OFFLINE", "1")
    monkeypatch.setattr("wind_forecast.config.Settings.from_env", lambda: settings)
    monkeypatch.setattr(
        "wind_forecast.service.Application",
        lambda *args, **kwargs: constructed.append((args, kwargs)),
    )
    monkeypatch.setattr(app, "render", lambda service: None)

    with pytest.raises(ValueError, match="DEMO_OFFLINE requires RUN_MODE=demo"):
        app.main()

    assert constructed == []
