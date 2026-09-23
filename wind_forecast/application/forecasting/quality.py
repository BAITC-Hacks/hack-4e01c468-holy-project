"""Pure quality gates for forecast outputs."""

from __future__ import annotations

import numpy as np
import pandas as pd

from wind_forecast.application.forecasting.weather_policy import validate_weather
from wind_forecast.contracts import Prediction, RunRequest, WeatherSnapshot

_QUANTILES = ("p10", "p50", "p90")
_TURBINES = ("turbine_1", "turbine_2")


def quality_gate(
    prediction: Prediction,
    snapshot: WeatherSnapshot,
    request: RunRequest,
) -> Prediction:
    """Validate weather and exact forecast coverage, then count safe corrections."""
    validate_weather(snapshot, request)
    if not isinstance(prediction, Prediction) or not isinstance(prediction.rows, pd.DataFrame):
        raise ValueError("prediction_schema_invalid")

    required = {"turbine_id", "valid_time", *_QUANTILES}
    missing = required - set(prediction.rows.columns)
    if missing:
        raise ValueError("prediction_schema_missing_columns")

    rows = prediction.rows.copy(deep=True)
    if "lead_hours" in rows.columns:
        expected_leads = set(range(request.horizon))
        if not set(rows["lead_hours"].dropna().tolist()).issubset(expected_leads):
            raise ValueError("prediction_lead_out_of_range")

    try:
        times = pd.to_datetime(rows["valid_time"], utc=True, errors="raise")
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("prediction_valid_time_invalid") from exc
    rows["valid_time"] = times
    keys = ["turbine_id", "valid_time"]
    if rows.duplicated(keys, keep=False).any():
        raise ValueError("prediction_duplicate_key")
    if "lead_hours" in rows.columns:
        try:
            leads = pd.to_numeric(rows["lead_hours"], errors="raise").to_numpy(dtype=float)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("prediction_lead_invalid") from exc
        expected_leads = ((times - pd.Timestamp(request.origin)) / pd.Timedelta(hours=1)).to_numpy(dtype=float)
        if not np.isfinite(leads).all() or not np.array_equal(leads, expected_leads):
            raise ValueError("prediction_lead_mismatch")

    expected_times = pd.date_range(
        pd.Timestamp(request.origin).tz_convert("UTC"), periods=request.horizon, freq="h"
    )
    if "lead_hours" not in rows.columns:
        rows["lead_hours"] = (
            (times - pd.Timestamp(request.origin)) / pd.Timedelta(hours=1)
        ).astype(int)
    expected = {(turbine, stamp) for turbine in _TURBINES for stamp in expected_times}
    actual = set(zip(rows["turbine_id"], rows["valid_time"], strict=True))
    if actual != expected:
        raise ValueError("prediction_grid_incomplete")
    if len(rows) != 2 * request.horizon:
        raise ValueError("prediction_grid_incomplete")

    try:
        numeric = rows.loc[:, list(_QUANTILES)].apply(pd.to_numeric, errors="raise").astype(float)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("prediction_quantile_invalid") from exc
    values = numeric.to_numpy(dtype=float, copy=True)
    if not np.isfinite(values).all():
        raise ValueError("prediction_nonfinite_quantile")

    crossings = 0
    for index, row in enumerate(values):
        ordered = np.sort(row)
        if not np.array_equal(row, ordered):
            crossings += 1
            values[index] = ordered
    before_clip = values.copy()
    clipped = np.clip(values, 0.0, 1.0)
    clipping_count = int(np.count_nonzero(before_clip != clipped))
    rows.loc[:, list(_QUANTILES)] = clipped
    rows = rows.sort_values(keys, kind="stable").reset_index(drop=True)

    diagnostics = dict(prediction.diagnostics or {})
    diagnostics["quality_gate"] = {
        "quantile_correction_count": crossings,
        "clipping_count": clipping_count,
        "prediction_rows": len(rows),
        "weather_validated": True,
    }
    return Prediction(rows=rows, model_name=prediction.model_name, diagnostics=diagnostics)
