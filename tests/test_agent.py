from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from wind_forecast.artifacts import ArtifactStore
from wind_forecast.contracts import Decision, Prediction, RunResult, parse_request
from wind_forecast.weather import WEATHER_VARIABLES, WeatherProvider, make_synthetic_weather_snapshot
from wind_forecast.agent import ForecastAgent, quality_gate


def _request(mode: str = "demo", horizon: int = 24):
    return parse_request("2026-02-01T00:00:00+05:00", horizon, mode)  # type: ignore[arg-type]


def _prediction(request: Any, *, model_name: str = "catboost") -> Prediction:
    rows = []
    for turbine_id in ("turbine_1", "turbine_2"):
        for lead in range(request.horizon):
            rows.append(
                {
                    "turbine_id": turbine_id,
                    "valid_time": pd.Timestamp(request.origin) + pd.Timedelta(hours=lead),
                    "lead_hours": lead,
                    "p10": 0.2,
                    "p50": 0.4,
                    "p90": 0.6,
                }
            )
    return Prediction(pd.DataFrame(rows), model_name, {"training_rows": 120})


class _Provider:
    def __init__(self, snapshot: Any, calls: list[str]) -> None:
        self.snapshot = snapshot
        self.calls = calls

    def fetch(self, request: Any, refresh: bool = False) -> Any:
        self.calls.append("fetch")
        return self.snapshot


class _Store:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.persisted: list[dict[str, Any]] = []

    def persist(self, request: Any, prediction: Any, snapshot: Any, metrics: Any,
                events: Any, report: Any, manifest: Any) -> RunResult:
        self.calls.append("persist")
        self.persisted.append(
            {"prediction": prediction, "snapshot": snapshot, "metrics": metrics,
             "events": events, "report": report, "manifest": manifest}
        )
        status = manifest["status"]
        return RunResult("test-run", Path("/tmp/test-run"), status, False)


class _Analyzer:
    def __init__(self, calls: list[str], outcomes: list[Any]) -> None:
        self.calls = calls
        self.outcomes = outcomes
        self.inputs: list[tuple[dict[str, Any], set[str]]] = []

    def __call__(self, diagnostics: dict[str, Any], allowed: set[str]) -> Decision:
        self.calls.append("analyze")
        self.inputs.append((diagnostics, allowed))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_llm_timeout_after_valid_forecast_persists_with_audited_fallback() -> None:
    request = _request()
    calls: list[str] = []
    snapshot = make_synthetic_weather_snapshot(request)
    store = _Store(calls)
    agent = ForecastAgent(
        _Provider(snapshot, calls),
        lambda history, weather, req: (calls.append("predict") or _prediction(req)),
        _Analyzer(calls, [TimeoutError("private transport detail")]),
        store,
    )

    result = agent.run(request, pd.DataFrame({"power": [0.5]}))

    assert result.status == "degraded"
    assert calls == ["fetch", "predict", "analyze", "persist"]
    persisted = store.persisted[0]
    assert persisted["prediction"] is not None
    assert persisted["manifest"]["competition_valid"] is False
    llm_events = [event for event in persisted["events"] if event["state"] == "analyze_result"]
    assert llm_events[0]["outcome"] == "failed"
    assert llm_events[0]["reason"] == "analyzer_unavailable:TimeoutError"
    opaque_event = next(
        event for event in persisted["events"] if event["state"] == "generate_forecast"
    )
    assert "opaque predictor call" in opaque_event["reason"]
    assert "private transport detail" not in persisted["report"]
    assert all(event["duration_ms"] >= 0 for event in persisted["events"])


def test_analyzer_deterministic_outage_fallback_is_marked_skipped_in_trace() -> None:
    request = _request()
    calls: list[str] = []
    snapshot = make_synthetic_weather_snapshot(request)
    store = _Store(calls)
    agent = ForecastAgent(
        _Provider(snapshot, calls),
        lambda history, weather, req: _prediction(req),
        _Analyzer(calls, [Decision("finalize", "deterministic_fallback:api_unavailable")]),
        store,
    )

    agent.run(request, pd.DataFrame({"power": [0.5]}))

    event = next(event for event in store.persisted[0]["events"] if event["state"] == "analyze_result")
    assert event["outcome"] == "skipped"
    assert event["reason"] == "validated_action:finalize"


def test_invalid_weather_provenance_cannot_be_bypassed_by_baseline() -> None:
    request = _request(mode="competition")
    calls: list[str] = []
    snapshot = make_synthetic_weather_snapshot(request)
    store = _Store(calls)
    agent = ForecastAgent(
        _Provider(snapshot, calls),
        lambda history, weather, req: (calls.append("predict") or _prediction(req)),
        _Analyzer(calls, [Decision("use_baseline", "try to bypass weather gate")]),
        store,
        baseline_predictor=lambda history, weather, req: _prediction(req, model_name="baseline"),
    )

    result = agent.run(request, pd.DataFrame({"power": [0.5]}))

    assert result.status == "failed"
    assert calls == ["fetch", "persist"]
    assert store.persisted[0]["prediction"] is None
    assert store.persisted[0]["manifest"]["competition_valid"] is False
    assert any(event["state"] == "validate_inputs" and event["outcome"] == "failed"
               for event in store.persisted[0]["events"])


def test_invalid_weather_run_is_persisted_without_a_forecast(tmp_path: Path) -> None:
    request = _request(mode="competition")
    snapshot = make_synthetic_weather_snapshot(request)
    agent = ForecastAgent(
        _Provider(snapshot, []),
        lambda history, weather, req: _prediction(req),
        _Analyzer([], []),
        ArtifactStore(tmp_path),
    )

    result = agent.run(request, pd.DataFrame({"power": [0.5]}))

    assert result.status == "failed"
    assert not (result.directory / "forecast.csv").exists()
    manifest = json.loads((result.directory / "manifest.json").read_text())
    assert manifest["status"] == "failed"
    assert manifest["competition_valid"] is False


def test_structurally_invalid_weather_still_gets_a_failure_manifest(tmp_path: Path) -> None:
    request = _request()
    snapshot = make_synthetic_weather_snapshot(request)
    snapshot.rows = snapshot.rows.drop(columns=["surface_pressure"])
    result = ForecastAgent(
        _Provider(snapshot, []),
        lambda history, weather, req: _prediction(req),
        _Analyzer([], []),
        ArtifactStore(tmp_path),
    ).run(request, pd.DataFrame({"power": [0.5]}))

    assert result.status == "failed"
    manifest = json.loads((result.directory / "manifest.json").read_text())
    weather = json.loads((result.directory / "weather.json").read_text())
    assert manifest["status"] == "failed"
    assert manifest["fingerprints"]["canonical_weather"] == weather["snapshot_fingerprint"]
    assert not (result.directory / "forecast.csv").exists()


def test_predictor_failure_uses_only_the_explicit_baseline_and_is_degraded() -> None:
    request = _request()
    calls: list[str] = []
    snapshot = make_synthetic_weather_snapshot(request)
    analyzer = _Analyzer(
        calls,
        [Decision("use_baseline", "model unavailable"), Decision("finalize", "quality passed")],
    )
    agent = ForecastAgent(
        _Provider(snapshot, calls),
        lambda history, weather, req: (_ for _ in ()).throw(RuntimeError("model failed")),
        analyzer,
        _Store(calls),
        baseline_predictor=lambda history, weather, req: (
            calls.append("baseline") or _prediction(req, model_name="persistence")
        ),
    )

    result = agent.run(request, pd.DataFrame({"power": [0.5]}))

    assert result.status == "degraded"
    assert calls == ["fetch", "analyze", "baseline", "analyze", "persist"]
    assert len(analyzer.inputs) == 2
    assert analyzer.inputs[0][1] == {"use_baseline", "abort"}
    assert analyzer.inputs[1][1] == {"continue", "finalize", "abort"}


def test_predictor_failure_without_injected_baseline_is_failed_not_fabricated() -> None:
    request = _request()
    calls: list[str] = []
    snapshot = make_synthetic_weather_snapshot(request)
    agent = ForecastAgent(
        _Provider(snapshot, calls),
        lambda history, weather, req: (_ for _ in ()).throw(RuntimeError("model failed")),
        _Analyzer(calls, [Decision("use_baseline", "baseline requested")]),
        _Store(calls),
    )

    result = agent.run(request, pd.DataFrame({"power": [0.5]}))

    assert result.status == "failed"
    assert calls == ["fetch", "analyze", "persist"]


def test_rejected_model_and_baseline_forecasts_cannot_produce_success() -> None:
    request = _request()
    calls: list[str] = []
    snapshot = make_synthetic_weather_snapshot(request)
    invalid = _prediction(request)
    invalid.rows = invalid.rows.iloc[:-1].copy()
    store = _Store(calls)
    agent = ForecastAgent(
        _Provider(snapshot, calls),
        lambda history, weather, req: invalid,
        _Analyzer(calls, [Decision("use_baseline", "replace invalid output")]),
        store,
        baseline_predictor=lambda history, weather, req: invalid,
    )

    result = agent.run(request, pd.DataFrame({"power": [0.5]}))

    assert result.status == "failed"
    assert store.persisted[0]["prediction"] is None
    assert store.persisted[0]["manifest"]["status"] == "failed"


def test_quality_gate_corrects_crossings_and_clips_only_finite_values() -> None:
    request = _request()
    snapshot = make_synthetic_weather_snapshot(request)
    prediction = _prediction(request)
    prediction.rows.loc[0, ["p10", "p50", "p90"]] = [0.8, 0.4, 1.2]
    gated = quality_gate(prediction, snapshot, request)

    assert gated.rows.loc[0, ["p10", "p50", "p90"]].tolist() == [0.4, 0.8, 1.0]
    assert gated.diagnostics["quality_gate"]["quantile_correction_count"] == 1
    assert gated.diagnostics["quality_gate"]["clipping_count"] == 1


@pytest.mark.parametrize(
    "diagnostic",
    ["quantile_crossing_correction_count", "quantile_clipping_count"],
)
def test_model_quantile_corrections_force_degraded_status(diagnostic: str) -> None:
    request = _request(mode="competition")
    snapshot = make_synthetic_weather_snapshot(request)
    snapshot.provenance["provenance_status"] = "verified"
    prediction = _prediction(request)
    prediction.diagnostics.update(
        {
            "quality_gate": {
                "quantile_correction_count": 0,
                "clipping_count": 0,
                "weather_validated": True,
            },
            diagnostic: 1,
        }
    )

    assert ForecastAgent._success_status(prediction, snapshot) == "degraded"


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf")])
def test_quality_gate_rejects_nonfinite_quantiles_before_clipping(bad_value: float) -> None:
    request = _request()
    snapshot = make_synthetic_weather_snapshot(request)
    prediction = _prediction(request)
    prediction.rows.loc[0, "p50"] = bad_value

    with pytest.raises(ValueError, match="^prediction_nonfinite_quantile"):
        quality_gate(prediction, snapshot, request)


def test_quality_gate_rejects_duplicate_key_even_when_grid_length_is_two_h() -> None:
    request = _request()
    snapshot = make_synthetic_weather_snapshot(request)
    prediction = _prediction(request)
    prediction.rows.loc[len(prediction.rows) - 1, "valid_time"] = prediction.rows.loc[0, "valid_time"]
    prediction.rows.loc[len(prediction.rows) - 1, "turbine_id"] = prediction.rows.loc[0, "turbine_id"]

    with pytest.raises(ValueError, match="^prediction_duplicate_key"):
        quality_gate(prediction, snapshot, request)


def test_refresh_fetch_failure_can_use_valid_cached_weather_without_retrying_fetch() -> None:
    request = _request()
    calls: list[str] = []
    snapshot = make_synthetic_weather_snapshot(request)

    class FailingRefreshProvider:
        def fetch(self, req: Any, refresh: bool = False) -> Any:
            calls.append("fetch")
            raise RuntimeError("transport detail")

        def read_cached(self, req: Any) -> Any:
            calls.append("read_cached")
            return snapshot

    store = _Store(calls)
    analyzer = _Analyzer(calls, [Decision("use_cached_weather", "cached snapshot is fine"),
                                Decision("finalize", "forecast passed")])
    agent = ForecastAgent(
        FailingRefreshProvider(),
        lambda history, weather, req: (calls.append("predict") or _prediction(req)),
        analyzer,
        store,
    )

    result = agent.run(request, pd.DataFrame({"power": [0.5]}), refresh=True)

    assert result.status == "degraded"
    assert calls == ["fetch", "read_cached", "analyze", "predict", "analyze", "persist"]
    assert analyzer.inputs[0][1] == {"use_cached_weather", "abort"}
    assert store.persisted[0]["events"][3]["state"] == "analyze_result"
    assert store.persisted[0]["events"][3]["action"] == "use_cached_weather"
    assert store.persisted[0]["events"][3]["reason"] == "validated_action:use_cached_weather"
    events = store.persisted[0]["events"]
    assert [event["state"] for event in events[:5]] == [
        "fetch_weather", "read_cached_weather", "validate_inputs", "analyze_result",
        "check_for_updates",
    ]
    assert sum(event["state"] == "fetch_weather" for event in events) == 1
    assert events[0]["outcome"] == "failed"


def test_weather_provider_cache_only_lookup_does_not_contact_transport(tmp_path: Path) -> None:
    request = _request()
    origin_run = "2026-01-31T06:00"
    times = [
        stamp.strftime("%Y-%m-%dT%H:%M")
        for stamp in pd.date_range(f"{origin_run}:00Z", periods=72, freq="h")
    ]
    units = {
        "wind_speed_10m": "m/s", "wind_speed_100m": "m/s",
        "wind_direction_10m": "°", "wind_gusts_10m": "m/s",
        "temperature_2m": "°C", "surface_pressure": "hPa",
    }
    payload = []
    for index in range(2):
        payload.append({
            "latitude": 43.64515 if index == 0 else 43.643198,
            "longitude": 78.535604 if index == 0 else 78.538828,
            "utc_offset_seconds": 0,
            "hourly_units": units,
            "hourly": {
                "time": times,
                **{
                    column: (
                        [205.0 + index] * len(times)
                        if column == "wind_direction_10m"
                        else [1013.0 + index] * len(times)
                        if column == "surface_pressure"
                        else [5.0 + index] * len(times)
                    )
                    for column in WEATHER_VARIABLES
                },
            },
        })

    class Response:
        status_code = 200

        def json(self) -> Any:
            return payload

    class Transport:
        def __init__(self) -> None:
            self.calls = 0

        def get(self, url: str, **kwargs: Any) -> Response:
            self.calls += 1
            return Response()

    transport = Transport()
    WeatherProvider(tmp_path, transport=transport).fetch(request)  # type: ignore[arg-type]
    provider = WeatherProvider(tmp_path, transport=transport)  # type: ignore[arg-type]

    cached = provider.read_cached(request)

    assert cached is not None
    assert len(cached.rows) == 2 * request.horizon
    assert transport.calls == 1

    cache_path = next((tmp_path / "open-meteo").glob("*.json"))
    cache_body = json.loads(cache_path.read_text(encoding="utf-8"))
    cache_body["retrieved_at"] = "not-a-zoned-timestamp"
    cache_path.write_text(json.dumps(cache_body), encoding="utf-8")
    assert provider.read_cached(request) is None
    assert transport.calls == 1


def test_invalid_cached_weather_cannot_be_approved_or_used_after_fetch_failure() -> None:
    request = _request()
    calls: list[str] = []
    invalid_snapshot = make_synthetic_weather_snapshot(request)
    invalid_snapshot.rows = invalid_snapshot.rows.drop(columns=["surface_pressure"])
    store = _Store(calls)

    class FailingRefreshProvider:
        def fetch(self, req: Any, refresh: bool = False) -> Any:
            calls.append("fetch")
            raise RuntimeError("transport unavailable")

        def read_cached(self, req: Any) -> Any:
            calls.append("read_cached")
            return invalid_snapshot

    result = ForecastAgent(
        FailingRefreshProvider(),
        lambda history, weather, req: (calls.append("predict") or _prediction(req)),
        _Analyzer(calls, []),
        store,
    ).run(request, pd.DataFrame({"power": [0.5]}), refresh=True)

    assert result.status == "failed"
    assert calls == ["fetch", "read_cached", "persist"]
    assert store.persisted[0]["prediction"] is None
    assert not any(event["state"] == "analyze_result" for event in store.persisted[0]["events"])
    assert any(event["state"] == "validate_inputs" and event["outcome"] == "failed"
               for event in store.persisted[0]["events"])


def test_event_reason_never_copies_secret_like_value_error_text() -> None:
    request = _request()
    calls: list[str] = []
    store = _Store(calls)

    class SecretFailureProvider:
        def fetch(self, req: Any, refresh: bool = False) -> Any:
            raise ValueError("supersecretvalue")

    ForecastAgent(
        SecretFailureProvider(),
        lambda history, weather, req: _prediction(req),
        _Analyzer(calls, []),
        store,
    ).run(request, pd.DataFrame({"power": [0.5]}))

    event_text = " ".join(event["reason"] for event in store.persisted[0]["events"])
    assert "supersecretvalue" not in event_text
    assert "supersecretvalue" not in store.persisted[0]["report"]


def test_staged_predictor_trace_times_real_stages_and_keeps_untrusted_reason_out_of_report() -> None:
    request = _request()
    calls: list[str] = []
    snapshot = make_synthetic_weather_snapshot(request)
    store = _Store(calls)

    class StagedPredictor:
        reuse_identity = {"config": "model-config-v1", "model": "model-v1"}

        def prepare(self, history: pd.DataFrame, weather: Any, req: Any) -> pd.DataFrame:
            calls.append("prepare")
            return history.copy()

        def train_or_load(self, history: pd.DataFrame, features: pd.DataFrame,
                          weather: Any, req: Any) -> str:
            calls.append("train_or_load")
            return "model-bundle"

        def predict(self, model_bundle: str, features: pd.DataFrame, req: Any) -> Prediction:
            calls.append("predict")
            return _prediction(req)

    reason = "MAE is 0.01 and confidence is 99%"
    agent = ForecastAgent(
        _Provider(snapshot, calls),
        StagedPredictor(),
        _Analyzer(calls, [Decision("finalize", reason)]),
        store,
    )

    agent.run(request, pd.DataFrame({"power": [0.5]}))

    events = store.persisted[0]["events"]
    states = [event["state"] for event in events]
    assert [state for state in states if state in {
        "prepare_features", "train_or_load_model", "generate_forecast"
    }] == ["prepare_features", "train_or_load_model", "generate_forecast"]
    assert all(next(event for event in events if event["state"] == state)["duration_ms"] >= 0
               for state in ("prepare_features", "train_or_load_model", "generate_forecast"))
    assert calls == ["fetch", "prepare", "train_or_load", "predict", "analyze", "persist"]
    assert calls.count("fetch") == 1
    assert "MAE is 0.01" not in store.persisted[0]["report"]
    assert "confidence is 99%" not in store.persisted[0]["report"]
    assert all(reason not in event["reason"] for event in events)
    assert "validated_action:finalize" in store.persisted[0]["report"]


def test_identical_staged_refresh_reuses_prior_forecast_with_its_own_trace(tmp_path: Path) -> None:
    request = _request()
    snapshot = make_synthetic_weather_snapshot(request)
    store = ArtifactStore(tmp_path)
    calls: list[str] = []
    refresh_flags: list[bool] = []

    class StagedPredictor:
        reuse_identity = {"config": {"iterations": 7}, "model": "catboost-v1"}

        def prepare(self, history: pd.DataFrame, weather: Any, req: Any) -> pd.DataFrame:
            calls.append("prepare")
            return history.copy()

        def train_or_load(self, history: pd.DataFrame, features: pd.DataFrame,
                          weather: Any, req: Any) -> str:
            calls.append("train_or_load")
            return "model-bundle"

        def predict(self, model_bundle: str, features: pd.DataFrame, req: Any) -> Prediction:
            calls.append("predict")
            return _prediction(req)

    class TrackingProvider:
        def fetch(self, req: Any, refresh: bool = False) -> Any:
            refresh_flags.append(refresh)
            return snapshot

    def build_agent() -> ForecastAgent:
        return ForecastAgent(
            TrackingProvider(), StagedPredictor(),
            _Analyzer([], [Decision("finalize", "ok")]), store,
        )

    history = pd.DataFrame({"power": [0.5]})
    first = build_agent().run(request, history)
    first_events = [
        json.loads(line) for line in (first.directory / "events.jsonl").read_text().splitlines()
    ]
    update_check = next(event for event in first_events if event["state"] == "check_for_updates")
    assert update_check["details"]["reuse_outcome"] == "miss"
    assert all(
        len(update_check["details"]["fingerprints"][name]) == 64
        for name in ("data_contents", "canonical_weather", "predictor_config", "predictor_model")
    )
    first_manifest = json.loads((first.directory / "manifest.json").read_text(encoding="utf-8"))
    original_hashes = {
        name: hashlib.sha256((first.directory / name).read_bytes()).hexdigest()
        for name in first_manifest["files"]
    }
    original_names = {path.name for path in first.directory.iterdir()}
    before_second = list(calls)
    second = build_agent().run(request, history, refresh=True)

    assert first.reused is False
    assert second.reused is True
    assert second.run_id == first.run_id
    assert refresh_flags == [False, True]
    assert calls == before_second
    assert {path.name for path in first.directory.iterdir()} == original_names
    assert {
        name: hashlib.sha256((first.directory / name).read_bytes()).hexdigest()
        for name in first_manifest["files"]
    } == original_hashes
    reuse_check = json.loads((tmp_path / "update_checks.jsonl").read_text().splitlines()[-1])
    assert reuse_check["reuse_outcome"] == "hit"
    assert reuse_check["run_id"] == first.run_id

    trace_files = list((tmp_path / "reuse-events").glob("*.jsonl"))
    assert len(trace_files) == 1
    trace_path = trace_files[0]
    trace = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    required_fields = {
        "schema_version", "sequence", "timestamp", "duration_ms", "state", "action",
        "reason", "outcome", "details", "invocation_id", "reused_run_id",
    }
    assert all(required_fields.issubset(event) for event in trace)
    assert [event["sequence"] for event in trace] == list(range(1, len(trace) + 1))
    assert all(event["invocation_id"] == trace_path.stem for event in trace)
    assert all(event["reused_run_id"] == first.run_id for event in trace)
    assert all(event["duration_ms"] >= 0 for event in trace)
    assert all(event["timestamp"].endswith("Z") for event in trace)
    check_event = next(event for event in trace if event["state"] == "check_for_updates")
    assert check_event["details"]["reuse_outcome"] == "hit"
    assert check_event["details"]["run_id"] == first.run_id
    assert [event["state"] for event in trace] == [
        "fetch_weather", "validate_inputs", "check_for_updates"
    ]


def test_refresh_failure_cached_weather_reuse_preserves_all_current_events(tmp_path: Path) -> None:
    request = _request()
    snapshot = make_synthetic_weather_snapshot(request)
    store = ArtifactStore(tmp_path)
    predictor_calls: list[str] = []

    class StagedPredictor:
        reuse_identity = {"config": {"iterations": 7}, "model": "catboost-v1"}

        def prepare(self, history: pd.DataFrame, weather: Any, req: Any) -> pd.DataFrame:
            predictor_calls.append("prepare")
            return history.copy()

        def train_or_load(self, history: pd.DataFrame, features: pd.DataFrame,
                          weather: Any, req: Any) -> str:
            predictor_calls.append("train_or_load")
            return "model-bundle"

        def predict(self, model_bundle: str, features: pd.DataFrame, req: Any) -> Prediction:
            predictor_calls.append("predict")
            return _prediction(req)

    class CachedRefreshProvider:
        def fetch(self, req: Any, refresh: bool = False) -> Any:
            assert refresh is True
            raise RuntimeError("private transport detail")

        def read_cached(self, req: Any) -> Any:
            return snapshot

    history = pd.DataFrame({"power": [0.5]})
    first = ForecastAgent(
        _Provider(snapshot, []), StagedPredictor(),
        _Analyzer([], [Decision("finalize", "ok")]), store,
    ).run(request, history)
    second = ForecastAgent(
        CachedRefreshProvider(), StagedPredictor(),
        _Analyzer([], [Decision("use_cached_weather", "cached weather is valid")]), store,
    ).run(request, history, refresh=True)

    assert first.reused is False
    assert second.reused is True
    assert second.run_id == first.run_id
    assert predictor_calls == ["prepare", "train_or_load", "predict"]
    trace_files = list((tmp_path / "reuse-events").glob("*.jsonl"))
    assert len(trace_files) == 1
    trace = [json.loads(line) for line in trace_files[0].read_text(encoding="utf-8").splitlines()]
    assert [event["state"] for event in trace] == [
        "fetch_weather", "read_cached_weather", "validate_inputs", "analyze_result",
        "check_for_updates",
    ]
    assert trace[0]["outcome"] == "failed"
    assert trace[0]["reason"] == "operation_failed:RuntimeError"
    assert trace[3]["action"] == "use_cached_weather"
    assert trace[3]["outcome"] == "success"
    assert trace[-1]["details"]["reuse_outcome"] == "hit"
    assert all(event["reused_run_id"] == first.run_id for event in trace)
    assert "private transport detail" not in trace_files[0].read_text(encoding="utf-8")
