"""Weather tests use fake transports only; no test contacts Open-Meteo."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from wind_forecast.contracts import WeatherSnapshot, parse_request
from wind_forecast.weather import (
    OPEN_METEO_SINGLE_RUNS_URL,
    WEATHER_COLUMNS,
    WEATHER_VARIABLES,
    WeatherProvider,
    make_synthetic_weather_snapshot,
    validate_weather,
    weather_fingerprint,
    write_synthetic_weather_fixture,
)


def _request(
    horizon: int = 48,
    mode: str = "demo",
    origin: str = "2026-02-01T00:00:00+05:00",
):
    return parse_request(origin, horizon, mode)  # type: ignore[arg-type]


def _verified_snapshot(
    request: Any | None = None,
    *,
    provenance_status: str = "verified",
) -> WeatherSnapshot:
    request = request or _request(mode="competition")
    snapshot = make_synthetic_weather_snapshot(request)
    origin = pd.Timestamp(request.origin)
    snapshot.rows["weather_model"] = "ecmwf_ifs"
    snapshot.rows["initialized_at"] = origin - pd.Timedelta(hours=6)
    snapshot.rows["issued_at"] = origin - pd.Timedelta(hours=2)
    snapshot.rows["available_at"] = origin - pd.Timedelta(hours=2)
    snapshot.provenance.update(
        {
            "weather_model": "ecmwf_ifs",
            "provenance_status": provenance_status,
            "competition_valid": provenance_status == "verified",
            "initialized_at": (origin - pd.Timedelta(hours=6)).isoformat(),
            "issued_at": (origin - pd.Timedelta(hours=2)).isoformat(),
            "available_at": (origin - pd.Timedelta(hours=2)).isoformat(),
        }
    )
    snapshot.fingerprint = weather_fingerprint(snapshot.rows, snapshot.provenance)
    return snapshot


def _open_meteo_payload(run: str = "2026-01-31T06:00") -> list[dict[str, Any]]:
    start = pd.Timestamp(f"{run}:00Z")
    times = [value.strftime("%Y-%m-%dT%H:%M") for value in pd.date_range(start, periods=72, freq="h")]
    units = {
        "wind_speed_10m": "m/s",
        "wind_speed_100m": "m/s",
        "wind_direction_10m": "°",
        "wind_gusts_10m": "m/s",
        "temperature_2m": "°C",
        "surface_pressure": "hPa",
    }
    results = []
    for index in range(2):
        hourly: dict[str, Any] = {"time": times}
        for variable in WEATHER_VARIABLES:
            values: list[float | None] = [5.0 + index] * len(times)
            if variable == "wind_gusts_10m":
                values[0] = None  # The requested origin is later and complete.
            if variable == "wind_direction_10m":
                values = [205.0 + index] * len(times)
            if variable == "temperature_2m":
                values = [-2.0 + index] * len(times)
            if variable == "surface_pressure":
                values = [1013.0 + index] * len(times)
            hourly[variable] = values
        results.append(
            {
                "latitude": 43.64515 if index == 0 else 43.643198,
                "longitude": 78.535604 if index == 0 else 78.538828,
                "utc_offset_seconds": 0,
                "hourly_units": units,
                "hourly": hourly,
                "generationtime_ms": 0.13 + index,
            }
        )
    return results


class _Response:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def json(self) -> Any:
        return self.payload


class _Transport:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append({"url": url, **kwargs})
        return self.responses.pop(0)


def test_synthetic_helper_covers_arbitrary_origin_offline_and_is_demo_only() -> None:
    request = _request(
        horizon=24,
        origin="2026-08-13T09:00:00+02:00",
    )
    snapshot = make_synthetic_weather_snapshot(request, seed=3)

    assert len(snapshot.rows) == 48
    assert set(snapshot.rows["turbine_id"]) == {"turbine_1", "turbine_2"}
    assert snapshot.rows["valid_time"].min() == pd.Timestamp("2026-08-13T07:00:00Z")
    assert snapshot.provenance["provenance_status"] == "synthetic"
    assert snapshot.provenance["competition_valid"] is False
    assert snapshot.rows[["issued_at", "available_at"]].isna().all().all()
    validate_weather(snapshot, request)
    with pytest.raises(ValueError, match="^unverified_weather_provenance"):
        validate_weather(snapshot, _request(horizon=24, mode="competition", origin="2026-08-13T07:00:00Z"))


def test_future_weather_availability_has_stable_reason_code() -> None:
    request = _request(horizon=24, mode="competition")
    snapshot = _verified_snapshot(request)
    future = pd.Timestamp(request.origin) + pd.Timedelta(hours=1)
    snapshot.rows.loc[0, "available_at"] = future
    snapshot.provenance["available_at"] = future.isoformat()

    with pytest.raises(ValueError, match="^future_weather_available_at"):
        validate_weather(snapshot, request)


@pytest.mark.parametrize("status", ["unknown", "hindcast", "synthetic", "assumed"])
def test_competition_rejects_unverified_provenance(status: str) -> None:
    request = _request(mode="competition")
    snapshot = _verified_snapshot(request, provenance_status=status)

    with pytest.raises(ValueError, match="^unverified_weather_provenance"):
        validate_weather(snapshot, request)


def test_future_initialization_is_rejected() -> None:
    request = _request(mode="demo")
    snapshot = _verified_snapshot(request, provenance_status="unknown")
    future = pd.Timestamp(request.origin) + pd.Timedelta(hours=1)
    snapshot.rows["initialized_at"] = future
    snapshot.provenance["initialized_at"] = future.isoformat()

    with pytest.raises(ValueError, match="^future_weather_initialization"):
        validate_weather(snapshot, request)


def test_fractional_lead_hours_are_rejected_before_integer_conversion() -> None:
    request = _request(mode="competition")
    snapshot = _verified_snapshot(request)
    snapshot.rows["lead_hours"] = snapshot.rows["lead_hours"].astype(float)
    snapshot.rows.loc[0, "lead_hours"] = 0.5

    with pytest.raises(ValueError, match="^weather_lead_invalid"):
        validate_weather(snapshot, request)


def test_impossible_known_provenance_order_is_rejected_in_demo_mode() -> None:
    request = _request(mode="demo")
    snapshot = _verified_snapshot(request, provenance_status="unknown")
    origin = pd.Timestamp(request.origin)
    snapshot.rows["issued_at"] = origin - pd.Timedelta(hours=3)
    snapshot.rows["available_at"] = origin - pd.Timedelta(hours=4)
    snapshot.provenance["issued_at"] = (origin - pd.Timedelta(hours=3)).isoformat()
    snapshot.provenance["available_at"] = (origin - pd.Timedelta(hours=4)).isoformat()

    with pytest.raises(ValueError, match="^weather_provenance_order_invalid"):
        validate_weather(snapshot, request)


def test_row_and_snapshot_provenance_must_match() -> None:
    request = _request(mode="demo")
    snapshot = _verified_snapshot(request, provenance_status="unknown")
    snapshot.rows.loc[0, "issued_at"] = pd.Timestamp(request.origin) - pd.Timedelta(hours=3)

    with pytest.raises(ValueError, match="^weather_provenance_mismatch"):
        validate_weather(snapshot, request)


def test_missing_turbine_or_hour_is_rejected() -> None:
    request = _request(horizon=24)
    snapshot = make_synthetic_weather_snapshot(request)
    original = snapshot.rows.copy()
    snapshot.rows = snapshot.rows[snapshot.rows["turbine_id"] == "turbine_1"].copy()

    with pytest.raises(ValueError, match="^weather_coverage_incomplete"):
        validate_weather(snapshot, request)

    snapshot.rows = original.iloc[1:].copy()
    with pytest.raises(ValueError, match="^weather_coverage_incomplete"):
        validate_weather(snapshot, request)


def test_duplicate_hour_is_rejected() -> None:
    request = _request(horizon=24)
    snapshot = make_synthetic_weather_snapshot(request)
    snapshot.rows = pd.concat([snapshot.rows, snapshot.rows.iloc[[0]]], ignore_index=True)

    with pytest.raises(ValueError, match="^weather_duplicate_hour"):
        validate_weather(snapshot, request)


def test_nonfinite_weather_is_rejected() -> None:
    request = _request(horizon=24)
    snapshot = make_synthetic_weather_snapshot(request)
    snapshot.rows.loc[0, "wind_speed_100m"] = float("nan")

    with pytest.raises(ValueError, match="^weather_value_missing"):
        validate_weather(snapshot, request)


def test_wrong_units_are_rejected() -> None:
    request = _request(horizon=24)
    snapshot = make_synthetic_weather_snapshot(request)
    snapshot.provenance["units"]["wind_speed_unit"] = "km/h"

    with pytest.raises(ValueError, match="^weather_units_invalid"):
        validate_weather(snapshot, request)


def test_fingerprint_ignores_retrieval_metadata_and_row_order() -> None:
    request = _request(horizon=24)
    snapshot = make_synthetic_weather_snapshot(request)
    reversed_rows = snapshot.rows.iloc[::-1].reset_index(drop=True)
    provenance = {**snapshot.provenance, "retrieved_at": "2026-09-23T08:00:00Z", "generationtime_ms": 4}

    assert weather_fingerprint(snapshot.rows, snapshot.provenance) == weather_fingerprint(
        reversed_rows,
        provenance,
    )


def test_fingerprint_changes_when_forecast_value_changes() -> None:
    request = _request(horizon=24)
    snapshot = make_synthetic_weather_snapshot(request)
    changed = snapshot.rows.copy()
    changed.loc[0, "wind_speed_10m"] += 0.01

    assert weather_fingerprint(snapshot.rows, snapshot.provenance) != weather_fingerprint(
        changed,
        snapshot.provenance,
    )


def test_single_runs_fetch_batches_two_turbines_and_preserves_raw_responses(tmp_path: Path) -> None:
    request = _request(origin="2026-02-01T00:00:00+05:00")
    payload = _open_meteo_payload()
    transport = _Transport([_Response(payload)])
    provider = WeatherProvider(tmp_path, transport=transport)  # type: ignore[arg-type]

    snapshot = provider.fetch(request)

    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["url"] == OPEN_METEO_SINGLE_RUNS_URL
    assert call["timeout"] == 20
    params = call["params"]
    assert params["models"] == "ecmwf_ifs"
    assert params["run"] == "2026-01-31T06:00"
    assert params["forecast_hours"] == 72
    assert params["latitude"] == "43.645150,43.643198"
    assert params["longitude"] == "78.535604,78.538828"
    assert params["timezone"] == "UTC"
    assert params["wind_speed_unit"] == "ms"
    assert len(snapshot.rows) == 2 * request.horizon
    assert snapshot.rows["valid_time"].min() == pd.Timestamp("2026-01-31T19:00:00Z")
    assert snapshot.rows["wind_gusts_10m"].notna().all()
    assert snapshot.provenance["issued_at"] is None
    assert snapshot.provenance["available_at"] is None
    assert snapshot.provenance["provenance_status"] == "hindcast"
    assert snapshot.provenance["competition_valid"] is False
    assert len(snapshot.raw_responses) == 2
    assert all(isinstance(response, dict) for response in snapshot.raw_responses)
    assert snapshot.raw_responses == payload

    with pytest.raises(ValueError, match="^unverified_weather_provenance"):
        validate_weather(snapshot, _request(mode="competition"))


def test_duplicate_timestamps_in_raw_api_response_are_rejected(tmp_path: Path) -> None:
    payload = _open_meteo_payload()
    duplicate_index = 13
    original_times = payload[0]["hourly"]["time"].copy()
    for response in payload:
        hourly = response["hourly"]
        hourly["time"] = original_times.copy()
        hourly["time"].insert(duplicate_index + 1, hourly["time"][duplicate_index])
        for variable in WEATHER_VARIABLES:
            hourly[variable].insert(duplicate_index + 1, hourly[variable][duplicate_index])
    provider = WeatherProvider(tmp_path, transport=_Transport([_Response(payload)]))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="^weather_response_invalid: duplicate hourly timestamp"):
        provider.fetch(_request())


def test_fetch_uses_cached_response_before_transport(tmp_path: Path) -> None:
    request = _request()
    first_transport = _Transport([_Response(_open_meteo_payload())])
    first = WeatherProvider(tmp_path, transport=first_transport).fetch(request)  # type: ignore[arg-type]
    unused_transport = _Transport([])
    second = WeatherProvider(tmp_path, transport=unused_transport).fetch(request)  # type: ignore[arg-type]

    assert len(first_transport.calls) == 1
    assert unused_transport.calls == []
    assert first.fingerprint == second.fingerprint
    assert list((tmp_path / "open-meteo").glob("*.json"))


def test_cached_future_issue_time_is_rejected(tmp_path: Path) -> None:
    request = _request()
    WeatherProvider(
        tmp_path,
        transport=_Transport([_Response(_open_meteo_payload())]),
    ).fetch(request)  # type: ignore[arg-type]
    cache_path = next((tmp_path / "open-meteo").glob("*.json"))
    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    cached["snapshot_provenance"]["issued_at"] = "2026-02-01T00:00:00Z"
    cache_path.write_text(json.dumps(cached), encoding="utf-8")
    unused_transport = _Transport([])

    with pytest.raises(ValueError, match="^future_weather_issued_at"):
        WeatherProvider(tmp_path, transport=unused_transport).fetch(request)  # type: ignore[arg-type]
    assert unused_transport.calls == []


def test_refresh_writes_new_immutable_version_when_response_changes(tmp_path: Path) -> None:
    request = _request()
    first_payload = _open_meteo_payload()
    second_payload = _open_meteo_payload()
    second_payload[0]["hourly"]["wind_speed_10m"][20] += 1.0
    first = WeatherProvider(tmp_path, transport=_Transport([_Response(first_payload)])).fetch(request)  # type: ignore[arg-type]
    second = WeatherProvider(tmp_path, transport=_Transport([_Response(second_payload)])).fetch(  # type: ignore[arg-type]
        request,
        refresh=True,
    )

    assert first.fingerprint != second.fingerprint
    assert len(list((tmp_path / "open-meteo").glob("*.json"))) == 2


def test_http_retry_budget_is_three_attempts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request()
    transport = _Transport([_Response({}, 503), _Response({}, 503), _Response({}, 503)])
    monkeypatch.setattr("wind_forecast.weather.time.sleep", lambda seconds: None)

    with pytest.raises(ValueError, match="^weather_request_failed"):
        WeatherProvider(tmp_path, transport=transport).fetch(request)  # type: ignore[arg-type]
    assert len(transport.calls) == 3


def test_http_client_error_is_not_retried(tmp_path: Path) -> None:
    transport = _Transport([_Response({"error": True}, 400), _Response({}, 200)])

    with pytest.raises(ValueError, match="^weather_http_error"):
        WeatherProvider(tmp_path, transport=transport).fetch(_request())  # type: ignore[arg-type]
    assert len(transport.calls) == 1


def test_written_fixture_is_offline_synthetic_and_has_complete_rows(tmp_path: Path) -> None:
    request = _request(horizon=24, origin="2026-07-14T02:00:00Z")
    destination = tmp_path / "weather.json"
    write_synthetic_weather_fixture(destination, request, seed=17)
    fixture = json.loads(destination.read_text(encoding="utf-8"))

    assert fixture["mode"] == "demo"
    assert fixture["provenance"]["provenance_status"] == "synthetic"
    assert fixture["provenance"]["competition_valid"] is False
    assert len(fixture["rows"]) == 48
    assert set(fixture["rows"][0]) == set(WEATHER_COLUMNS)


def test_checked_in_fixture_has_explicit_synthetic_provenance() -> None:
    fixture_path = Path(__file__).parent / "fixtures/weather/synthetic_48h.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    assert fixture["mode"] == "demo"
    assert fixture["provenance"]["provenance_status"] == "synthetic"
    assert fixture["provenance"]["issued_at"] is None
    assert len(fixture["rows"]) == 96
