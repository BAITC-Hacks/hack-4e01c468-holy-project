from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from tests.test_features import make_history, make_snapshot
from wind_forecast.contracts import Prediction, RunRequest
from wind_forecast.features import FEATURE_COLUMNS, FeaturePipeline, training_rows
from wind_forecast.models import (
    BaselineModel,
    ForecastModel,
    fit_residual_calibration,
    select_baseline,
)


def _model_rows(
    count: int, origin: str, *, target_offset: float = 0.0
) -> pd.DataFrame:
    forecast_origin = pd.Timestamp(origin)
    rows = []
    for i in range(count):
        turbine_id = "turbine_1" if i % 2 == 0 else "turbine_2"
        valid_time = forecast_origin + pd.Timedelta(hours=i % 24)
        row = {
            column: 0.5 for column in FEATURE_COLUMNS if column != "turbine_id"
        }
        row.update(
            {
                "turbine_id": turbine_id,
                "lead_hours": float(i % 48),
                "last_power": 0.25 + (i % 10) * 0.02,
                "wind_speed_10m": 4.0 + i % 10,
                "hour_sin": np.sin(i),
                "forecast_origin": forecast_origin,
                "valid_time": valid_time,
                "target_end": valid_time + pd.Timedelta(hours=1),
                "max_observation_available_at": forecast_origin,
                "forecast_mode": "demo",
                "competition_valid": False,
                "target": float(np.clip(0.2 + (i % 10) * 0.05 + target_offset, 0, 1)),
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


def test_persistence_and_seasonal_repeat_last_day() -> None:
    history = make_history()
    request, weather = make_snapshot("2026-01-02T00:00:00Z")
    features = FeaturePipeline().build(history, weather, request)
    model = BaselineModel.fit(history, request.origin)

    persistence = model.predict(features, "persistence")
    for turbine_id in ("turbine_1", "turbine_2"):
        actual_last = features.loc[features["turbine_id"] == turbine_id, "last_power"].iloc[0]
        predicted = persistence.rows.loc[
            persistence.rows["turbine_id"] == turbine_id, "p50"
        ]
        np.testing.assert_allclose(predicted.to_numpy(), actual_last)
    assert set(persistence.rows["p10"]) == {0.0}
    assert set(persistence.rows["p90"]) == {1.0}
    assert persistence.diagnostics["calibration_status"] == "unavailable"

    seasonal = model.predict(features, "seasonal")
    for turbine_id in ("turbine_1", "turbine_2"):
        points = seasonal.rows.loc[
            seasonal.rows["turbine_id"] == turbine_id
        ].sort_values("lead_hours")["p50"].to_numpy()
        np.testing.assert_allclose(points[:24], points[24:])


def test_baseline_keeps_its_as_of_cutoff_when_predicting_later() -> None:
    history = make_history()
    cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
    request, weather = make_snapshot("2026-01-02T00:00:00Z")
    features = FeaturePipeline().build(history, weather, request)
    model = BaselineModel.fit(history, cutoff)
    prediction = model.predict(features, "persistence")
    for turbine_id in ("turbine_1", "turbine_2"):
        expected = model.last_values[turbine_id]
        values = prediction.rows.loc[
            prediction.rows["turbine_id"] == turbine_id, "p50"
        ]
        np.testing.assert_allclose(values.to_numpy(), expected)


def test_baseline_json_round_trip(tmp_path) -> None:
    history = make_history()
    request, weather = make_snapshot("2026-01-02T00:00:00Z")
    features = FeaturePipeline().build(history, weather, request)
    fitted = BaselineModel.fit(history, request.origin)
    residuals = pd.DataFrame(
        {
            "turbine_id": ["turbine_1", "turbine_2"] * 20,
            "target": np.linspace(0.1, 0.9, 40),
            "prediction": np.full(40, 0.5),
        }
    )
    fitted.calibrate("power_curve", residuals)
    path = tmp_path / "baseline-state"
    fitted.save(path)
    loaded = BaselineModel.load(path)
    expected = fitted.predict(features, "power_curve").rows
    actual = loaded.predict(features, "power_curve").rows
    np.testing.assert_allclose(expected["p50"], actual["p50"])
    np.testing.assert_allclose(expected["p10"], actual["p10"])
    assert loaded.predict(features, "power_curve").diagnostics["calibration_status"] == "calibrated"


def test_power_curve_is_cutoff_safe_and_uses_wind_10m() -> None:
    cutoff = pd.Timestamp("2026-01-01T04:00:00Z")
    rows = []
    values = [(0, 0.2, 0.1), (1, 0.4, 0.3), (2, 1.3, 0.5), (3, 1.7, 0.7)]
    for turbine_id in ("turbine_1", "turbine_2"):
        for hour, wind, power in values:
            start = pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(hours=hour)
            rows.append(
                {
                    "turbine_id": turbine_id,
                    "hour_start": start,
                    "available_at": start + pd.Timedelta(hours=1),
                    "power": power,
                    "wind_speed": wind,
                    "quality_flag": "good",
                }
            )
    history = pd.DataFrame(rows)
    # This later point would move the high wind bins if cutoff enforcement slipped.
    future_start = pd.Timestamp("2026-01-01T05:00:00Z")
    future = pd.DataFrame(
        [
            {
                "turbine_id": turbine_id,
                "hour_start": future_start,
                "available_at": future_start + pd.Timedelta(hours=1),
                "power": 1.0,
                "wind_speed": 1.2,
                "quality_flag": "good",
            }
            for turbine_id in ("turbine_1", "turbine_2")
        ]
    )
    model = BaselineModel.fit(pd.concat([history, future]), cutoff.to_pydatetime())
    features = pd.DataFrame(
        {
            "turbine_id": ["turbine_1", "turbine_2"],
            "valid_time": [cutoff, cutoff],
            "forecast_origin": [cutoff, cutoff],
            "lead_hours": [0, 0],
            "wind_speed_10m": [1.0, 1.0],
            "last_power": [0.7, 0.7],
            "forecast_mode": ["demo", "demo"],
            "competition_valid": [False, False],
        }
    )
    prediction = model.predict(features, "power_curve")
    np.testing.assert_allclose(prediction.rows["p50"], [0.4, 0.4])
    assert prediction.diagnostics["measurement_height_mismatch"] is True


def test_residual_calibration_and_baseline_selection_use_historical_scores() -> None:
    residual_rows = pd.DataFrame(
        {
            "turbine_id": ["turbine_1"] * 20 + ["turbine_2"] * 20,
            "target": np.linspace(0.1, 0.9, 40),
            "prediction": np.linspace(0.2, 0.8, 40),
        }
    )
    calibration = fit_residual_calibration(residual_rows)
    assert calibration["status"] == "calibrated"
    assert calibration["global"] is not None
    assert calibration["by_turbine"] == {}

    scores = {
        "persistence": {"mae": 0.2, "n": 40},
        "seasonal": {"mae": 0.1, "n": 40},
        "power_curve": {"mae": 0.1, "n": 40},
    }
    assert select_baseline(scores) == "seasonal"
    assert select_baseline({}) == "persistence"


def test_catboost_fit_predict_save_load_and_calibration(tmp_path) -> None:
    pytest.importorskip("catboost")
    training = _model_rows(64, "2026-01-01T00:00:00Z")
    calibration = _model_rows(40, "2026-01-03T00:00:00Z", target_offset=0.03)
    model = ForecastModel(iterations=30, thread_count=1).fit(training, calibration)
    request_rows = _model_rows(40, "2026-01-04T00:00:00Z").drop(columns=["target"])
    first = model.predict(request_rows)
    assert len(first.rows) == len(calibration)
    assert set((first.rows["p10"] <= first.rows["p50"]) & (first.rows["p50"] <= first.rows["p90"])) == {
        True
    }
    raw_quantiles = np.column_stack(
        [model.models[name].predict(request_rows.loc[:, FEATURE_COLUMNS]) for name in ("p10", "p50", "p90")]
    )
    expected_quantiles = np.clip(np.sort(raw_quantiles, axis=1), 0.0, 1.0)
    np.testing.assert_allclose(
        first.rows[["p10", "p50", "p90"]].to_numpy(), expected_quantiles
    )
    assert first.diagnostics["calibration_status"] == "calibrated"
    assert first.diagnostics["uncertainty_method"] == "catboost_quantiles"
    assert first.diagnostics["calibration_used_for_intervals"] is False
    assert first.diagnostics["competition_valid"] is False

    path = tmp_path / "tiny-model"
    model.save(path)
    loaded = ForecastModel.load(path)
    second = loaded.predict(request_rows)
    np.testing.assert_allclose(first.rows["p50"], second.rows["p50"], rtol=1e-6, atol=1e-6)
    np.testing.assert_allclose(first.rows["p10"], second.rows["p10"], rtol=1e-6, atol=1e-6)
    assert loaded.training_fingerprint == model.training_fingerprint

    with pytest.raises(ValueError, match="training cutoff is after prediction origin"):
        model.predict(training.drop(columns=["target"]))

    # A calibration origin before training labels finish is rejected before fit.
    too_early = _model_rows(32, "2026-01-01T00:00:00Z")
    with pytest.raises(ValueError, match="calibration origins must follow"):
        ForecastModel(iterations=1, thread_count=1).fit(training, too_early)


def test_catboost_rejects_predictions_before_calibration_cutoff() -> None:
    pytest.importorskip("catboost")
    training = _model_rows(64, "2026-01-01T00:00:00Z")
    calibration = _model_rows(40, "2026-01-03T00:00:00Z", target_offset=0.03)
    model = ForecastModel(iterations=2, thread_count=1).fit(training, calibration)
    before_calibration = _model_rows(2, "2026-01-02T00:00:00Z").drop(
        columns=["target"]
    )

    with pytest.raises(ValueError, match="calibration cutoff is after prediction origin"):
        model.predict(before_calibration)


def test_catboost_sorts_crossing_quantiles_and_clips_out_of_range_values() -> None:
    pytest.importorskip("catboost")
    training = _model_rows(32, "2026-01-01T00:00:00Z")
    model = ForecastModel(iterations=2, thread_count=1).fit(
        training, training.iloc[0:0].copy()
    )

    class FixedPredictions:
        def __init__(self, values: list[float]) -> None:
            self.values = np.asarray(values, dtype=float)

        def predict(self, features: pd.DataFrame) -> np.ndarray:
            assert len(features) == len(self.values)
            return self.values

    model.models = {
        "p10": FixedPredictions([0.8, -0.2, 0.4]),
        "p50": FixedPredictions([0.2, 0.4, 0.5]),
        "p90": FixedPredictions([0.6, 1.2, 0.5]),
    }
    features = _model_rows(3, "2026-01-03T00:00:00Z").drop(columns=["target"])

    prediction = model.predict(features)

    np.testing.assert_allclose(
        prediction.rows[["p10", "p50", "p90"]].to_numpy(),
        [[0.2, 0.6, 0.8], [0.0, 0.4, 1.0], [0.4, 0.5, 0.5]],
    )
    assert prediction.diagnostics["quantile_crossing_correction_count"] == 1
    assert prediction.diagnostics["quantile_crossing_policy"] == "sort_per_row_then_clip"
    assert prediction.diagnostics["quantile_clipping_count"] == 2


def test_catboost_rejects_nonfinite_raw_quantiles() -> None:
    pytest.importorskip("catboost")
    training = _model_rows(32, "2026-01-01T00:00:00Z")
    model = ForecastModel(iterations=2, thread_count=1).fit(
        training, training.iloc[0:0].copy()
    )

    class FixedPredictions:
        def __init__(self, value: float) -> None:
            self.value = value

        def predict(self, features: pd.DataFrame) -> np.ndarray:
            return np.full(len(features), self.value)

    model.models = {
        "p10": FixedPredictions(0.2),
        "p50": FixedPredictions(np.inf),
        "p90": FixedPredictions(0.8),
    }
    features = _model_rows(1, "2026-01-03T00:00:00Z").drop(columns=["target"])

    with pytest.raises(ValueError, match="CatBoost returned a nonfinite prediction"):
        model.predict(features)


def test_competition_prediction_rejects_demo_trained_model(tmp_path) -> None:
    pytest.importorskip("catboost")
    training = _model_rows(32, "2026-01-01T00:00:00Z")
    model = ForecastModel(iterations=2, thread_count=1).fit(
        training, training.iloc[0:0].copy()
    )
    competition = _model_rows(2, "2026-01-03T00:00:00Z").drop(columns=["target"])
    competition["forecast_mode"] = "competition"
    competition["competition_valid"] = True
    with pytest.raises(ValueError, match="trained with non-competition"):
        model.predict(competition)


def test_training_fingerprint_ignores_future_labels() -> None:
    history = make_history(end="2026-01-05T00:00:00Z")
    snapshots = [make_snapshot("2026-01-01T00:00:00Z"), make_snapshot("2026-01-02T00:00:00Z")]
    cutoff = datetime(2026, 1, 2, tzinfo=timezone.utc)
    rows = training_rows(history, snapshots, cutoff)
    poisoned = history.copy()
    poisoned.loc[poisoned["hour_start"] >= pd.Timestamp(cutoff), "power"] = 0.99
    poisoned_rows = training_rows(poisoned, snapshots, cutoff)
    assert ForecastModel.fingerprint_for(rows, rows.iloc[0:0]) == ForecastModel.fingerprint_for(
        poisoned_rows, poisoned_rows.iloc[0:0]
    )
