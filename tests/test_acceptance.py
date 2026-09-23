from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from wind_forecast.config import Settings
from wind_forecast.contracts import RunResult, parse_request
from wind_forecast.service import Application


def _application(root: Path) -> Application:
    data_dir = root / "data"
    data_dir.mkdir(parents=True)
    local_times = pd.date_range("2026-01-24 00:00", "2026-02-01 00:00", freq="10min", inclusive="left")
    for index, turbine in enumerate(("turbine_1", "turbine_2"), start=1):
        pd.DataFrame(
            {
                "Статистическое время": local_times.strftime("%Y-%m-%d %H:%M:%S"),
                "Средняя скорость ветра(m/s)": [5.0 + index] * len(local_times),
                "Нормализованная активная мощность": [0.35 + index * 0.05] * len(local_times),
                "Средняя температура окружающей среды(°C)": [-3.0] * len(local_times),
            }
        ).to_csv(data_dir / f"{turbine}.csv", index=False)
    return Application(
        Settings(
            root_dir=root,
            data_dir=data_dir,
            cache_dir=root / "cache",
            model_dir=root / "models",
            run_dir=root / "runs",
            fixture_dir=root / "fixtures",
            openai_api_key="",
            mode="demo",
        ),
        offline=True,
    )


@pytest.mark.parametrize("horizon", [24, 48])
def test_run_artifacts_follow_forecast_and_manifest_schemas(tmp_path: Path, horizon: int) -> None:
    app = _application(tmp_path)
    request = parse_request("2026-02-01T00:00:00+05:00", horizon, "demo")

    result = app.run(request)
    data = app.read_run(result.run_id)
    frame = data["forecast"]

    assert list(frame.columns) == [
        "run_id", "forecast_origin", "turbine_id", "valid_time", "lead_hours",
        "p10", "p50", "p90", "weather_issued_at", "weather_model", "run_status",
    ]
    assert len(frame) == horizon * 2
    assert set(frame["turbine_id"]) == {"turbine_1", "turbine_2"}
    assert not frame.duplicated(["turbine_id", "valid_time"]).any()
    assert all(set(group["lead_hours"]) == set(range(horizon)) for _, group in frame.groupby("turbine_id"))
    times = pd.to_datetime(frame["valid_time"], utc=True)
    assert times.dt.tz is not None
    quantiles = frame[["p10", "p50", "p90"]].to_numpy(dtype=float)
    assert np.isfinite(quantiles).all()
    assert ((0 <= quantiles[:, 0]) & (quantiles[:, 0] <= quantiles[:, 1]) &
            (quantiles[:, 1] <= quantiles[:, 2]) & (quantiles[:, 2] <= 1)).all()

    manifest = data["manifest"]
    assert set(manifest) == {
        "schema_version", "run_id", "forecast_origin", "horizon", "mode", "status",
        "competition_valid", "created_at", "parent_run_id", "fingerprints", "model",
        "weather_provenance", "data_quality", "calibration", "files",
    }
    assert manifest["competition_valid"] is False
    assert manifest["horizon"] == horizon
    assert set(manifest["files"]) == {
        "forecast.csv", "metrics.json", "weather.json", "report.md", "events.jsonl",
    }
    for filename, digest in manifest["files"].items():
        assert hashlib.sha256((result.directory / filename).read_bytes()).hexdigest() == digest
    metrics = json.loads((result.directory / "metrics.json").read_text())
    assert metrics["metric_status"] == "unavailable_no_labels"
    assert metrics["evaluation_period"] is None
    assert all(event["schema_version"] == 1 for event in data["events"])
    assert all(event["duration_ms"] >= 0 for event in data["events"])


def test_simulation_indexes_every_inclusive_origin_and_continues_after_error(tmp_path: Path) -> None:
    app = _application(tmp_path)
    returned = []

    def run_one(request, refresh=False):
        del refresh
        origin = pd.Timestamp(request.origin).tz_convert("UTC")
        if origin == pd.Timestamp("2026-02-01T19:00:00Z"):
            raise RuntimeError("private token must not enter the index")
        result = RunResult(
            run_id=f"run-{origin.strftime('%Y%m%d')}",
            directory=tmp_path / "runs" / f"run-{origin.strftime('%Y%m%d')}",
            status="degraded",
            reused=False,
        )
        returned.append(result)
        return result

    app.run = run_one
    results = app.simulate(date(2026, 2, 1), date(2026, 2, 3), mode="demo")

    index = json.loads(app.last_simulation_index.read_text())
    assert len(results) == 2
    assert len(index["origins"]) == 3
    assert index["failed_origins"] == 1
    assert [item["origin"] for item in index["origins"]] == [
        "2026-01-31T19:00:00Z", "2026-02-01T19:00:00Z", "2026-02-02T19:00:00Z"
    ]
    assert index["origins"][1]["error"] == "RuntimeError:execution_error"
    assert "private token" not in app.last_simulation_index.read_text()


def test_backtest_hides_accuracy_metrics_when_weather_is_synthetic(
    tmp_path: Path, monkeypatch
) -> None:
    import wind_forecast.service as service

    app = _application(tmp_path)
    metrics_path = tmp_path / "fake-backtest" / "metrics.json"
    metrics_path.parent.mkdir()

    def recorded_evaluation(*args, **kwargs):
        del args, kwargs
        metrics_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "metric_status": "available",
                    "evaluation_period": {
                        "start": "2026-01-30T19:00:00Z",
                        "end": "2026-02-01T19:00:00Z",
                    },
                    "models": [{"model": "catboost_quantile", "n": 96, "mae": 0.1}],
                    "coverage": {"evaluated_origins": 1},
                }
            )
        )
        return {"metrics_path": metrics_path}

    monkeypatch.setattr(service, "rolling_backtest", recorded_evaluation)
    actual_path = app.backtest(
        mode="demo",
        start=date(2026, 1, 30),
        end=date(2026, 1, 30),
        history_start=date(2026, 1, 24),
        iterations=2,
    )

    metrics = json.loads(actual_path.read_text())
    assert metrics["metric_status"] == "unavailable_unverified_weather"
    assert metrics["models"] == []
    assert metrics["coverage"]["competition_valid"] is False
