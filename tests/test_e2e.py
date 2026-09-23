from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from wind_forecast.config import Settings
from wind_forecast.contracts import parse_request
from wind_forecast.service import Application


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
    import wind_forecast.service as service

    def fail_catboost_fit(*args, **kwargs):
        raise RuntimeError("forced CatBoost failure")

    monkeypatch.setattr(service.ForecastModel, "fit", fail_catboost_fit)
    app = Application(_settings(tmp_path), offline=True)
    request = parse_request("2026-02-01T00:00:00+05:00", 48, "demo")

    result = app.run(request)
    saved = app.read_run(result.run_id)
    forecast = saved["forecast"]
    manifest = saved["manifest"]

    assert result.status == "degraded"
    assert app.last_training_error == "RuntimeError"
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
    assert [event["state"] for event in saved["events"]][-1] == "persist_run"
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
