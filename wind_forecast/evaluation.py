"""Forecast scoring and leakage-safe rolling-origin evaluation."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from wind_forecast.contracts import Prediction, RunRequest, WeatherSnapshot
from wind_forecast.features import FeaturePipeline, training_rows
from wind_forecast.models import (
    DEFAULT_MODEL_PARAMETERS,
    BaselineModel,
    ForecastModel,
    _metadata_path,
    fit_residual_calibration,
    select_baseline,
)

_MODELS = ("catboost_quantile", "persistence", "seasonal", "power_curve")


def _vector(values: Sequence[float] | np.ndarray | pd.Series, name: str) -> np.ndarray:
    try:
        result = np.asarray(values, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a one-dimensional numeric sequence") from exc
    if result.ndim != 1:
        raise ValueError(f"{name} must be a one-dimensional numeric sequence")
    return result


def score(
    y: Sequence[float] | np.ndarray | pd.Series,
    p50: Sequence[float] | np.ndarray | pd.Series,
    p10: Sequence[float] | np.ndarray | pd.Series | None = None,
    p90: Sequence[float] | np.ndarray | pd.Series | None = None,
) -> dict[str, float | int | None]:
    """Calculate point and interval metrics, excluding only missing targets.

    Predictions must be finite for every requested row. A target NaN is missing
    and excluded pairwise; infinite targets and malformed predictions are errors.
    ``coverage`` is the share of requested rows with a finite observed target.
    """
    truth = _vector(y, "y")
    median = _vector(p50, "p50")
    if len(truth) != len(median):
        raise ValueError("y and p50 must have the same length")
    if not np.isfinite(median).all():
        raise ValueError("p50 predictions must all be finite")
    if np.isinf(truth).any():
        raise ValueError("truth values cannot be infinite")
    if (p10 is None) != (p90 is None):
        raise ValueError("p10 and p90 must be provided together")

    lower: np.ndarray | None = None
    upper: np.ndarray | None = None
    if p10 is not None and p90 is not None:
        lower = _vector(p10, "p10")
        upper = _vector(p90, "p90")
        if len(lower) != len(truth) or len(upper) != len(truth):
            raise ValueError("p10 and p90 must have the same length as y")
        if not np.isfinite(lower).all() or not np.isfinite(upper).all():
            raise ValueError("p10 and p90 predictions must all be finite")

    valid = np.isfinite(truth)
    count = int(valid.sum())
    coverage = float(count / len(truth)) if len(truth) else 0.0
    if count == 0:
        return {
            "n": 0,
            "coverage": coverage,
            "mae": None,
            "rmse": None,
            "smape": None,
            "bias": None,
            "interval_coverage": None if lower is None else None,
        }

    errors = median[valid] - truth[valid]
    denominator = np.abs(median[valid]) + np.abs(truth[valid])
    smape_terms = np.zeros(count, dtype=float)
    nonzero = denominator > 0
    smape_terms[nonzero] = 200.0 * np.abs(errors[nonzero]) / denominator[nonzero]
    interval_coverage = None
    if lower is not None and upper is not None:
        interval_coverage = float(
            np.mean((truth[valid] >= lower[valid]) & (truth[valid] <= upper[valid]))
        )
    return {
        "n": count,
        "coverage": coverage,
        "mae": float(np.mean(np.abs(errors))),
        "rmse": float(np.sqrt(np.mean(errors**2))),
        "smape": float(np.mean(smape_terms)),
        "bias": float(np.mean(errors)),
        "interval_coverage": interval_coverage,
    }


def apply_residual_calibration(
    prediction: pd.DataFrame,
    calibration_predictions: pd.DataFrame,
    *,
    point_column: str = "p50",
    minimum_samples: int = 30,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Apply out-of-sample residual offsets to point forecasts.

    Calibration rows require ``turbine_id``, ``target``, and ``prediction``;
    prediction rows require ``turbine_id`` and ``point_column``. Missing evidence
    produces explicit ``[0, 1]`` bounds with status ``unavailable``.
    """
    needed = {"turbine_id", point_column}
    missing = needed - set(prediction.columns)
    if missing:
        raise ValueError(f"prediction rows are missing columns: {sorted(missing)}")
    calibration = fit_residual_calibration(
        calibration_predictions, minimum_samples=minimum_samples
    )
    result = prediction.copy()
    points = pd.to_numeric(result[point_column], errors="coerce").to_numpy(dtype=float)
    if not np.isfinite(points).all():
        raise ValueError("point predictions must be finite")
    lower: list[float] = []
    upper: list[float] = []
    for point, turbine_id in zip(points, result["turbine_id"].astype(str)):
        residuals = calibration.get("by_turbine", {}).get(str(turbine_id)) or calibration.get(
            "global"
        )
        if residuals is None:
            lower.append(0.0)
            upper.append(1.0)
        else:
            lower.append(float(np.clip(point + residuals["p10"], 0, 1)))
            upper.append(float(np.clip(point + residuals["p90"], 0, 1)))
    result["p10"] = np.minimum(np.asarray(lower), points)
    result["p50"] = points
    result["p90"] = np.maximum(np.asarray(upper), points)
    return result, calibration


def _origin(value: datetime | pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("backtest origins must be timezone-aware")
    timestamp = timestamp.tz_convert("UTC")
    if timestamp.minute or timestamp.second or timestamp.microsecond or timestamp.nanosecond:
        raise ValueError("backtest origins must be hour boundaries")
    return timestamp


def _valid_truth(history: pd.DataFrame) -> pd.DataFrame:
    required = {"turbine_id", "hour_start", "available_at", "power", "quality_flag"}
    missing = required - set(history.columns)
    if missing:
        raise ValueError(f"history is missing columns: {sorted(missing)}")
    truth = history.loc[
        history["quality_flag"].astype(str).isin(("good", "partial"))
        & history["power"].notna()
        & np.isfinite(pd.to_numeric(history["power"], errors="coerce"))
        & pd.to_numeric(history["power"], errors="coerce").between(0, 1)
    ].copy()
    if truth["hour_start"].dt.tz is None or truth["available_at"].dt.tz is None:
        raise ValueError("history truth timestamps must be timezone-aware")
    truth["valid_time"] = truth["hour_start"].dt.tz_convert("UTC")
    truth["target"] = pd.to_numeric(truth["power"], errors="coerce")
    truth["target_end"] = truth["valid_time"] + pd.Timedelta(hours=1)
    return truth[["turbine_id", "valid_time", "target", "target_end"]]


def _score_records(frame: pd.DataFrame, model_name: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    groups: list[tuple[Any, Any, pd.DataFrame]] = [(None, None, frame)]
    groups.extend(
        (turbine_id, None, group)
        for turbine_id, group in frame.groupby("turbine_id", sort=True)
    )
    groups.extend(
        (None, int(lead), group)
        for lead, group in frame.groupby("lead_hours", sort=True)
    )
    for turbine_id, lead_hours, group in groups:
        metrics = score(group["target"], group["p50"], group["p10"], group["p90"])
        records.append(
            {
                "model": model_name,
                "turbine_id": turbine_id,
                "lead_hours": lead_hours,
                **metrics,
            }
        )
    return records


def _catboost_failure(exc: Exception) -> bool:
    return isinstance(exc, (ImportError, RuntimeError)) or (
        type(exc).__name__ == "CatBoostError"
        or type(exc).__module__.startswith("catboost")
    )


def _json_safe(value: Any) -> Any:
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (pd.Timestamp, datetime, np.datetime64)):
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is not None:
            return timestamp.tz_convert("UTC").isoformat()
        return timestamp.isoformat()
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if pd.isna(value):
        return None
    return value


def _calibration_baseline_scores(
    history: pd.DataFrame,
    calibration_snapshots: list[tuple[RunRequest, WeatherSnapshot]],
    evaluation_cutoff: pd.Timestamp,
) -> tuple[dict[str, dict[str, Any]], dict[str, pd.DataFrame]]:
    """Score and retain residuals only from pre-evaluation calibration origins."""
    actual = _valid_truth(history)
    collected: dict[str, list[pd.DataFrame]] = {name: [] for name in _MODELS[1:]}
    pipeline = FeaturePipeline()
    for request, snapshot in calibration_snapshots:
        features = pipeline.build(history, snapshot, request)
        labels = actual.loc[actual["target_end"] <= evaluation_cutoff]
        features = features.merge(
            labels[["turbine_id", "valid_time", "target"]],
            on=["turbine_id", "valid_time"],
            how="left",
            validate="one_to_one",
        )
        baseline = BaselineModel.fit(history, request.origin)
        for strategy in _MODELS[1:]:
            predicted = baseline.predict(features.drop(columns=["target"]), strategy)
            subset = predicted.rows[["turbine_id", "valid_time", "p50"]].copy()
            subset["target"] = features["target"].to_numpy()
            collected[strategy].append(subset)
    scores: dict[str, dict[str, Any]] = {}
    residuals_by_strategy: dict[str, pd.DataFrame] = {}
    for strategy, frames in collected.items():
        if not frames:
            scores[strategy] = {"mae": None, "n": 0}
            residuals_by_strategy[strategy] = pd.DataFrame(
                columns=["turbine_id", "target", "prediction"]
            )
            continue
        data = pd.concat(frames, ignore_index=True)
        metrics = score(data["target"], data["p50"])
        scores[strategy] = {"mae": metrics["mae"], "n": metrics["n"]}
        residuals_by_strategy[strategy] = data[["turbine_id", "target", "p50"]].rename(
            columns={"p50": "prediction"}
        )
    return scores, residuals_by_strategy


def rolling_backtest(
    history: pd.DataFrame,
    snapshots: list[tuple[RunRequest, WeatherSnapshot]],
    origins: list[datetime],
    model_dir: Path,
    *,
    model_parameters: Mapping[str, Any] | None = None,
    calibration_days: int = 15,
) -> dict[str, Any]:
    """Run daily 48-hour folds, persist common-key scores, and cache fold models.

    ``model_parameters`` permits a short CPU test fit; production defaults remain
    250 iterations, depth 6, seed 42, and four threads. Each fold uses historical
    snapshots before its origin; the latest ``calibration_days`` form its held-out
    residual/selection period and are excluded from that fold's CatBoost fitting.
    """
    if calibration_days < 1:
        raise ValueError("calibration_days must be positive")
    parameters = {**DEFAULT_MODEL_PARAMETERS, **(model_parameters or {})}
    unknown = set(parameters) - set(DEFAULT_MODEL_PARAMETERS)
    if unknown:
        raise ValueError(f"unsupported CatBoost parameters: {sorted(unknown)}")
    normalized_origins = [_origin(value) for value in origins]
    if len(set(normalized_origins)) != len(normalized_origins):
        raise ValueError("backtest origins must be unique")
    normalized_origins.sort()

    snapshot_by_origin: dict[pd.Timestamp, tuple[RunRequest, WeatherSnapshot]] = {}
    for request, snapshot in snapshots:
        key = _origin(request.origin)
        if key in snapshot_by_origin:
            raise ValueError("snapshots contain duplicate forecast origins")
        snapshot_by_origin[key] = (request, snapshot)

    model_dir = Path(model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    actual = _valid_truth(history)
    fold_failures: list[dict[str, Any]] = []
    selection_rows: list[dict[str, Any]] = []
    prediction_frames: list[pd.DataFrame] = []
    eligible_fold_count = 0
    pipeline = FeaturePipeline()

    for fold_origin in normalized_origins:
        pair = snapshot_by_origin.get(fold_origin)
        if pair is None:
            fold_failures.append(
                {"forecast_origin": fold_origin.isoformat(), "reason": "missing_weather_snapshot"}
            )
            continue
        request, snapshot = pair
        if request.horizon != 48:
            raise ValueError("rolling_backtest requires 48-hour snapshot requests")
        fold_features = pipeline.build(history, snapshot, request)
        previous = [
            pair
            for snapshot_origin, pair in snapshot_by_origin.items()
            if snapshot_origin < fold_origin
        ]
        previous.sort(key=lambda item: _origin(item[0].origin))
        calibration_start = fold_origin - pd.Timedelta(days=calibration_days)
        calibration_snapshots = [
            pair
            for pair in previous
            if _origin(pair[0].origin) >= calibration_start
        ]
        if calibration_snapshots:
            train_cutoff = min(_origin(item[0].origin) for item in calibration_snapshots)
            train_snapshots = [
                pair
                for pair in previous
                if _origin(pair[0].origin) < train_cutoff
            ]
            fit_rows = training_rows(history, train_snapshots, train_cutoff.to_pydatetime())
            calibration_rows = training_rows(
                history, calibration_snapshots, fold_origin.to_pydatetime()
            )
        else:
            train_cutoff = fold_origin
            train_snapshots = previous
            fit_rows = training_rows(history, train_snapshots, train_cutoff.to_pydatetime())
            calibration_rows = pd.DataFrame(columns=fit_rows.columns)

        calibration_scores, baseline_residuals = _calibration_baseline_scores(
            history, calibration_snapshots, fold_origin
        )
        baseline_calibration_states = {
            strategy: fit_residual_calibration(residuals)
            for strategy, residuals in baseline_residuals.items()
        }
        selected_baseline = select_baseline(calibration_scores)
        selection_rows.append(
            {
                "forecast_origin": fold_origin,
                "selected_baseline": selected_baseline,
                "calibration_scores": calibration_scores,
                "residual_calibration": baseline_calibration_states,
                "calibration_origins": len(calibration_snapshots),
            }
        )

        fold_predictions: list[pd.DataFrame] = []
        baseline_model = BaselineModel.fit(history, fold_origin.to_pydatetime())
        for strategy in _MODELS[1:]:
            baseline_model.calibrate(strategy, baseline_residuals[strategy])
            prediction = baseline_model.predict(fold_features, strategy)
            rows = prediction.rows
            rows["model"] = strategy
            fold_predictions.append(rows)

        catboost_error: Exception | None = None
        if fit_rows.empty:
            catboost_error = RuntimeError("no leakage-safe CatBoost training rows")
        else:
            candidate = ForecastModel(**parameters)
            fingerprint = candidate.fingerprint_for(fit_rows, calibration_rows, parameters)
            prefix = model_dir / (
                f"fold-{fold_origin.strftime('%Y%m%dT%H')}-{fingerprint[:12]}"
            )
            model: ForecastModel | None = None
            metadata_path = _metadata_path(prefix)
            if metadata_path.is_file():
                try:
                    cached = ForecastModel.load(prefix)
                    if cached.training_fingerprint == fingerprint:
                        model = cached
                except (ValueError, OSError, RuntimeError):
                    # An invalid cache is not used; fit again from this fold's exact rows.
                    model = None
            try:
                if model is None:
                    model = candidate.fit(fit_rows, calibration_rows)
                    model.save(prefix)
                cat_prediction = model.predict(fold_features)
                cat_rows = cat_prediction.rows.copy()
                cat_rows["model"] = "catboost_quantile"
                fold_predictions.append(cat_rows)
            except Exception as exc:
                if not _catboost_failure(exc):
                    raise
                catboost_error = exc

        if catboost_error is not None:
            fold_failures.append(
                {
                    "forecast_origin": fold_origin.isoformat(),
                    "reason": "catboost_failed",
                    "error_type": type(catboost_error).__name__,
                    "message": str(catboost_error),
                }
            )
            eligible = False
        else:
            eligible = True
            eligible_fold_count += 1

        labels = actual[["turbine_id", "valid_time", "target"]]
        for rows in fold_predictions:
            joined = rows.merge(
                labels,
                on=["turbine_id", "valid_time"],
                how="left",
                validate="one_to_one",
            )
            joined["comparison_eligible"] = eligible
            prediction_frames.append(joined)

    pred_columns = [
        "model",
        "forecast_origin",
        "turbine_id",
        "valid_time",
        "lead_hours",
        "target",
        "target_end",
        "p10",
        "p50",
        "p90",
        "comparison_eligible",
    ]
    predictions = (
        pd.concat(prediction_frames, ignore_index=True)
        if prediction_frames
        else pd.DataFrame(columns=pred_columns)
    )
    eligible_predictions = predictions.loc[
        predictions["comparison_eligible"].fillna(False)
    ]
    model_metrics: list[dict[str, Any]] = []
    for model_name in _MODELS:
        group = eligible_predictions.loc[eligible_predictions["model"] == model_name]
        model_metrics.extend(_score_records(group, model_name))

    requested_target_rows = len(normalized_origins) * 2 * 48
    available_truth = int(
        predictions.loc[
            predictions["comparison_eligible"].fillna(False), "target"
        ].notna().sum()
        / max(1, len(_MODELS))
    )
    metrics_document: dict[str, Any] = {
        "schema_version": 1,
        "metric_status": (
            "available"
            if eligible_predictions["target"].notna().any()
            else "unavailable"
        ),
        "evaluation_period": {
            "start": normalized_origins[0].isoformat() if normalized_origins else None,
            "end": (normalized_origins[-1] + pd.Timedelta(hours=48)).isoformat()
            if normalized_origins
            else None,
        },
        "models": model_metrics,
        "coverage": {
            "requested_origins": len(normalized_origins),
            "evaluated_origins": eligible_fold_count,
            "failed_folds": fold_failures,
            "requested_target_rows": requested_target_rows,
            "available_common_target_rows": available_truth,
            "model_rows_by_name": {
                name: int(
                    predictions.loc[
                        (predictions["model"] == name)
                        & predictions["comparison_eligible"].fillna(False)
                    ].shape[0]
                )
                for name in _MODELS
            },
        },
        "baseline_selection": selection_rows,
    }

    output_dir = model_dir.parent / "backtest"
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = output_dir / "metrics.json"
    predictions_path = output_dir / "predictions.csv"
    metrics_path.write_text(
        json.dumps(_json_safe(metrics_document), indent=2, sort_keys=True, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    predictions.loc[:, [column for column in pred_columns if column in predictions]].to_csv(
        predictions_path, index=False
    )
    return {
        "metrics": metrics_document,
        "predictions": predictions,
        "metrics_path": metrics_path,
        "predictions_path": predictions_path,
    }
