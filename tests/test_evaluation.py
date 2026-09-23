from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from tests.test_features import make_history, make_snapshot
from wind_forecast.evaluation import apply_residual_calibration, rolling_backtest, score


def test_metrics_and_zero_denominator() -> None:
    metrics = score([0.0, 1.0], [0.0, 0.5], [0.0, 0.4], [0.1, 1.0])
    assert metrics["mae"] == pytest.approx(0.25)
    assert metrics["rmse"] == pytest.approx((0.125) ** 0.5)
    assert metrics["bias"] == pytest.approx(-0.25)
    assert metrics["smape"] == pytest.approx(100 / 3)
    assert metrics["interval_coverage"] == 1.0
    assert score([0.0], [0.0])["smape"] == 0.0


def test_score_excludes_missing_truth_but_rejects_nonfinite_predictions() -> None:
    metrics = score([0.0, np.nan, 1.0], [0.0, 0.8, 0.5])
    assert metrics["n"] == 2
    assert metrics["coverage"] == pytest.approx(2 / 3)
    with pytest.raises(ValueError, match="p50 predictions must all be finite"):
        score([np.nan, 1.0], [np.inf, 0.5])
    with pytest.raises(ValueError, match="p10 and p90 must be provided together"):
        score([1.0], [0.5], [0.4])
    missing = score([np.nan], [0.5], [0.0], [1.0])
    assert missing["n"] == 0
    assert missing["mae"] is None
    assert missing["interval_coverage"] is None


def test_apply_residual_calibration_uses_global_fallback() -> None:
    prediction = pd.DataFrame(
        {"turbine_id": ["turbine_1", "turbine_2"], "p50": [0.5, 0.5]}
    )
    calibration_predictions = pd.DataFrame(
        {
            "turbine_id": ["turbine_1", "turbine_2"] * 20,
            "target": np.linspace(0.1, 0.9, 40),
            "prediction": np.full(40, 0.5),
        }
    )
    result, calibration = apply_residual_calibration(prediction, calibration_predictions)
    assert calibration["global"] is not None
    assert (result["p10"] <= result["p50"]).all()
    assert (result["p50"] <= result["p90"]).all()

    unavailable, state = apply_residual_calibration(prediction, calibration_predictions.iloc[:10])
    assert state["status"] == "unavailable"
    assert unavailable["p10"].tolist() == [0.0, 0.0]
    assert unavailable["p90"].tolist() == [1.0, 1.0]


def test_rolling_backtest_uses_common_keys_and_persists_artifacts(tmp_path) -> None:
    pytest.importorskip("catboost")
    history = make_history(end="2026-01-06T00:00:00Z")
    missing_truth = (history["turbine_id"] == "turbine_1") & (
        history["hour_start"] == pd.Timestamp("2026-01-04T00:00:00Z")
    )
    history.loc[missing_truth, "quality_flag"] = "bad"
    snapshots = [
        make_snapshot("2026-01-01T00:00:00Z"),
        make_snapshot("2026-01-02T00:00:00Z"),
        make_snapshot("2026-01-03T00:00:00Z"),
    ]
    result = rolling_backtest(
        history,
        snapshots,
        [datetime(2026, 1, 3, tzinfo=timezone.utc)],
        tmp_path / "models",
        model_parameters={"iterations": 2, "thread_count": 1},
        calibration_days=1,
    )
    metrics = result["metrics"]
    assert metrics["coverage"]["evaluated_origins"] == 1
    assert metrics["coverage"]["available_common_target_rows"] == 95
    assert {entry["model"] for entry in metrics["models"]} == {
        "catboost_quantile",
        "persistence",
        "seasonal",
        "power_curve",
    }
    overall = [entry for entry in metrics["models"] if entry["turbine_id"] is None and entry["lead_hours"] is None]
    assert len(overall) == 4
    assert all(entry["n"] == 95 for entry in overall)
    assert all(entry["coverage"] == pytest.approx(95 / 96) for entry in overall)
    assert result["metrics_path"].is_file()
    assert result["predictions_path"].is_file()
    saved = json.loads(result["metrics_path"].read_text(encoding="utf-8"))
    assert saved["coverage"]["evaluated_origins"] == 1
    eligible = result["predictions"].loc[result["predictions"]["comparison_eligible"]]
    assert len(eligible) == 4 * 96
    assert eligible["target"].isna().sum() == 4


def test_rolling_backtest_without_future_truth_writes_null_metrics(tmp_path) -> None:
    pytest.importorskip("catboost")
    history = make_history(end="2026-01-03T00:00:00Z")
    snapshots = [
        make_snapshot("2026-01-01T00:00:00Z"),
        make_snapshot("2026-01-02T00:00:00Z"),
        make_snapshot("2026-01-03T00:00:00Z"),
    ]
    result = rolling_backtest(
        history,
        snapshots,
        [datetime(2026, 1, 3, tzinfo=timezone.utc)],
        tmp_path / "models",
        model_parameters={"iterations": 2, "thread_count": 1},
        calibration_days=1,
    )
    assert result["metrics"]["metric_status"] == "unavailable"
    assert not result["predictions"].empty
    assert result["predictions"]["comparison_eligible"].all()
    assert result["predictions"]["target"].isna().all()
    overall = [
        record
        for record in result["metrics"]["models"]
        if record["turbine_id"] is None and record["lead_hours"] is None
    ]
    assert len(overall) == 4
    assert all(record["n"] == 0 and record["mae"] is None for record in overall)
    saved = json.loads(result["metrics_path"].read_text(encoding="utf-8"))
    assert all(
        record["mae"] is None
        for record in saved["models"]
        if record["turbine_id"] is None and record["lead_hours"] is None
    )
