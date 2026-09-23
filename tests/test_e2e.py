from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from wind_forecast.config import Settings
from wind_forecast.contracts import parse_request
from wind_forecast.service import Application
from wind_forecast.weather import make_synthetic_weather_snapshot


def _source_data(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    local_times = pd.date_range("2026-01-24 00:00", "2026-02-01 00:00", freq="10min", inclusive="left")
    for index, turbine in enumerate(("turbine_1", "turbine_2"), start=1):
        pd.DataFrame(
            {
                "Статистическое время": local_times.strftime("%Y-%m-%d %H:%M:%S"),
                "Средняя скорость ветра(m/s)": [5.0 + index] * len(local_times),
                "Нормализованная активная мощность": [0.35 + index * 0.05] * len(local_times),
                "Средняя температура окружающей среды(°C)": [-3.0] * len(local_times),
            }
        ).to_csv(directory / f"{turbine}.csv", index=False)


def _settings(root: Path) -> Settings:
    data = root / "data"
    _source_data(data)
    return Settings(
        root_dir=root,
        data_dir=data,
        cache_dir=root / "cache",
        model_dir=root / "models",
        run_dir=root / "runs",
        fixture_dir=root / "fixtures",
        openai_api_key="",
        mode="demo",
    )


def test_offline_application_falls_back_after_catboost_failure_without_openai(
    tmp_path: Path, monkeypatch
) -> None:
    import wind_forecast.agent as agent_module
    import wind_forecast.service as service

    def fail_catboost_fit(*args, **kwargs):
        raise RuntimeError("forced CatBoost failure")

    monkeypatch.setattr(service.ForecastModel, "fit", fail_catboost_fit)
    app = Application(_settings(tmp_path), offline=True)
    request = parse_request("2026-02-01T00:00:00+05:00", 48, "demo")
    validated_request_weather: list[bool] = []
    real_validate_weather = agent_module.validate_weather

    def track_request_weather(snapshot, checked_request):
        result = real_validate_weather(snapshot, checked_request)
        if checked_request == request:
            validated_request_weather.append(True)
        return result

    monkeypatch.setattr(agent_module, "validate_weather", track_request_weather)
    ensure_attempts_after_gate: list[bool] = []
    real_ensure_model = app._ensure_model

    def ensure_only_after_weather_gate(mode, origin=None):
        assert validated_request_weather, "model access ran before request weather validation"
        ensure_attempts_after_gate.append(True)
        return real_ensure_model(mode, origin)

    monkeypatch.setattr(app, "_ensure_model", ensure_only_after_weather_gate)

    result = app.run(request)
    saved = app.read_run(result.run_id)
    forecast = saved["forecast"]
    manifest = saved["manifest"]

    assert result.status == "degraded"
    assert app.last_training_error == "RuntimeError"
    assert ensure_attempts_after_gate == [True]
    assert len(forecast) == 96
    assert not forecast.duplicated(["turbine_id", "valid_time"]).any()
    assert set(forecast["turbine_id"]) == {"turbine_1", "turbine_2"}
    assert pd.to_datetime(forecast["forecast_origin"], utc=True).dt.tz is not None
    quantiles = forecast[["p10", "p50", "p90"]].to_numpy(dtype=float)
    assert np.isfinite(quantiles).all()
    assert ((0 <= quantiles[:, 0]) & (quantiles[:, 0] <= quantiles[:, 1]) &
            (quantiles[:, 1] <= quantiles[:, 2]) & (quantiles[:, 2] <= 1)).all()
    assert manifest["status"] == "degraded"
    assert manifest["competition_valid"] is False
    assert {path.name for path in result.directory.iterdir()} == {
        "forecast.csv", "manifest.json", "metrics.json", "events.jsonl", "weather.json", "report.md"
    }
    states = [event["state"] for event in saved["events"]]
    required_order = [
        "fetch_weather", "validate_inputs", "prepare_features", "train_or_load_model",
        "generate_baseline", "quality_gate", "persist_run",
    ]
    positions = [states.index(state) for state in required_order]
    assert positions == sorted(positions)
    assert "generate_forecast" not in states
    assert saved["metrics"]["metric_status"] == "unavailable_no_labels"
    assert "synthetic" in manifest["weather_provenance"]["provenance_status"]
    assert json.loads((result.directory / "manifest.json").read_text())["mode"] == "demo"


def test_competition_request_rejects_synthetic_weather_before_forecasting(tmp_path: Path) -> None:
    app = Application(_settings(tmp_path), offline=True)
    request = parse_request("2026-02-01T00:00:00+05:00", 24, "competition")

    try:
        app.run(request)
    except ValueError as exc:
        assert str(exc) == "offline synthetic weather is only allowed in demo mode"
    else:
        raise AssertionError("competition accepted a synthetic weather provider")


def test_past_origin_manifest_excludes_later_observations(tmp_path: Path, monkeypatch) -> None:
    import wind_forecast.service as service

    def fail_catboost_fit(*args, **kwargs):
        raise RuntimeError("forced CatBoost failure")

    monkeypatch.setattr(service.ForecastModel, "fit", fail_catboost_fit)
    settings = _settings(tmp_path)
    app = Application(settings, offline=True, model_parameters={"iterations": 2})
    request = parse_request("2026-01-30T00:00:00+05:00", 24, "demo")

    result = app.run(request)
    saved = app.read_run(result.run_id)
    latest_available = pd.Timestamp(saved["manifest"]["data_quality"]["latest_available_at"])

    assert latest_available <= pd.Timestamp(request.origin)
    assert saved["manifest"]["data_quality"]["observation_age_hours"] == 0


def test_data_quality_filters_future_rows_before_calculating_age(tmp_path: Path) -> None:
    app = Application(_settings(tmp_path), offline=True)
    history = app._load_history()
    origin = pd.Timestamp("2026-01-30T00:00:00+05:00")

    quality = app._data_quality(history, origin)

    assert pd.Timestamp(quality["latest_available_at"]) <= origin
    assert quality["observation_age_hours"] == 0


def test_data_quality_reports_old_january_observations_as_stale_in_february(
    tmp_path: Path,
) -> None:
    app = Application(_settings(tmp_path), offline=True)
    history = app._load_history()
    origin = pd.Timestamp("2026-02-28T00:00:00+05:00")

    quality = app._data_quality(history, origin)

    assert pd.Timestamp(quality["latest_available_at"]) <= origin
    assert quality["observation_age_hours"] >= 24 * 20
    assert quality["stale_observations"] is True


def test_missing_baseline_calibration_cutoff_cannot_select_a_nondefault_strategy(
    tmp_path: Path,
) -> None:
    app = Application(_settings(tmp_path), offline=True)
    pointer = {
        "schema_version": 1,
        "mode": "demo",
        "weather_source": app._snapshot_identity,
        "source_fingerprint": app._source_identity(),
        "strategy": "seasonal",
        "calibration_scores": {
            "persistence": {"mae": 0.3, "n": 10},
            "seasonal": {"mae": 0.1, "n": 10},
            "power_curve": {"mae": 0.2, "n": 10},
        },
    }
    app._write_json(app._baseline_selection_path("demo"), pointer)

    strategy = app._selected_baseline("demo", pd.Timestamp("2026-02-01T00:00:00Z"))

    assert strategy == "persistence"


def test_baseline_selection_uses_held_out_scores_only_after_their_cutoff(
    tmp_path: Path,
) -> None:
    app = Application(_settings(tmp_path), offline=True)
    pointer = {
        "schema_version": 1,
        "mode": "demo",
        "weather_source": app._snapshot_identity,
        "source_fingerprint": app._source_identity(),
        "strategy": "seasonal",
        "calibration_cutoff": "2026-01-31T19:00:00Z",
        "calibration_scores": {
            "persistence": {"mae": 0.3, "n": 10},
            "seasonal": {"mae": 0.1, "n": 10},
            "power_curve": {"mae": 0.2, "n": 10},
        },
    }
    app._write_json(app._baseline_selection_path("demo"), pointer)

    prior = app._baseline_selection("demo", pd.Timestamp("2026-01-30T19:00:00Z"))
    eligible = app._baseline_selection("demo", pd.Timestamp("2026-02-01T00:00:00Z"))

    assert prior["strategy"] == "persistence"
    assert prior["status"] == "unavailable_uncalibrated_persistence"
    assert eligible["strategy"] == "seasonal"
    assert eligible["status"] == "selected_from_held_out_labels"


def test_safe_heldout_baseline_residuals_calibrate_fallback_intervals(
    tmp_path: Path, monkeypatch
) -> None:
    app = Application(_settings(tmp_path), offline=True)
    monkeypatch.setattr(app, "_ensure_model", lambda *args, **kwargs: None)
    pointer = {
        "schema_version": 1,
        "mode": "demo",
        "weather_source": app._snapshot_identity,
        "source_fingerprint": app._source_identity(),
        "strategy": "seasonal",
        "calibration_cutoff": "2026-01-31T19:00:00Z",
        "calibration_scores": {
            "persistence": {"mae": 0.3, "n": 10},
            "seasonal": {"mae": 0.1, "n": 10},
            "power_curve": {"mae": 0.2, "n": 10},
        },
        "baseline_calibrations": {
            "seasonal": {
                "status": "calibrated",
                "minimum_samples": 30,
                "n": 80,
                "counts_by_turbine": {"turbine_1": 40, "turbine_2": 40},
                "by_turbine": {
                    "turbine_1": {"p10": -0.1, "p90": 0.1},
                    "turbine_2": {"p10": -0.1, "p90": 0.1},
                },
                "global": None,
            }
        },
    }
    app._write_json(app._baseline_selection_path("demo"), pointer)
    request = parse_request("2026-02-01T00:00:00+05:00", 24, "demo")

    result = app.run(request)
    saved = app.read_run(result.run_id)

    assert result.status == "degraded"
    assert saved["manifest"]["model"]["name"] == "seasonal"
    assert saved["manifest"]["calibration"]["status"] == "calibrated"
    assert saved["manifest"]["calibration"]["strategy"] == "seasonal"
    widths = saved["forecast"]["p90"] - saved["forecast"]["p10"]
    assert (widths < 1).all()


def test_weather_outage_uses_valid_cached_weather_without_openai(tmp_path: Path) -> None:
    class CachedWeatherOnly:
        def fetch(self, request, refresh=False):
            del request, refresh
            raise RuntimeError("weather service unavailable")

        def read_cached(self, request):
            return make_synthetic_weather_snapshot(request)

    app = Application(_settings(tmp_path), weather_provider=CachedWeatherOnly())
    request = parse_request("2026-02-01T00:00:00+05:00", 24, "demo")

    result = app.run(request)
    saved = app.read_run(result.run_id)

    assert result.status == "degraded"
    assert any(
        event["state"] == "analyze_result"
        and event["action"] == "use_cached_weather"
        for event in saved["events"]
    )
    assert any(event["state"] == "read_cached_weather" for event in saved["events"])


def test_cold_run_records_trained_model_identity_for_the_next_reuse(tmp_path: Path) -> None:
    app = Application(_settings(tmp_path), offline=True, model_parameters={"iterations": 2})
    request = parse_request("2026-02-01T00:00:00+05:00", 24, "demo")

    first = app.run(request)
    first_run = app.read_run(first.run_id)
    second = app.run(request)

    assert first_run["manifest"]["fingerprints"]["predictor_config"]
    assert first_run["manifest"]["fingerprints"]["predictor_model"]
    assert second.reused is True
