from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pandas.testing as pdt
import pytest

from wind_forecast.contracts import RunRequest, WeatherSnapshot


def make_history(
    start: str = "2025-12-01T00:00:00Z",
    end: str = "2026-01-03T00:00:00Z",
) -> pd.DataFrame:
    hours = pd.date_range(start, end, freq="h", inclusive="left")
    rows = []
    for turbine_id, offset in (("turbine_1", 0.0), ("turbine_2", 0.1)):
        for i, hour in enumerate(hours):
            rows.append(
                {
                    "turbine_id": turbine_id,
                    "hour_start": hour,
                    "available_at": hour + pd.Timedelta(hours=1),
                    "power": min(1.0, 0.4 + offset + (i % 5) * 0.01),
                    "wind_speed": 7.0 + offset,
                    "temperature": 5.0,
                    "observation_count": 6,
                    "quality_flag": "good",
                }
            )
    return pd.DataFrame(rows)


def make_snapshot(
    origin: str = "2026-01-01T00:00:00Z", horizon: int = 48, *, status: str = "synthetic"
) -> tuple[RunRequest, WeatherSnapshot]:
    origin_ts = pd.Timestamp(origin)
    request = RunRequest(
        origin=origin_ts.to_pydatetime(), horizon=horizon, mode="demo"
    )
    rows = []
    for turbine_id in ("turbine_1", "turbine_2"):
        for lead, valid_time in enumerate(pd.date_range(origin_ts, periods=horizon, freq="h")):
            rows.append(
                {
                    "turbine_id": turbine_id,
                    "valid_time": valid_time,
                    "initialized_at": origin_ts - pd.Timedelta(hours=6),
                    "issued_at": origin_ts - pd.Timedelta(hours=5),
                    "available_at": origin_ts - pd.Timedelta(hours=4),
                    "lead_hours": lead,
                    "weather_model": "synthetic-test",
                    "wind_speed_10m": 6.0 + lead / 10,
                    "wind_speed_100m": 8.0 + lead / 10,
                    "wind_direction_10m": 180.0,
                    "wind_gusts_10m": 9.0,
                    "temperature_2m": 10.0,
                    "surface_pressure": 1000.0,
                }
            )
    return request, WeatherSnapshot(
        rows=pd.DataFrame(rows),
        raw_responses=[],
        fingerprint=f"weather-{origin}",
        provenance={
            "provenance_status": status,
            "competition_valid": status == "verified",
            "weather_model": "synthetic-test",
            "initialized_at": (origin_ts - pd.Timedelta(hours=6)).isoformat(),
            "issued_at": (origin_ts - pd.Timedelta(hours=5)).isoformat(),
            "available_at": (origin_ts - pd.Timedelta(hours=4)).isoformat(),
            "units": {
                "wind_speed_unit": "ms",
                "wind_direction": "degrees",
                "temperature": "°C",
                "surface_pressure": "hPa",
            },
        },
    )


@pytest.fixture
def history() -> pd.DataFrame:
    return make_history()


@pytest.fixture
def snapshots() -> list[tuple[RunRequest, WeatherSnapshot]]:
    return [make_snapshot("2026-01-01T00:00:00Z"), make_snapshot("2026-01-02T00:00:00Z")]


@pytest.fixture
def cutoff() -> datetime:
    return datetime(2026, 1, 3, tzinfo=timezone.utc)


def test_build_ignores_future_observations_and_origin_hour(history: pd.DataFrame) -> None:
    from wind_forecast.features import FeaturePipeline

    request, weather = make_snapshot()
    origin = pd.Timestamp(request.origin)
    base = FeaturePipeline().build(history, weather, request)
    poisoned = history.copy()
    poisoned.loc[
        (poisoned["turbine_id"] == "turbine_1")
        & (poisoned["hour_start"] == origin),
        "power",
    ] = 0.99
    changed = FeaturePipeline().build(poisoned, weather, request)
    pdt.assert_frame_equal(base, changed)
    assert (changed["max_observation_available_at"] <= origin).all()


def test_training_labels_available_by_cutoff(
    history: pd.DataFrame,
    snapshots: list[tuple[RunRequest, WeatherSnapshot]],
    cutoff: datetime,
) -> None:
    from wind_forecast.features import FEATURE_COLUMNS, training_rows

    rows = training_rows(history, snapshots, cutoff)
    assert len(rows) > 0
    assert (rows["target_end"] <= pd.Timestamp(cutoff)).all()
    assert (rows["max_observation_available_at"] <= rows["forecast_origin"]).all()
    assert "target" not in FEATURE_COLUMNS
    assert "forecast_origin" not in FEATURE_COLUMNS
    assert "valid_time" not in FEATURE_COLUMNS


def test_day_week_lags_do_not_read_future_slots(history: pd.DataFrame) -> None:
    from wind_forecast.features import FeaturePipeline

    request, weather = make_snapshot(horizon=48)
    features = FeaturePipeline().build(history, weather, request)
    first_turbine = features.loc[features["turbine_id"] == "turbine_1"].set_index(
        "lead_hours"
    )
    # At lead 24, valid_time - 24h is exactly the origin hour, unavailable until 01:00.
    assert np.isnan(first_turbine.loc[24, "lag_24"])
    assert first_turbine.loc[24, "lag_24_missing"] == 1
    # At lead 47, valid_time - 24h is after the origin and must still be missing.
    assert np.isnan(first_turbine.loc[47, "lag_24"])
    assert first_turbine.loc[47, "lag_24_missing"] == 1


def test_short_history_and_missing_turbine_are_explicit(history: pd.DataFrame) -> None:
    from wind_forecast.features import FeaturePipeline

    request, weather = make_snapshot()
    one_hour = history.loc[
        history["hour_start"] == pd.Timestamp("2025-12-31T23:00:00Z")
    ].copy()
    features = FeaturePipeline().build(one_hour, weather, request)
    row = features.loc[features["turbine_id"] == "turbine_1"].iloc[0]
    assert row["power_count_168"] == 1
    assert row["power_std_168"] == 0.0
    with pytest.raises(ValueError, match="missing_turbine_history"):
        FeaturePipeline().build(
            one_hour.loc[one_hour["turbine_id"] == "turbine_1"], weather, request
        )


def test_february_features_show_stale_observations_without_backfill(
    history: pd.DataFrame,
) -> None:
    from wind_forecast.features import FeaturePipeline

    request, weather = make_snapshot("2026-02-03T00:00:00Z")
    features = FeaturePipeline().build(history, weather, request)
    row = features.loc[
        (features["turbine_id"] == "turbine_1") & (features["lead_hours"] == 0)
    ].iloc[0]
    assert row["observation_age_hours"] >= 48
    assert row["last_power"] == history.loc[
        history["turbine_id"] == "turbine_1", "power"
    ].iloc[-1]
    assert row["lag_24_missing"] == 1
    assert row["lag_168_missing"] == 1


def test_competition_mode_rejects_unverified_weather(history: pd.DataFrame) -> None:
    from wind_forecast.features import FeaturePipeline

    _, snapshot = make_snapshot()
    competition_request = RunRequest(
        origin=datetime(2026, 1, 1, tzinfo=timezone.utc), mode="competition"
    )
    with pytest.raises(ValueError, match="unverified_weather_provenance"):
        FeaturePipeline().build(history, snapshot, competition_request)


def test_verified_weather_is_marked_competition_valid(history: pd.DataFrame) -> None:
    from wind_forecast.features import FeaturePipeline

    demo_request, snapshot = make_snapshot(status="verified")
    competition_request = RunRequest(
        origin=demo_request.origin, horizon=demo_request.horizon, mode="competition"
    )
    features = FeaturePipeline().build(history, snapshot, competition_request)
    assert features["competition_valid"].all()


def test_offline_weather_provider_snapshot_is_demo_only(history: pd.DataFrame) -> None:
    from wind_forecast.features import FeaturePipeline
    from wind_forecast.weather import make_synthetic_weather_snapshot

    request = RunRequest(
        origin=datetime(2026, 1, 1, tzinfo=timezone.utc), mode="demo"
    )
    snapshot = make_synthetic_weather_snapshot(request)
    features = FeaturePipeline().build(history, snapshot, request)
    assert len(features) == 96
    assert set(features["provenance_status"]) == {"synthetic"}
    assert not features["competition_valid"].any()
