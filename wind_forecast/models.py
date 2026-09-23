"""Deterministic baselines and a reproducible CatBoost forecast model."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from wind_forecast.contracts import Prediction
from wind_forecast.features import FEATURE_COLUMNS, TURBINE_IDS

DEFAULT_MODEL_PARAMETERS: dict[str, Any] = {
    "iterations": 250,
    "depth": 6,
    "learning_rate": 0.05,
    "random_seed": 42,
    "thread_count": 4,
    "allow_writing_files": False,
    "verbose": False,
}
_QUANTILES = {"p10": 0.1, "p50": 0.5, "p90": 0.9}
_STRATEGIES = ("persistence", "seasonal", "power_curve")
_ARTIFACT_SCHEMA = 1


def _timestamp(value: datetime | pd.Timestamp, name: str) -> pd.Timestamp:
    result = pd.Timestamp(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return result.tz_convert("UTC")


def _valid_history(history: pd.DataFrame, cutoff: datetime) -> pd.DataFrame:
    required = {
        "turbine_id",
        "hour_start",
        "available_at",
        "power",
        "wind_speed",
        "quality_flag",
    }
    missing = required - set(history.columns)
    if missing:
        raise ValueError(f"history is missing columns: {sorted(missing)}")
    cutoff_utc = _timestamp(cutoff, "baseline cutoff")
    result = history.copy()
    if result["hour_start"].dt.tz is None or result["available_at"].dt.tz is None:
        raise ValueError("history timestamps must be timezone-aware")
    result["hour_start"] = result["hour_start"].dt.tz_convert("UTC")
    result["available_at"] = result["available_at"].dt.tz_convert("UTC")
    result["turbine_id"] = result["turbine_id"].astype(str)
    if not set(result["turbine_id"]).issubset(TURBINE_IDS):
        raise ValueError("history contains an unknown turbine_id")
    if result.duplicated(["turbine_id", "hour_start"]).any():
        raise ValueError("history must have one row per turbine and hour")
    result["power"] = pd.to_numeric(result["power"], errors="coerce")
    result["wind_speed"] = pd.to_numeric(result["wind_speed"], errors="coerce")
    valid = result.loc[
        (result["available_at"] <= cutoff_utc)
        & result["quality_flag"].astype(str).isin(("good", "partial"))
        & result["power"].notna()
        & np.isfinite(result["power"])
        & result["power"].between(0, 1)
    ].copy()
    if valid.empty or set(valid["turbine_id"]) != set(TURBINE_IDS):
        missing_turbines = sorted(set(TURBINE_IDS) - set(valid["turbine_id"]))
        raise ValueError(f"missing_turbine_history: {missing_turbines}")
    return valid.sort_values(["turbine_id", "hour_start"], kind="stable")


def _json_value(value: Any) -> Any:
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (pd.Timestamp, datetime, np.datetime64)):
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is None:
            return timestamp.isoformat()
        return timestamp.tz_convert("UTC").isoformat()
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if pd.isna(value):
        return None
    return value


def _canonical_records(frame: pd.DataFrame) -> list[list[Any]]:
    if frame.empty:
        return []
    sort_columns = [
        name
        for name in ("forecast_origin", "turbine_id", "valid_time", "target_end")
        if name in frame.columns
    ]
    canonical = frame.sort_values(sort_columns, kind="stable") if sort_columns else frame
    columns = list(canonical.columns)
    return [[_json_value(value) for value in row] for row in canonical[columns].itertuples(index=False, name=None)]


def _catboost_version() -> str | None:
    try:
        return importlib.metadata.version("catboost")
    except importlib.metadata.PackageNotFoundError:
        return None


def _fingerprint(
    rows: pd.DataFrame,
    calibration: pd.DataFrame,
    parameters: Mapping[str, Any],
    catboost_version: str | None,
) -> str:
    training_cutoff = None
    calibration_cutoff = None
    if "target_end" in rows and not rows.empty:
        training_cutoff = _json_value(rows["target_end"].max())
    if "target_end" in calibration and not calibration.empty:
        calibration_cutoff = _json_value(calibration["target_end"].max())
    payload = {
        "schema": _ARTIFACT_SCHEMA,
        "features": list(FEATURE_COLUMNS),
        "parameters": dict(parameters),
        "catboost_version": catboost_version,
        "training_cutoff": training_cutoff,
        "calibration_cutoff": calibration_cutoff,
        "training_rows": _canonical_records(rows),
        "calibration_rows": _canonical_records(calibration),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _frame_x(frame: pd.DataFrame) -> pd.DataFrame:
    missing = set(FEATURE_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"feature rows are missing predictors: {sorted(missing)}")
    if frame["turbine_id"].isna().any():
        raise ValueError("turbine_id predictor cannot be missing")
    result = frame.loc[:, FEATURE_COLUMNS].copy()
    result["turbine_id"] = result["turbine_id"].astype(str)
    numeric = [column for column in FEATURE_COLUMNS if column != "turbine_id"]
    for column in numeric:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    values = result[numeric].to_numpy(dtype=float, na_value=np.nan)
    if np.isinf(values).any():
        raise ValueError("numeric predictors cannot contain infinity")
    return result


def _calibration_input(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    _frame_x(frame)
    required = {"target"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"calibration rows are missing columns: {sorted(missing)}")
    if "forecast_origin" not in frame or "target_end" not in frame:
        raise ValueError("calibration rows need forecast_origin and target_end metadata")
    return frame


def fit_residual_calibration(
    calibration_predictions: pd.DataFrame, *, minimum_samples: int = 30
) -> dict[str, Any]:
    """Fit residual offsets from out-of-sample ``target`` and ``prediction`` rows.

    The caller must supply predictions made without these targets in model fitting.
    Per-turbine offsets need ``minimum_samples``; otherwise a sufficiently large
    global pool is used. This function never generates in-sample residuals itself.
    """
    if minimum_samples < 1:
        raise ValueError("minimum_samples must be positive")
    needed = {"turbine_id", "target", "prediction"}
    missing = needed - set(calibration_predictions.columns)
    if missing:
        raise ValueError(f"calibration predictions are missing columns: {sorted(missing)}")
    frame = calibration_predictions.copy()
    frame["target"] = pd.to_numeric(frame["target"], errors="coerce")
    frame["prediction"] = pd.to_numeric(frame["prediction"], errors="coerce")
    if np.isinf(frame[["target", "prediction"]].to_numpy(dtype=float, na_value=np.nan)).any():
        raise ValueError("calibration values cannot contain infinity")
    valid = frame.loc[frame["target"].notna() & frame["prediction"].notna()].copy()
    valid["residual"] = valid["target"] - valid["prediction"]
    global_values = valid["residual"].to_numpy(dtype=float)

    def offsets(values: np.ndarray) -> dict[str, float]:
        return {
            "p10": float(np.quantile(values, 0.1)),
            "p90": float(np.quantile(values, 0.9)),
        }

    global_offsets = offsets(global_values) if len(global_values) >= minimum_samples else None
    by_turbine: dict[str, dict[str, float]] = {}
    counts: dict[str, int] = {}
    for turbine_id, group in valid.groupby("turbine_id", sort=True):
        values = group["residual"].to_numpy(dtype=float)
        counts[str(turbine_id)] = int(len(values))
        if len(values) >= minimum_samples:
            by_turbine[str(turbine_id)] = offsets(values)

    return {
        "status": "calibrated" if by_turbine or global_offsets is not None else "unavailable",
        "minimum_samples": minimum_samples,
        "n": int(len(valid)),
        "counts_by_turbine": counts,
        "by_turbine": by_turbine,
        "global": global_offsets,
    }


def _residual_offsets(calibration: Mapping[str, Any], turbine_id: str) -> tuple[float, float] | None:
    per_turbine = calibration.get("by_turbine", {}).get(str(turbine_id))
    selected = per_turbine or calibration.get("global")
    if not selected:
        return None
    return float(selected["p10"]), float(selected["p90"])


def select_baseline(calibration_scores: Mapping[str, Mapping[str, Any]]) -> str:
    """Select the lowest-MAE strategy from explicitly supplied calibration scores.

    ``calibration_scores`` must be computed from historical calibration origins
    that precede the forecast origin. If no candidate has usable scores, return
    persistence as the documented unvalidated fallback.
    """
    ranked: list[tuple[float, int, str]] = []
    for priority, strategy in enumerate(_STRATEGIES):
        stats = calibration_scores.get(strategy)
        if not stats:
            continue
        mae = stats.get("mae")
        n = stats.get("n", 0)
        if mae is None or not isinstance(n, (int, np.integer)) or n <= 0:
            continue
        mae_value = float(mae)
        if math.isfinite(mae_value) and mae_value >= 0:
            ranked.append((mae_value, priority, strategy))
    if not ranked:
        return "persistence"
    return min(ranked)[2]


class BaselineModel:
    """As-of persistence, repeat-last-day seasonal, and empirical power curve."""

    def __init__(self) -> None:
        self.training_cutoff: pd.Timestamp | None = None
        self.last_values: dict[str, float] = {}
        self.daily_profiles: dict[str, dict[int, float]] = {}
        self.power_curves: dict[str, dict[float, float]] = {}
        self.residual_calibrations: dict[str, dict[str, Any]] = {
            strategy: fit_residual_calibration(
                pd.DataFrame(columns=["turbine_id", "target", "prediction"])
            )
            for strategy in _STRATEGIES
        }

    @classmethod
    def fit(cls, history: pd.DataFrame, cutoff: datetime) -> "BaselineModel":
        """Fit baseline state using only observations available at ``cutoff``."""
        model = cls()
        model.training_cutoff = _timestamp(cutoff, "baseline cutoff")
        valid = _valid_history(history, cutoff)
        for turbine_id, group in valid.groupby("turbine_id", sort=False):
            model.last_values[turbine_id] = float(group.iloc[-1]["power"])
            local = group.assign(
                local_date=(group["hour_start"] + pd.Timedelta(hours=5)).dt.date,
                local_hour=(group["hour_start"] + pd.Timedelta(hours=5)).dt.hour,
            )
            latest_date = local["local_date"].max()
            daily = local.loc[local["local_date"] == latest_date]
            model.daily_profiles[turbine_id] = {
                int(row.local_hour): float(row.power) for row in daily.itertuples()
            }

            curve_rows = group.loc[group["wind_speed"].notna() & np.isfinite(group["wind_speed"])]
            if curve_rows.empty:
                model.power_curves[turbine_id] = {}
            else:
                curve_rows = curve_rows.assign(
                    wind_bin=np.floor(curve_rows["wind_speed"].astype(float))
                )
                medians = curve_rows.groupby("wind_bin")["power"].median()
                model.power_curves[turbine_id] = {
                    float(wind_bin): float(power) for wind_bin, power in medians.items()
                }
        return model

    def calibrate(
        self,
        strategy: str,
        calibration_predictions: pd.DataFrame,
        *,
        minimum_samples: int = 30,
    ) -> dict[str, Any]:
        """Attach out-of-sample residuals for a named baseline strategy."""
        if strategy not in _STRATEGIES:
            raise ValueError(f"unknown baseline strategy: {strategy}")
        calibration = fit_residual_calibration(
            calibration_predictions, minimum_samples=minimum_samples
        )
        self.residual_calibrations[strategy] = calibration
        return calibration

    def predict(self, features: pd.DataFrame, strategy: str) -> Prediction:
        """Predict with one named strategy and carry all key/provenance metadata."""
        if strategy not in _STRATEGIES:
            raise ValueError(f"unknown baseline strategy: {strategy}")
        if self.training_cutoff is None:
            raise ValueError("baseline model must be fitted before predict")
        required = {"turbine_id", "valid_time", "lead_hours"}
        missing = required - set(features.columns)
        if missing:
            raise ValueError(f"features are missing prediction keys: {sorted(missing)}")
        if "target" in features.columns:
            raise ValueError("target cannot be supplied to predict")
        frame = features.copy()
        if frame["valid_time"].dt.tz is None:
            raise ValueError("valid_time must be timezone-aware")
        frame["valid_time"] = frame["valid_time"].dt.tz_convert("UTC")
        if "forecast_origin" not in frame:
            raise ValueError("features must carry forecast_origin metadata")
        if frame["forecast_origin"].dt.tz is None:
            raise ValueError("forecast_origin must be timezone-aware")
        origins = frame["forecast_origin"].dt.tz_convert("UTC")
        if (origins < self.training_cutoff).any():
            raise ValueError("baseline training cutoff is after prediction origin")
        if "forecast_mode" in frame:
            competition = frame["forecast_mode"].astype(str).eq("competition")
            if competition.any() and (
                "competition_valid" not in frame
                or not frame.loc[competition, "competition_valid"].fillna(False).all()
            ):
                raise ValueError("competition prediction requires verified weather")

        point: list[float] = []
        fallback_count = 0
        for row in frame.itertuples(index=False):
            turbine_id = str(row.turbine_id)
            fallback = self.last_values.get(turbine_id)
            if fallback is None:
                raise ValueError(f"missing_turbine_history: {turbine_id}")
            if strategy == "persistence":
                value = fallback
            elif strategy == "seasonal":
                local_hour = (pd.Timestamp(row.valid_time) + pd.Timedelta(hours=5)).hour
                value = self.daily_profiles.get(turbine_id, {}).get(local_hour, fallback)
                if local_hour not in self.daily_profiles.get(turbine_id, {}):
                    fallback_count += 1
            else:
                value = self._predict_power_curve(row, turbine_id)
                if value is None:
                    value = fallback
                    fallback_count += 1
            point.append(float(np.clip(value, 0.0, 1.0)))

        result = frame.copy()
        result["p50"] = point
        # Without a pre-origin residual pool these are explicitly demo bounds,
        # not claimed as calibrated uncertainty.
        calibration = self.residual_calibrations[strategy]
        lower: list[float] = []
        upper: list[float] = []
        for value, turbine_id in zip(point, frame["turbine_id"].astype(str)):
            residual_offset = _residual_offsets(calibration, turbine_id)
            if residual_offset is None:
                lower.append(0.0)
                upper.append(1.0)
            else:
                lower.append(float(np.clip(value + residual_offset[0], 0, 1)))
                upper.append(float(np.clip(value + residual_offset[1], 0, 1)))
        result["p10"] = np.minimum(np.asarray(lower), np.asarray(point))
        result["p90"] = np.maximum(np.asarray(upper), np.asarray(point))
        return Prediction(
            rows=result,
            model_name=strategy,
            diagnostics={
                "calibration_status": calibration["status"],
                "uncertainty_method": (
                    "empirical_residual" if calibration["status"] == "calibrated" else "unavailable"
                ),
                "calibration_n": int(calibration.get("n", 0)),
                "fallback_count": fallback_count,
                "measurement_height_mismatch": strategy == "power_curve",
                "training_cutoff": self.training_cutoff.isoformat(),
                "competition_valid": bool(
                    result["competition_valid"].fillna(False).all()
                )
                if "competition_valid" in result
                else False,
            },
        )

    def save(self, path: Path) -> None:
        """Save baseline state as inspectable JSON; no executable pickle is used."""
        if self.training_cutoff is None:
            raise ValueError("baseline model must be fitted before save")
        value = Path(path)
        if value.suffix != ".json":
            value = value.with_name(f"{value.name}.json")
        value.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "artifact_schema": _ARTIFACT_SCHEMA,
            "model_type": "wind_forecast.baseline",
            "training_cutoff": self.training_cutoff.isoformat(),
            "last_values": self.last_values,
            "daily_profiles": self.daily_profiles,
            "power_curves": self.power_curves,
            "residual_calibrations": self.residual_calibrations,
        }
        _atomic_json(value, payload)

    @classmethod
    def load(cls, path: Path) -> "BaselineModel":
        """Load JSON baseline state after validating its schema and numeric values."""
        value = Path(path)
        if value.suffix != ".json":
            value = value.with_name(f"{value.name}.json")
        try:
            payload = json.loads(value.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid baseline metadata: {value}") from exc
        if (
            payload.get("artifact_schema") != _ARTIFACT_SCHEMA
            or payload.get("model_type") != "wind_forecast.baseline"
        ):
            raise ValueError("baseline metadata does not match this model schema")
        model = cls()
        model.training_cutoff = _timestamp(payload["training_cutoff"], "baseline cutoff")
        model.last_values = {str(key): float(val) for key, val in payload["last_values"].items()}
        model.daily_profiles = {
            str(turbine): {int(hour): float(power) for hour, power in profile.items()}
            for turbine, profile in payload["daily_profiles"].items()
        }
        model.power_curves = {
            str(turbine): {float(wind_bin): float(power) for wind_bin, power in curve.items()}
            for turbine, curve in payload["power_curves"].items()
        }
        model.residual_calibrations = payload.get("residual_calibrations") or {
            strategy: fit_residual_calibration(
                pd.DataFrame(columns=["turbine_id", "target", "prediction"])
            )
            for strategy in _STRATEGIES
        }
        if set(model.residual_calibrations) != set(_STRATEGIES):
            raise ValueError("baseline metadata has invalid residual calibration entries")
        if set(model.last_values) != set(TURBINE_IDS):
            raise ValueError("baseline metadata is missing turbine state")
        if any(not math.isfinite(value) or not 0 <= value <= 1 for value in model.last_values.values()):
            raise ValueError("baseline metadata has invalid last power values")
        for profiles in model.daily_profiles.values():
            if any(hour not in range(24) or not math.isfinite(power) or not 0 <= power <= 1 for hour, power in profiles.items()):
                raise ValueError("baseline metadata has an invalid daily profile")
        for curve in model.power_curves.values():
            if any(not math.isfinite(wind) or not math.isfinite(power) or not 0 <= power <= 1 for wind, power in curve.items()):
                raise ValueError("baseline metadata has an invalid power curve")
        return model

    def _predict_power_curve(self, row: Any, turbine_id: str) -> float | None:
        speed = getattr(row, "wind_speed_10m", np.nan)
        if speed is None or pd.isna(speed) or not math.isfinite(float(speed)):
            return None
        curve = self.power_curves.get(turbine_id, {})
        if not curve:
            return None
        x_values = np.array(sorted(curve), dtype=float) + 0.5
        y_values = np.array([curve[x] for x in sorted(curve)], dtype=float)
        return float(np.interp(float(speed), x_values, y_values))


class ForecastModel:
    """Three CPU CatBoost quantile models with held-out residual diagnostics."""

    def __init__(self, **parameters: Any) -> None:
        unknown = set(parameters) - set(DEFAULT_MODEL_PARAMETERS)
        if unknown:
            raise ValueError(f"unsupported CatBoost parameters: {sorted(unknown)}")
        self.parameters = {**DEFAULT_MODEL_PARAMETERS, **parameters}
        if self.parameters["allow_writing_files"] is not False or self.parameters["verbose"] is not False:
            raise ValueError("CatBoost file writing and verbose logging must remain disabled")
        if (
            int(self.parameters["iterations"]) < 1
            or int(self.parameters["depth"]) < 1
            or int(self.parameters["thread_count"]) < 1
        ):
            raise ValueError("iterations, depth, and thread_count must be positive")
        self.models: dict[str, Any] = {}
        self.training_cutoff: pd.Timestamp | None = None
        self.calibration_cutoff: pd.Timestamp | None = None
        self.training_fingerprint: str | None = None
        self.competition_valid = False
        self.training_row_count = 0
        self.calibration = fit_residual_calibration(
            pd.DataFrame(columns=["turbine_id", "target", "prediction"])
        )
        self.catboost_version: str | None = None

    @staticmethod
    def fingerprint_for(
        rows: pd.DataFrame, calibration: pd.DataFrame, parameters: Mapping[str, Any] | None = None
    ) -> str:
        """Return the content fingerprint used for a fit and its local cache key."""
        resolved = {**DEFAULT_MODEL_PARAMETERS, **(parameters or {})}
        return _fingerprint(rows, calibration, resolved, _catboost_version())

    def fit(self, rows: pd.DataFrame, calibration: pd.DataFrame) -> "ForecastModel":
        """Fit quantile models and calibrate only from a later held-out period."""
        if rows.empty:
            raise ValueError("training rows cannot be empty")
        if "target" not in rows or "target_end" not in rows:
            raise ValueError("training rows need target and target_end")
        if "forecast_origin" not in rows or "max_observation_available_at" not in rows:
            raise ValueError("training rows need point-in-time metadata")
        x_train = _frame_x(rows)
        target = pd.to_numeric(rows["target"], errors="coerce").to_numpy(dtype=float)
        if not np.isfinite(target).all() or ((target < 0) | (target > 1)).any():
            raise ValueError("training targets must be finite normalized power in [0, 1]")
        train_origins = pd.to_datetime(rows["forecast_origin"], utc=True)
        max_observation = pd.to_datetime(rows["max_observation_available_at"], utc=True)
        if (max_observation > train_origins).any():
            raise ValueError("training features include observations unavailable at origin")
        self.training_cutoff = _timestamp(pd.to_datetime(rows["target_end"], utc=True).max(), "training cutoff")
        if "competition_valid" in rows:
            self.competition_valid = bool(rows["competition_valid"].fillna(False).all())
        else:
            self.competition_valid = False
        if "forecast_mode" in rows:
            modes = set(rows["forecast_mode"].dropna().astype(str))
            if len(modes) > 1:
                raise ValueError("demo and competition training rows cannot be mixed")
            if modes == {"competition"} and not self.competition_valid:
                raise ValueError("competition training requires verified historical weather")

        calibration = _calibration_input(calibration)
        if not calibration.empty:
            calibration_origins = pd.to_datetime(calibration["forecast_origin"], utc=True)
            if "max_observation_available_at" in calibration:
                calibration_observations = pd.to_datetime(
                    calibration["max_observation_available_at"], utc=True
                )
                if (calibration_observations > calibration_origins).any():
                    raise ValueError("calibration features include observations unavailable at origin")
            train_modes = set(rows["forecast_mode"].dropna().astype(str)) if "forecast_mode" in rows else set()
            calibration_modes = (
                set(calibration["forecast_mode"].dropna().astype(str))
                if "forecast_mode" in calibration
                else set()
            )
            if train_modes and calibration_modes and train_modes != calibration_modes:
                raise ValueError("training and calibration modes cannot be mixed")
            if calibration_modes == {"competition"} and (
                "competition_valid" not in calibration
                or not calibration["competition_valid"].fillna(False).all()
            ):
                raise ValueError("competition calibration requires verified historical weather")
            train_target_end = pd.to_datetime(rows["target_end"], utc=True).max()
            calibration_origins = pd.to_datetime(calibration["forecast_origin"], utc=True)
            calibration_end = pd.to_datetime(calibration["target_end"], utc=True)
            if (calibration_origins < train_target_end).any():
                raise ValueError("calibration origins must follow every training target")
            if (calibration_end < calibration_origins).any():
                raise ValueError("calibration targets must end after their forecast origins")
            overlap = rows[["forecast_origin", "turbine_id", "valid_time"]].merge(
                calibration[["forecast_origin", "turbine_id", "valid_time"]],
                on=["forecast_origin", "turbine_id", "valid_time"],
                how="inner",
            )
            if not overlap.empty:
                raise ValueError("calibration rows overlap training rows")

        try:
            from catboost import CatBoostRegressor
        except ImportError as exc:  # optional ML dependency; baseline mode remains usable
            raise RuntimeError("CatBoost is required to fit ForecastModel") from exc

        self.models = {}
        for name, alpha in _QUANTILES.items():
            model = CatBoostRegressor(
                loss_function=f"Quantile:alpha={alpha}",
                **self.parameters,
            )
            model.fit(x_train, target, cat_features=["turbine_id"])
            self.models[name] = model

        if calibration.empty:
            self.calibration = fit_residual_calibration(
                pd.DataFrame(columns=["turbine_id", "target", "prediction"])
            )
            self.calibration_cutoff = None
        else:
            x_calibration = _frame_x(calibration)
            raw_median = self.models["p50"].predict(x_calibration)
            residuals = calibration[["turbine_id", "target"]].copy()
            residuals["prediction"] = raw_median
            self.calibration = fit_residual_calibration(residuals)
            self.calibration_cutoff = _timestamp(
                pd.to_datetime(calibration["target_end"], utc=True).max(),
                "calibration cutoff",
            )

        self.training_row_count = len(rows)
        self.catboost_version = _catboost_version()
        self.training_fingerprint = _fingerprint(
            rows, calibration, self.parameters, self.catboost_version
        )
        return self

    def predict(self, features: pd.DataFrame) -> Prediction:
        """Predict from CatBoost quantiles, preserving request keys and provenance.

        Raw quantiles are sorted per row to correct crossings, then clipped to
        normalized power bounds. Held-out residual calibration is retained as
        audit metadata; CatBoost interval bounds come from the three quantile
        models and do not silently switch to residual offsets.
        """
        if not self.models or self.training_fingerprint is None:
            raise ValueError("ForecastModel must be fitted or loaded before predict")
        required = {"turbine_id", "valid_time", "lead_hours"}
        missing = required - set(features.columns)
        if missing:
            raise ValueError(f"features are missing prediction keys: {sorted(missing)}")
        if "target" in features.columns:
            raise ValueError("target cannot be supplied to predict")
        if "forecast_origin" not in features:
            raise ValueError("features must carry forecast_origin metadata")
        if features["forecast_origin"].dt.tz is None:
            raise ValueError("forecast_origin must be timezone-aware")
        prediction_origins = features["forecast_origin"].dt.tz_convert("UTC")
        if self.training_cutoff is not None and (
            prediction_origins < self.training_cutoff
        ).any():
            raise ValueError("model training cutoff is after prediction origin")
        if self.calibration_cutoff is not None and (
            prediction_origins < self.calibration_cutoff
        ).any():
            raise ValueError("model calibration cutoff is after prediction origin")
        if "forecast_mode" in features:
            competition = features["forecast_mode"].astype(str).eq("competition")
            if competition.any():
                if not self.competition_valid:
                    raise ValueError("competition model was trained with non-competition data")
                if (
                    "competition_valid" not in features
                    or not features.loc[competition, "competition_valid"].fillna(False).all()
                ):
                    raise ValueError("competition prediction requires verified weather")
        x = _frame_x(features)
        raw = {
            name: np.asarray(model.predict(x), dtype=float).reshape(-1)
            for name, model in self.models.items()
        }
        if any(len(values) != len(features) for values in raw.values()):
            raise ValueError("CatBoost returned a prediction with the wrong row count")
        if any(not np.isfinite(values).all() for values in raw.values()):
            raise ValueError("CatBoost returned a nonfinite prediction")

        raw_quantiles = np.column_stack([raw[name] for name in _QUANTILES])
        crossing = np.any(raw_quantiles[:, :-1] > raw_quantiles[:, 1:], axis=1)
        ordered_quantiles = np.sort(raw_quantiles, axis=1)
        clipping_count = int(
            ((ordered_quantiles < 0.0) | (ordered_quantiles > 1.0)).sum()
        )
        quantiles = np.clip(ordered_quantiles, 0.0, 1.0)
        result = features.copy()
        for index, name in enumerate(_QUANTILES):
            result[name] = quantiles[:, index]
        return Prediction(
            rows=result,
            model_name="catboost_quantile",
            diagnostics={
                "training_fingerprint": self.training_fingerprint,
                "training_cutoff": self.training_cutoff.isoformat()
                if self.training_cutoff is not None
                else None,
                "calibration_cutoff": self.calibration_cutoff.isoformat()
                if self.calibration_cutoff is not None
                else None,
                "calibration_status": self.calibration.get("status", "unavailable"),
                "uncertainty_method": "catboost_quantiles",
                "calibration_n": int(self.calibration.get("n", 0)),
                "calibration_used_for_intervals": False,
                "quantile_crossing_correction_count": int(crossing.sum()),
                "quantile_crossing_policy": "sort_per_row_then_clip",
                "quantile_clipping_count": clipping_count,
                "competition_valid": self.competition_valid,
            },
        )

    def save(self, path: Path) -> None:
        """Persist local CatBoost files and JSON metadata using a prefix path."""
        if not self.models or self.training_fingerprint is None:
            raise ValueError("ForecastModel must be fitted before save")
        prefix = _artifact_prefix(path)
        prefix.parent.mkdir(parents=True, exist_ok=True)
        model_files = {name: f"{prefix.name}.{name}.cbm" for name in _QUANTILES}
        hashes: dict[str, str] = {}
        temporary_files: dict[str, Path] = {}
        try:
            for name, filename in model_files.items():
                fd, temp_name = tempfile.mkstemp(prefix=f".{filename}.", dir=prefix.parent)
                os.close(fd)
                temp_path = Path(temp_name)
                self.models[name].save_model(str(temp_path), format="cbm")
                temporary_files[filename] = temp_path
                hashes[filename] = _sha256_file(temp_path)
            for filename, temp_path in temporary_files.items():
                os.replace(temp_path, prefix.parent / filename)
            metadata = {
                "artifact_schema": _ARTIFACT_SCHEMA,
                "model_type": "wind_forecast.catboost_quantile",
                "feature_columns": list(FEATURE_COLUMNS),
                "parameters": self.parameters,
                "quantiles": _QUANTILES,
                "model_files": model_files,
                "sha256": hashes,
                "training_fingerprint": self.training_fingerprint,
                "training_cutoff": self.training_cutoff.isoformat()
                if self.training_cutoff is not None
                else None,
                "calibration_cutoff": self.calibration_cutoff.isoformat()
                if self.calibration_cutoff is not None
                else None,
                "training_row_count": self.training_row_count,
                "calibration": self.calibration,
                "competition_valid": self.competition_valid,
                "catboost_version": self.catboost_version,
            }
            _atomic_json(_metadata_path(prefix), metadata)
        finally:
            for temp_path in temporary_files.values():
                temp_path.unlink(missing_ok=True)

    @classmethod
    def load(cls, path: Path) -> "ForecastModel":
        """Load only complete local artifacts with matching schema and file hashes."""
        prefix = _artifact_prefix(path)
        metadata_path = _metadata_path(prefix)
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid forecast model metadata: {metadata_path}") from exc
        if (
            metadata.get("artifact_schema") != _ARTIFACT_SCHEMA
            or metadata.get("model_type") != "wind_forecast.catboost_quantile"
            or metadata.get("feature_columns") != list(FEATURE_COLUMNS)
            or metadata.get("quantiles") != _QUANTILES
        ):
            raise ValueError("forecast model metadata does not match this model schema")
        if _catboost_version() != metadata.get("catboost_version"):
            raise ValueError("CatBoost version does not match saved model metadata")
        model_files = metadata.get("model_files")
        hashes = metadata.get("sha256")
        if not isinstance(model_files, dict) or set(model_files) != set(_QUANTILES):
            raise ValueError("forecast model metadata has invalid model file entries")
        if not isinstance(hashes, dict):
            raise ValueError("forecast model metadata has invalid hashes")
        for name, filename in model_files.items():
            if Path(filename).name != filename or filename != f"{prefix.name}.{name}.cbm":
                raise ValueError("forecast model metadata points outside its local prefix")
            model_path = prefix.parent / filename
            if not model_path.is_file() or _sha256_file(model_path) != hashes.get(filename):
                raise ValueError(f"forecast model file failed integrity check: {filename}")
        try:
            from catboost import CatBoostRegressor
        except ImportError as exc:
            raise RuntimeError("CatBoost is required to load ForecastModel") from exc

        model = cls(**metadata["parameters"])
        for name in _QUANTILES:
            loaded = CatBoostRegressor()
            loaded.load_model(str(prefix.parent / model_files[name]), format="cbm")
            model.models[name] = loaded
        model.training_fingerprint = str(metadata["training_fingerprint"])
        model.training_cutoff = (
            _timestamp(metadata["training_cutoff"], "training cutoff")
            if metadata.get("training_cutoff")
            else None
        )
        model.calibration_cutoff = (
            _timestamp(metadata["calibration_cutoff"], "calibration cutoff")
            if metadata.get("calibration_cutoff")
            else None
        )
        model.training_row_count = int(metadata.get("training_row_count", 0))
        model.calibration = metadata.get("calibration") or fit_residual_calibration(
            pd.DataFrame(columns=["turbine_id", "target", "prediction"])
        )
        model.competition_valid = bool(metadata.get("competition_valid", False))
        model.catboost_version = metadata.get("catboost_version")
        return model


def _artifact_prefix(path: Path) -> Path:
    value = Path(path)
    if value.suffix in {".json", ".cbm"}:
        return value.with_suffix("")
    return value


def _metadata_path(prefix: Path) -> Path:
    return prefix.parent / f"{prefix.name}.json"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        Path(temp_name).unlink(missing_ok=True)
