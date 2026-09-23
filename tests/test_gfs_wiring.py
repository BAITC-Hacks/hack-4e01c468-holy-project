from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from wind_forecast.config import Settings
from wind_forecast.contracts import parse_request
from wind_forecast.infrastructure.weather.noaa_gfs import NoaaGfsWeatherProvider
from wind_forecast.service import Application, _ApplicationPredictor


def _settings(root, *, provider="openmeteo", auto_train=True):
    return Settings(
        root_dir=root,
        data_dir=root / "data",
        cache_dir=root / "cache",
        model_dir=root / "models",
        run_dir=root / "runs",
        fixture_dir=root / "fixtures",
        mode="demo",
        weather_provider=provider,
        auto_train_model=auto_train,
    )


def test_gfs_weather_identity_is_used_for_all_source_scoped_workspaces(tmp_path):
    from tests.test_e2e import _settings as data_settings

    settings = replace(
        data_settings(tmp_path), weather_provider="noaa_gfs", auto_train_model=False
    )
    app = Application(settings)
    request = parse_request("2026-02-01T00:00:00+05:00", 24, "demo")

    assert app._snapshot_identity == NoaaGfsWeatherProvider.source_identity
    assert app._snapshot_cache_path(request).parent.name == "application-snapshots"
    assert app._active_model_path("demo").name.endswith(
        f"{NoaaGfsWeatherProvider.source_identity}.json"
    )
    assert app._backtest_fold_cache_dir().name == (
        f"backtest-folds-{NoaaGfsWeatherProvider.source_identity}"
    )
    assert app._backtest_identity("demo", [])["weather_source"] == (
        NoaaGfsWeatherProvider.source_identity
    )


def test_gfs_identity_cannot_read_openmeteo_model_pointer_or_snapshot_cache(tmp_path):
    from tests.test_e2e import _settings as data_settings

    openmeteo = Application(data_settings(tmp_path / "openmeteo"))
    gfs = Application(
        replace(
            data_settings(tmp_path / "gfs"),
            weather_provider="noaa_gfs",
            auto_train_model=False,
        )
    )
    request = parse_request("2026-02-01T00:00:00+05:00", 24, "demo")
    openmeteo._write_json(
        openmeteo._active_model_path("demo"),
        {
            "schema_version": 1,
            "mode": "demo",
            "weather_source": openmeteo._snapshot_identity,
            "source_fingerprint": openmeteo._source_identity(),
            "training_fingerprint": "openmeteo-trained-model",
        },
    )

    assert gfs._snapshot_identity != openmeteo._snapshot_identity
    assert gfs._snapshot_cache_path(request) != openmeteo._snapshot_cache_path(request)
    assert gfs._active_model_path("demo") != openmeteo._active_model_path("demo")
    assert gfs._backtest_fold_cache_dir() != openmeteo._backtest_fold_cache_dir()
    assert gfs._active_model_identity("demo", request.origin) is None


def test_explicit_injected_provider_identity_is_namespaced_and_redacted(tmp_path):
    class InjectedProvider:
        source_identity = "private/cache/path-and-token"

    app = Application(_settings(tmp_path), weather_provider=InjectedProvider())

    assert app._snapshot_identity.startswith("injected-")
    assert "/" not in app._snapshot_identity
    assert "private" not in app._snapshot_identity
    assert "token" not in app._snapshot_identity

    injected_over_offline = Application(
        _settings(tmp_path / "offline"),
        weather_provider=InjectedProvider(),
        offline=True,
    )
    assert injected_over_offline._snapshot_identity == app._snapshot_identity


def test_provider_without_declared_identity_keeps_legacy_identity(tmp_path):
    app = Application(_settings(tmp_path), weather_provider=object())

    assert app._snapshot_identity == "openmeteo-ifs"


def test_disabled_auto_training_returns_safe_error_without_training(tmp_path, monkeypatch):
    from tests.test_e2e import _settings as data_settings

    app = Application(replace(data_settings(tmp_path), auto_train_model=False))
    calls = []

    def forbidden_training(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("automatic training must remain disabled")

    monkeypatch.setattr(app, "train", forbidden_training)

    model = app._ensure_model("demo", datetime(2026, 2, 1, tzinfo=timezone.utc))

    assert model is None
    assert calls == []
    assert app.last_training_error == "automatic_training_disabled_model_unavailable"


def test_disabled_auto_training_returns_an_existing_model_without_training(
    tmp_path, monkeypatch
):
    app = Application(_settings(tmp_path, auto_train=False))
    saved_model = object()
    monkeypatch.setattr(app, "_load_active_model", lambda mode, origin: saved_model)
    monkeypatch.setattr(
        app,
        "train",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("existing model should avoid training")
        ),
    )

    result = app._ensure_model("demo", datetime(2026, 2, 1, tzinfo=timezone.utc))

    assert result is saved_model
    assert app.last_training_error is None


def test_auto_training_policy_changes_predictor_reuse_identity(tmp_path):
    from tests.test_e2e import _settings as data_settings

    enabled = Application(replace(data_settings(tmp_path / "enabled"), auto_train_model=True))
    disabled = Application(replace(data_settings(tmp_path / "disabled"), auto_train_model=False))
    request = parse_request("2026-02-01T00:00:00+05:00", 24, "demo")
    enabled_identity = _ApplicationPredictor(enabled, request).reuse_identity()
    disabled_identity = _ApplicationPredictor(disabled, request).reuse_identity()

    assert enabled_identity["config"]["auto_train_model"] is True
    assert disabled_identity["config"]["auto_train_model"] is False
    assert enabled_identity["config"] != disabled_identity["config"]
