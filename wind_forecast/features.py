"""Point-in-time-safe feature construction for turbine forecasts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from wind_forecast.application.forecasting.weather_policy import validate_weather
from wind_forecast.contracts import RunRequest, WeatherSnapshot

TURBINE_IDS: tuple[str, str] = ("turbine_1", "turbine_2")
WEATHER_COLUMNS: tuple[str, ...] = (
    "wind_speed_10m",
    "wind_speed_100m",
    "wind_direction_10m",
    "wind_gusts_10m",
    "temperature_2m",
    "surface_pressure",
)
FEATURE_COLUMNS: tuple[str, ...] = (
    "turbine_id",
    "lead_hours",
    *WEATHER_COLUMNS,
    "hour_sin",
    "hour_cos",
    "month_sin",
    "month_cos",
    "weekday_sin",
    "weekday_cos",
    "last_power",
    "last_wind_speed",
    "power_mean_6",
    "power_std_6",
    "power_count_6",
    "power_mean_24",
    "power_std_24",
    "power_count_24",
    "power_mean_168",
    "power_std_168",
    "power_count_168",
    "lag_24",
    "lag_24_missing",
    "lag_168",
    "lag_168_missing",
    "observation_age_hours",
)

_HOUR = pd.Timedelta(hours=1)
_ALMATY_OFFSET = pd.Timedelta(hours=5)
_VALID_FLAGS = {"good", "partial"}
_HISTORY_COLUMNS = {
    "turbine_id",
    "hour_start",
    "available_at",
    "power",
    "wind_speed",
    "quality_flag",
}
_WEATHER_REQUIRED = {"turbine_id", "valid_time", "lead_hours", *WEATHER_COLUMNS}


def _utc_timestamp(value: datetime | pd.Timestamp, name: str) -> pd.Timestamp:
    """Return a UTC timestamp while refusing timezone-naive inputs."""
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return timestamp.tz_convert("UTC")


def _utc_series(values: pd.Series, name: str, *, allow_missing: bool = False) -> pd.Series:
    """Normalize a timestamp column without silently interpreting naive times."""
    if pd.api.types.is_datetime64_any_dtype(values.dtype):
        if values.dt.tz is None:
            if allow_missing and values.isna().all():
                return values.copy()
            raise ValueError(f"{name} must be timezone-aware")
        return values.dt.tz_convert("UTC")

    converted: list[pd.Timestamp | pd.NaT] = []
    for value in values:
        if pd.isna(value):
            if not allow_missing:
                raise ValueError(f"{name} cannot contain missing timestamps")
            converted.append(pd.NaT)
            continue
        converted.append(_utc_timestamp(value, name))
    return pd.Series(pd.array(converted, dtype="datetime64[ns, UTC]"), index=values.index)


def _validate_request(request: RunRequest) -> pd.Timestamp:
    origin = _utc_timestamp(request.origin, "forecast origin")
    if origin.minute or origin.second or origin.microsecond or origin.nanosecond:
        raise ValueError("forecast origin must be an hour boundary")
    if request.horizon not in (24, 48):
        raise ValueError("forecast horizon must be 24 or 48 hours")
    if request.mode not in ("competition", "demo"):
        raise ValueError("forecast mode must be 'competition' or 'demo'")
    return origin


def _validated_history(history: pd.DataFrame) -> pd.DataFrame:
    missing = _HISTORY_COLUMNS - set(history.columns)
    if missing:
        raise ValueError(f"history is missing columns: {sorted(missing)}")
    result = history.copy()
    if result["turbine_id"].isna().any() or not set(result["turbine_id"].astype(str)).issubset(
        TURBINE_IDS
    ):
        raise ValueError("history contains an unknown turbine_id")
    result["turbine_id"] = result["turbine_id"].astype(str)
    result["hour_start"] = _utc_series(result["hour_start"], "history.hour_start")
    result["available_at"] = _utc_series(result["available_at"], "history.available_at")
    if result.duplicated(["turbine_id", "hour_start"]).any():
        raise ValueError("history must have one row per turbine and hour")
    if (result["available_at"] < result["hour_start"] + _HOUR).any():
        raise ValueError("history.available_at cannot precede the end of its hour")
    result["power"] = pd.to_numeric(result["power"], errors="coerce")
    result["wind_speed"] = pd.to_numeric(result["wind_speed"], errors="coerce")
    return result


def _validated_weather(
    weather: WeatherSnapshot, request: RunRequest, origin: pd.Timestamp
) -> tuple[pd.DataFrame, str, bool]:
    # T3 owns the complete provider/provenance contract. Apply it before any
    # feature/label join, then retain the local normalized view used below.
    validate_weather(weather, request)
    rows = weather.rows.copy()
    missing = _WEATHER_REQUIRED - set(rows.columns)
    if missing:
        raise ValueError(f"weather is missing columns: {sorted(missing)}")
    if rows["turbine_id"].isna().any():
        raise ValueError("weather contains a missing turbine_id")
    rows["turbine_id"] = rows["turbine_id"].astype(str)
    if not set(rows["turbine_id"]).issubset(TURBINE_IDS):
        raise ValueError("weather contains an unknown turbine_id")
    rows["valid_time"] = _utc_series(rows["valid_time"], "weather.valid_time")

    expected_times = pd.date_range(origin, periods=request.horizon, freq="h", tz="UTC")
    expected = pd.MultiIndex.from_product(
        [TURBINE_IDS, expected_times], names=["turbine_id", "valid_time"]
    )
    actual = pd.MultiIndex.from_frame(rows[["turbine_id", "valid_time"]])
    if rows.duplicated(["turbine_id", "valid_time"]).any():
        raise ValueError("weather must have one row per turbine and valid_time")
    if len(actual) != len(expected) or not actual.sort_values().equals(expected.sort_values()):
        raise ValueError("weather must cover both turbines at every requested lead hour")
    supplied_leads = pd.to_numeric(rows["lead_hours"], errors="coerce")
    expected_leads = (rows["valid_time"] - origin).dt.total_seconds() / 3600
    if (
        supplied_leads.isna().any()
        or not np.isfinite(supplied_leads.to_numpy(dtype=float)).all()
        or not np.array_equal(supplied_leads.to_numpy(dtype=float), expected_leads.to_numpy(dtype=float))
    ):
        raise ValueError("weather lead_hours do not match the requested origin and valid_time")
    rows["lead_hours"] = supplied_leads.astype("int64")

    for column in WEATHER_COLUMNS:
        rows[column] = pd.to_numeric(rows[column], errors="coerce")
        values = rows[column].to_numpy(dtype=float, na_value=np.nan)
        if np.isinf(values).any():
            raise ValueError(f"weather.{column} contains an infinite value")

    provenance = weather.provenance or {}
    status = str(
        provenance.get("provenance_status")
        or provenance.get("status")
        or "unknown"
    ).lower()
    row_statuses: set[str] = set()
    if "provenance_status" in rows:
        row_statuses = set(rows["provenance_status"].dropna().astype(str).str.lower())
        if len(row_statuses) > 1:
            status = "mixed"
        elif row_statuses:
            status = next(iter(row_statuses))

    issued_column = "issued_at" if "issued_at" in rows else None
    available_column = "available_at" if "available_at" in rows else None
    issued_values: pd.Series | None = None
    available_values: pd.Series | None = None
    if issued_column:
        rows["weather_issued_at"] = _utc_series(
            rows[issued_column], "weather.issued_at", allow_missing=True
        )
        issued_values = rows["weather_issued_at"]
    else:
        rows["weather_issued_at"] = pd.NaT
    if available_column:
        rows["weather_available_at"] = _utc_series(
            rows[available_column], "weather.available_at", allow_missing=True
        )
        available_values = rows["weather_available_at"]
    else:
        rows["weather_available_at"] = pd.NaT

    competition_valid = (
        status == "verified"
        and bool(provenance.get("competition_valid", False))
        and issued_values is not None
        and available_values is not None
        and issued_values.notna().all()
        and available_values.notna().all()
        and (issued_values <= origin).all()
        and (available_values <= origin).all()
    )
    if request.mode == "competition" and not competition_valid:
        raise ValueError("competition mode requires verified weather available by origin")
    if available_values is not None and available_values.notna().any():
        if (available_values.dropna() > origin).any():
            raise ValueError("weather was not available by forecast origin")
    if issued_values is not None and issued_values.notna().any():
        if (issued_values.dropna() > origin).any():
            raise ValueError("weather was issued after forecast origin")

    if "weather_model" not in rows:
        rows["weather_model"] = provenance.get("weather_model")
    return rows, status, bool(competition_valid)


def _valid_observations(history: pd.DataFrame, origin: pd.Timestamp) -> pd.DataFrame:
    known = history.loc[
        (history["available_at"] <= origin)
        & history["quality_flag"].astype(str).isin(_VALID_FLAGS)
        & history["power"].notna()
        & np.isfinite(history["power"])
        & history["power"].between(0.0, 1.0)
    ].copy()
    return known.sort_values(["turbine_id", "hour_start"], kind="stable")


def _features_for_turbine(
    observations: pd.DataFrame, turbine_id: str, target_times: pd.Series
) -> pd.DataFrame:
    turbine = observations.loc[observations["turbine_id"] == turbine_id]
    if turbine.empty:
        raise ValueError(f"missing_turbine_history: {turbine_id}")

    powers = turbine["power"].astype(float)
    last = turbine.iloc[-1]
    feature_rows: list[dict[str, float | int]] = []
    by_time = turbine.set_index("hour_start", drop=False)
    for valid_time in target_times:
        features: dict[str, float | int] = {
            "last_power": float(last["power"]),
            "last_wind_speed": (
                float(last["wind_speed"]) if pd.notna(last["wind_speed"]) else np.nan
            ),
        }
        for window in (6, 24, 168):
            sample = powers.tail(window)
            features[f"power_mean_{window}"] = float(sample.mean())
            features[f"power_std_{window}"] = float(sample.std(ddof=0))
            features[f"power_count_{window}"] = int(sample.count())

        for hours in (24, 168):
            lag_time = valid_time - pd.Timedelta(hours=hours)
            if lag_time in by_time.index:
                lag_row = by_time.loc[lag_time]
                # The input has one row per key, but preserve a defensive guard here.
                if isinstance(lag_row, pd.DataFrame):
                    raise ValueError("history must have unique turbine/hour keys")
                lag_value = float(lag_row["power"])
                features[f"lag_{hours}"] = lag_value
                features[f"lag_{hours}_missing"] = 0
            else:
                features[f"lag_{hours}"] = np.nan
                features[f"lag_{hours}_missing"] = 1

        features["observation_age_hours"] = max(
            0.0,
            (valid_time - pd.Timestamp(last["available_at"])).total_seconds() / 3600,
        )
        feature_rows.append(features)
    return pd.DataFrame(feature_rows)


class FeaturePipeline:
    """Build a complete forecast grid from as-of observations and weather."""

    def build(
        self, history: pd.DataFrame, weather: WeatherSnapshot, request: RunRequest
    ) -> pd.DataFrame:
        """Return predictor columns plus timestamp, key, and provenance metadata."""
        origin = _validate_request(request)
        source = _validated_history(history)
        weather_rows, provenance_status, competition_valid = _validated_weather(
            weather, request, origin
        )
        known = _valid_observations(source, origin)
        if known.empty:
            raise ValueError("missing_turbine_history: no valid observations available")

        expected_times = pd.date_range(origin, periods=request.horizon, freq="h", tz="UTC")
        weather_rows = weather_rows.sort_values(["turbine_id", "valid_time"], kind="stable")
        frames: list[pd.DataFrame] = []
        for turbine_id in TURBINE_IDS:
            turbine_weather = weather_rows.loc[
                weather_rows["turbine_id"] == turbine_id
            ].copy()
            turbine_weather = turbine_weather.reset_index(drop=True)
            computed = _features_for_turbine(known, turbine_id, pd.Series(expected_times))
            frames.append(pd.concat([turbine_weather, computed], axis=1))

        result = pd.concat(frames, ignore_index=True)
        result = result.rename(columns={"valid_time": "valid_time"})
        result["forecast_origin"] = origin
        result["target_end"] = result["valid_time"] + _HOUR
        result["lead_hours"] = (
            (result["valid_time"] - origin).dt.total_seconds() / 3600
        ).astype("int64")
        result["forecast_mode"] = request.mode
        result["provenance_status"] = provenance_status
        result["competition_valid"] = competition_valid
        latest_available = known.groupby("turbine_id")["available_at"].max()
        result["max_observation_available_at"] = result["turbine_id"].map(
            latest_available
        )

        local_time = result["valid_time"] + _ALMATY_OFFSET
        hour = local_time.dt.hour
        month = local_time.dt.month - 1
        weekday = local_time.dt.weekday
        result["hour_sin"] = np.sin(2 * np.pi * hour / 24)
        result["hour_cos"] = np.cos(2 * np.pi * hour / 24)
        result["month_sin"] = np.sin(2 * np.pi * month / 12)
        result["month_cos"] = np.cos(2 * np.pi * month / 12)
        result["weekday_sin"] = np.sin(2 * np.pi * weekday / 7)
        result["weekday_cos"] = np.cos(2 * np.pi * weekday / 7)

        rows_per_turbine = result.groupby("turbine_id").size().to_dict()
        if rows_per_turbine != {"turbine_1": request.horizon, "turbine_2": request.horizon}:
            raise ValueError("feature grid must have exactly the requested horizon per turbine")
        if result.duplicated(["turbine_id", "valid_time"]).any():
            raise ValueError("feature grid contains duplicate turbine/valid_time keys")
        numeric_features = [column for column in FEATURE_COLUMNS if column != "turbine_id"]
        numeric = result[numeric_features].to_numpy(dtype=float, na_value=np.nan)
        if np.isinf(numeric).any():
            raise ValueError("feature predictors contain infinite values")
        return result.sort_values(["turbine_id", "valid_time"], kind="stable").reset_index(
            drop=True
        )


def _valid_targets(history: pd.DataFrame) -> pd.DataFrame:
    targets = history.loc[
        history["quality_flag"].astype(str).isin(_VALID_FLAGS)
        & history["power"].notna()
        & np.isfinite(history["power"])
        & history["power"].between(0.0, 1.0)
    ].copy()
    targets["target_end"] = targets["hour_start"] + _HOUR
    return targets[
        ["turbine_id", "hour_start", "target_end", "available_at", "power"]
    ].rename(
        columns={
            "hour_start": "valid_time",
            "available_at": "target_available_at",
            "power": "target",
        }
    )


def training_rows(
    history: pd.DataFrame,
    snapshots: list[tuple[RunRequest, WeatherSnapshot]],
    cutoff: datetime,
) -> pd.DataFrame:
    """Build historical feature/target pairs whose hourly labels ended by cutoff."""
    cutoff_utc = _utc_timestamp(cutoff, "training cutoff")
    source = _validated_history(history)
    targets = _valid_targets(source)
    eligible_snapshots: list[tuple[RunRequest, WeatherSnapshot]] = []
    for request, snapshot in snapshots:
        origin = _validate_request(request)
        if origin < cutoff_utc:
            eligible_snapshots.append((request, snapshot))

    pipeline = FeaturePipeline()
    frames: list[pd.DataFrame] = []
    for request, snapshot in eligible_snapshots:
        features = pipeline.build(source, snapshot, request)
        joined = features.merge(
            targets,
            on=["turbine_id", "valid_time"],
            how="inner",
            validate="one_to_one",
            suffixes=("", "_label"),
        )
        # The label's hour must have completed by cutoff; feature timestamps are
        # tied to the snapshot origin and cannot be advanced by label joins.
        joined = joined.loc[joined["target_end_label"] <= cutoff_utc].copy()
        joined = joined.loc[joined["target_available_at"] <= cutoff_utc].copy()
        joined = joined.rename(columns={"target_end_label": "label_available_at"})
        joined["target_end"] = joined["valid_time"] + _HOUR
        joined = joined.drop(columns=["label_available_at", "target_available_at"])
        if not joined.empty:
            frames.append(joined)

    columns = [
        *FEATURE_COLUMNS,
        "forecast_origin",
        "valid_time",
        "target_end",
        "max_observation_available_at",
        "weather_issued_at",
        "weather_model",
        "provenance_status",
        "competition_valid",
        "forecast_mode",
        "target",
    ]
    if not frames:
        return pd.DataFrame(columns=columns)
    result = pd.concat(frames, ignore_index=True)
    if (result["target_end"] > cutoff_utc).any():
        raise AssertionError("training_rows leaked a target beyond cutoff")
    if (result["max_observation_available_at"] > result["forecast_origin"]).any():
        raise AssertionError("training_rows used observations unavailable at the origin")
    if result.duplicated(["forecast_origin", "turbine_id", "valid_time"]).any():
        raise ValueError("training snapshots contain duplicate origin/turbine/time keys")
    return result.loc[:, columns].sort_values(
        ["forecast_origin", "turbine_id", "valid_time"], kind="stable"
    ).reset_index(drop=True)
