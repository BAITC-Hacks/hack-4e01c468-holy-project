"""Bounded, auditable orchestration for one forecast run."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import numbers
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

import numpy as np
import pandas as pd

from wind_forecast.application.code_identity import _code_fingerprint, _code_identity
from wind_forecast.contracts import Decision, Prediction, RunRequest, RunResult, WeatherSnapshot
from wind_forecast.weather import validate_weather, weather_fingerprint

CallablePredictor = Callable[[pd.DataFrame, WeatherSnapshot, RunRequest], Prediction]


class StagedPredictor(Protocol):
    """Predictor interface with individually auditable work stages."""

    def prepare(
        self, history: pd.DataFrame, snapshot: WeatherSnapshot, request: RunRequest
    ) -> Any: ...

    def train_or_load(
        self,
        history: pd.DataFrame,
        features: Any,
        snapshot: WeatherSnapshot,
        request: RunRequest,
    ) -> Any: ...

    def predict(
        self, model_bundle: Any, features: Any, request: RunRequest
    ) -> Prediction: ...


Predictor = CallablePredictor | StagedPredictor
Analyzer = Callable[[dict[str, Any], set[str]], Decision]

_QUANTILES = ("p10", "p50", "p90")
_TURBINES = ("turbine_1", "turbine_2")
_MAX_ANALYZER_CALLS = 2
_DEPENDENCIES = ("numpy", "pandas", "catboost", "openai", "requests")
_SAFE_ERROR_CODES = {
    "artifact_timestamp_must_be_zoned",
    "artifact_run_status_invalid",
    "artifact_success_requires_prediction_and_weather",
    "future_weather_initialization",
    "future_weather_issued_at",
    "future_weather_available_at",
    "invalid_run_mode",
    "invalid_weather_rows",
    "missing_weather_columns",
    "weather_cache_invalid",
    "weather_cache_provenance_mismatch",
    "weather_cache_run_mismatch",
    "weather_cache_timestamp_invalid",
    "weather_coverage_incomplete",
    "weather_duplicate_hour",
    "weather_http_error",
    "weather_lead_invalid",
    "weather_model_mismatch",
    "weather_model_missing",
    "weather_provenance_mismatch",
    "weather_provenance_order_invalid",
    "weather_request_failed",
    "weather_response_invalid",
    "weather_timestamp_invalid",
    "weather_turbine_invalid",
    "weather_units_invalid",
    "weather_value_missing",
    "weather_value_out_of_range",
    "weather_variable_missing",
    "unverified_weather_provenance",
    "prediction_schema_invalid",
    "prediction_schema_missing_columns",
    "prediction_lead_out_of_range",
    "prediction_valid_time_invalid",
    "prediction_duplicate_key",
    "prediction_lead_invalid",
    "prediction_lead_mismatch",
    "prediction_grid_incomplete",
    "prediction_quantile_invalid",
    "prediction_nonfinite_quantile",
}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _jsonable(value: Any) -> Any:
    """Convert scientific Python values to strict JSON values."""
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        stamp = pd.Timestamp(value)
        if stamp.tzinfo is not None:
            stamp = stamp.tz_convert("UTC")
            return stamp.isoformat().replace("+00:00", "Z")
        return stamp.isoformat()
    if isinstance(value, np.generic):
        return _jsonable(value.item())
    if isinstance(value, bool):
        return value
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, Path)):
        return str(value)
    try:
        missing = pd.isna(value)
        if isinstance(missing, (bool, np.bool_)) and missing:
            return None
    except (TypeError, ValueError):
        pass
    raise TypeError(f"not_json_serializable:{type(value).__name__}")


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        _jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _frame_fingerprint(frame: pd.DataFrame) -> str:
    columns = sorted(str(column) for column in frame.columns)
    records: list[str] = []
    for raw in frame.to_dict(orient="records"):
        row = {str(key): _jsonable(value) for key, value in raw.items()}
        records.append(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False))
    records.sort()
    return _canonical_hash({"columns": columns, "rows": records})


def _snapshot_fingerprint(snapshot: WeatherSnapshot) -> str:
    try:
        return weather_fingerprint(snapshot.rows, snapshot.provenance)
    except Exception:
        rows = snapshot.rows
        if isinstance(rows, pd.DataFrame):
            row_identity: Any = {"dataframe": _frame_fingerprint(rows)}
        else:
            row_identity = _jsonable(rows)
        stable_provenance = {
            key: value
            for key, value in snapshot.provenance.items()
            if key not in {"retrieved_at", "generationtime_ms", "cache_hit", "http_status"}
        }
        return _canonical_hash(
            {
                "invalid_snapshot": row_identity,
                "source_fingerprint": snapshot.fingerprint,
                "provenance": stable_provenance,
            }
        )


def _dependency_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for package in _DEPENDENCIES:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            continue
    return versions


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


class ForecastAgent:
    """Run one forecast through deterministic gates and at most two LLM decisions."""

    def __init__(
        self,
        weather_provider: Any,
        predictor: Predictor,
        analyzer: Analyzer,
        store: Any,
        baseline_predictor: CallablePredictor | None = None,
    ) -> None:
        self.weather_provider = weather_provider
        self.predictor = predictor
        self.analyzer = analyzer
        self.store = store
        self.baseline_predictor = baseline_predictor

    def run(
        self,
        request: RunRequest,
        history: pd.DataFrame,
        refresh: bool = False,
    ) -> RunResult:
        events: list[dict[str, Any]] = []
        analyzer_calls = 0
        snapshot: WeatherSnapshot | None = None
        prediction: Prediction | None = None
        used_cached_weather = False
        active_predictor_fingerprints = self._predictor_fingerprints(self.predictor)

        try:
            try:
                snapshot = self._step(
                    events,
                    "fetch_weather",
                    "continue",
                    "fetch point-in-time weather snapshot",
                    lambda: self.weather_provider.fetch(request, refresh=refresh),
                )
            except Exception as fetch_error:
                read_cached = getattr(self.weather_provider, "read_cached", None)
                if not callable(read_cached):
                    return self._failed(
                        request, history, None, None, events, self._error_code(fetch_error)
                    )
                try:
                    snapshot = self._step(
                        events,
                        "read_cached_weather",
                        "use_cached_weather",
                        "look for a valid snapshot without network access",
                        lambda: read_cached(request),
                    )
                except Exception:
                    return self._failed(
                        request, history, None, None, events, self._error_code(fetch_error)
                    )
                if snapshot is None:
                    events[-1]["action"] = "abort"
                    events[-1]["reason"] = "no_valid_cached_weather"
                    events[-1]["outcome"] = "skipped"
                    return self._failed(
                        request, history, None, None, events, self._error_code(fetch_error)
                    )

                try:
                    self._step(
                        events,
                        "validate_inputs",
                        "continue",
                        "validate cached weather provenance and horizon coverage",
                        lambda: validate_weather(snapshot, request),
                    )
                except Exception as cache_error:
                    return self._failed(
                        request, history, snapshot, None, events, self._error_code(cache_error)
                    )

                choice, analyzer_calls = self._decide(
                    events,
                    {
                        "status": "weather_fetch_failed",
                        "failure_code": self._error_code(fetch_error),
                        "cached_weather_available": True,
                    },
                    {"use_cached_weather", "abort"},
                    analyzer_calls,
                )
                if choice.action != "use_cached_weather":
                    return self._failed(
                        request, history, None, None, events, "weather_fetch_failed"
                    )
                used_cached_weather = True
            else:
                self._step(
                    events,
                    "validate_inputs",
                    "continue",
                    "validate weather provenance and horizon coverage",
                    lambda: validate_weather(snapshot, request),
                )

            update_fingerprints = self._update_fingerprints(
                history, snapshot, active_predictor_fingerprints
            )
            reusable = self._check_for_updates(events, request, snapshot, update_fingerprints)
            if reusable is not None:
                record_reuse_events = getattr(self.store, "record_reuse_events", None)
                if callable(record_reuse_events):
                    record_reuse_events(events, reusable.run_id)
                return reusable

            prediction_error: Exception | None = None
            try:
                prediction, prediction_predictor_fingerprints = self._generate_forecast(
                    events,
                    history,
                    snapshot,
                    request,
                    self.predictor,
                    active_predictor_fingerprints,
                )
                prediction = self._with_predictor_fingerprints(
                    prediction, prediction_predictor_fingerprints
                )
            except Exception as exc:
                prediction_error = exc

            if prediction_error is not None:
                diagnostics = {
                    "status": "prediction_failed",
                    "model_name": type(self.predictor).__name__,
                    "failure_code": type(prediction_error).__name__,
                    "baseline_available": self.baseline_predictor is not None,
                }
                choice, analyzer_calls = self._decide(
                    events, diagnostics, {"use_baseline", "abort"}, analyzer_calls
                )
                if choice.action != "use_baseline" or self.baseline_predictor is None:
                    return self._failed(
                        request, history, snapshot, prediction, events,
                        "forecast_model_unavailable",
                    )
                prediction = self._step(
                    events,
                    "generate_baseline",
                    "use_baseline",
                    "use the explicitly injected baseline predictor",
                    lambda: self.baseline_predictor(history, snapshot, request),
                )
                active_predictor_fingerprints = self._predictor_fingerprints(
                    self.baseline_predictor
                )
                prediction = self._with_predictor_fingerprints(
                    prediction, active_predictor_fingerprints
                )
                prediction = self._mark_degraded(prediction, "explicit_baseline_fallback")

            try:
                prediction = self._step(
                    events,
                    "quality_gate",
                    "continue",
                    "validate exact grid, finite quantiles, bounds, and provenance",
                    lambda: quality_gate(prediction, snapshot, request),
                )
            except Exception as gate_error:
                # Weather provenance is checked before prediction and is never bypassed.
                # At this point only the model output can fail the quality gate.
                diagnostics = {
                    "status": "quality_gate_failed",
                    "model_name": prediction.model_name if prediction is not None else "unknown",
                    "failure_code": self._error_code(gate_error),
                    "baseline_available": self.baseline_predictor is not None,
                }
                choice, analyzer_calls = self._decide(
                    events, diagnostics, {"use_baseline", "abort"}, analyzer_calls
                )
                if (
                    choice.action != "use_baseline"
                    or self.baseline_predictor is None
                    or prediction is None
                    or prediction.model_name == "baseline"
                ):
                    return self._failed(
                        request, history, snapshot, None, events,
                        self._error_code(gate_error),
                    )
                try:
                    prediction = self._step(
                        events,
                        "generate_baseline",
                        "use_baseline",
                        "replace a rejected model forecast with the injected baseline",
                        lambda: self.baseline_predictor(history, snapshot, request),
                    )
                    active_predictor_fingerprints = self._predictor_fingerprints(
                        self.baseline_predictor
                    )
                    prediction = self._with_predictor_fingerprints(
                        prediction, active_predictor_fingerprints
                    )
                    prediction = self._mark_degraded(prediction, "quality_gate_baseline_fallback")
                    prediction = self._step(
                        events,
                        "quality_gate",
                        "continue",
                        "validate baseline grid, finite quantiles, bounds, and provenance",
                        lambda: quality_gate(prediction, snapshot, request),
                    )
                except Exception as baseline_error:
                    return self._failed(
                        request, history, snapshot, None, events,
                        self._error_code(baseline_error),
                    )

            if used_cached_weather:
                prediction = self._mark_degraded(
                    prediction, "cached_weather_after_fetch_failure"
                )
            diagnostics = self._analysis_diagnostics(prediction, snapshot)
            choice, analyzer_calls = self._decide(
                events,
                diagnostics,
                {"continue", "finalize", "abort"},
                analyzer_calls,
            )
            if choice.action == "abort":
                return self._failed(
                    request, history, snapshot, None, events,
                    "analysis_aborted",
                )

            return self._persist(
                request, history, snapshot, prediction, events,
                status=self._success_status(prediction, snapshot),
                reason=self._result_reason(diagnostics, choice.action),
            )
        except Exception as exc:
            if events and events[-1]["state"] == "persist_run":
                raise
            code = self._error_code(exc)
            return self._failed(request, history, snapshot, None, events, code)

    def _generate_forecast(
        self,
        events: list[dict[str, Any]],
        history: pd.DataFrame,
        snapshot: WeatherSnapshot,
        request: RunRequest,
        predictor: Any,
        initial_predictor_fingerprints: dict[str, str],
    ) -> tuple[Prediction, dict[str, str]]:
        """Call staged predictors and return the identity for the model actually used."""
        stages = ("prepare", "train_or_load", "predict")
        if not all(callable(getattr(predictor, name, None)) for name in stages):
            prediction = self._step(
                events,
                "generate_forecast",
                "continue",
                "opaque predictor call; feature preparation and model loading are not observable",
                lambda: predictor(history, snapshot, request),
            )
            return prediction, initial_predictor_fingerprints

        features = self._step(
            events,
            "prepare_features",
            "continue",
            "prepare point-in-time model features",
            lambda: predictor.prepare(history, snapshot, request),
        )
        model_bundle = self._step(
            events,
            "train_or_load_model",
            "continue",
            "train or load the configured model bundle",
            lambda: predictor.train_or_load(history, features, snapshot, request),
        )
        model_fingerprints = self._predictor_fingerprints(predictor)
        prediction = self._step(
            events,
            "generate_forecast",
            "continue",
            "predict with the prepared features and model bundle",
            lambda: predictor.predict(model_bundle, features, request),
        )
        return prediction, model_fingerprints

    @staticmethod
    def _predictor_fingerprints(predictor: Any) -> dict[str, str]:
        """Hash an explicitly declared staged-predictor config/model identity."""
        declared = getattr(predictor, "reuse_identity", None)
        if callable(declared):
            try:
                declared = declared()
            except Exception:
                return {}
        if not isinstance(declared, dict):
            config = getattr(predictor, "config_fingerprint", None)
            if config is None:
                config = getattr(predictor, "model_parameters", None)
            model = getattr(predictor, "model_identity", None)
            if model is None:
                model = getattr(predictor, "model_name", None)
            declared = {"config": config, "model": model}
        config = declared.get("config", declared.get("config_fingerprint"))
        model = declared.get("model", declared.get("model_identity"))
        if config is None or model is None:
            return {}

        stages: dict[str, Any] = {}
        for name in ("prepare", "train_or_load", "predict"):
            method = getattr(predictor, name, None)
            function = getattr(method, "__func__", method)
            code = getattr(function, "__code__", None)
            if code is not None:
                stages[name] = _code_fingerprint(code)
        if not stages:
            function = predictor
            if getattr(function, "__code__", None) is None:
                function = getattr(predictor, "__call__", predictor)
            code = getattr(function, "__code__", None)
            if code is not None:
                stages["call"] = _code_fingerprint(code)
        try:
            config_hash = _canonical_hash(config)
            model_hash = _canonical_hash(
                {
                    "model": model,
                    "predictor_type": f"{type(predictor).__module__}.{type(predictor).__qualname__}",
                    "stages": stages,
                }
            )
        except (TypeError, ValueError):
            return {}
        return {
            "predictor_config": config_hash,
            "predictor_model": model_hash,
            "predictor_identity": _canonical_hash(
                {"config": config_hash, "model": model_hash}
            ),
        }

    @staticmethod
    def _with_predictor_fingerprints(
        prediction: Prediction, fingerprints: dict[str, str]
    ) -> Prediction:
        diagnostics = dict(prediction.diagnostics or {})
        diagnostics["agent_predictor_fingerprints"] = dict(fingerprints)
        return Prediction(prediction.rows.copy(), prediction.model_name, diagnostics)

    @staticmethod
    def _update_fingerprints(
        history: pd.DataFrame,
        snapshot: WeatherSnapshot,
        predictor_fingerprints: dict[str, str],
    ) -> dict[str, str]:
        return {
            "data_contents": _frame_fingerprint(history),
            "canonical_weather": _snapshot_fingerprint(snapshot),
            **predictor_fingerprints,
        }

    def _check_for_updates(
        self,
        events: list[dict[str, Any]],
        request: RunRequest,
        snapshot: WeatherSnapshot,
        fingerprints: dict[str, str],
    ) -> RunResult | None:
        lookup = getattr(self.store, "check_for_updates", None)
        can_reuse = callable(lookup) and all(
            key in fingerprints
            for key in ("data_contents", "canonical_weather", "predictor_config", "predictor_model")
        )
        if not can_reuse:
            self._event(
                events,
                "check_for_updates",
                "continue",
                "reuse_identity_unavailable; forecast_required",
                0.0,
                "skipped",
                {"fingerprints": fingerprints, "reuse_outcome": "unavailable"},
            )
            return None
        try:
            result = self._step(
                events,
                "check_for_updates",
                "continue",
                "compare history, weather, config, and model fingerprints",
                lambda: lookup(request, snapshot, fingerprints),
            )
        except Exception:
            events[-1]["reason"] = "update_check_failed; forecast_required"
            return None
        if result is None:
            events[-1]["reason"] = "fingerprints_changed; forecast_required"
            events[-1]["details"] = {
                "fingerprints": fingerprints,
                "reuse_outcome": "miss",
            }
            return None
        events[-1]["reason"] = "matching_forecast_reused"
        events[-1]["details"] = {
            "fingerprints": fingerprints,
            "reuse_outcome": "hit",
            "run_id": result.run_id,
        }
        return result

    @staticmethod
    def _result_reason(diagnostics: dict[str, Any], action: str) -> str:
        status = diagnostics.get("status", "validated_result")
        return f"validated_action:{action}; deterministic_result:{status}"

    def _step(
        self,
        events: list[dict[str, Any]],
        state: str,
        action: str,
        reason: str,
        operation: Callable[[], Any],
    ) -> Any:
        start = time.perf_counter()
        try:
            result = operation()
        except Exception as exc:
            self._event(
                events, state, "abort", self._error_code(exc),
                time.perf_counter() - start, "failed",
            )
            raise
        self._event(
            events, state, action, reason,
            time.perf_counter() - start, "success",
        )
        return result

    def _decide(
        self,
        events: list[dict[str, Any]],
        diagnostics: dict[str, Any],
        allowed: set[str],
        call_count: int,
    ) -> tuple[Decision, int]:
        fallback = self._fallback_decision(diagnostics, allowed)
        if call_count >= _MAX_ANALYZER_CALLS:
            self._event(
                events, "analyze_result", fallback.action,
                "deterministic_fallback:llm_call_limit", 0.0, "skipped",
            )
            return fallback, call_count

        start = time.perf_counter()
        try:
            decision = self.analyzer(diagnostics, set(allowed))
        except Exception as exc:
            self._event(
                events, "analyze_result", fallback.action,
                f"analyzer_unavailable:{type(exc).__name__}",
                time.perf_counter() - start, "failed",
            )
            return fallback, call_count + 1
        if (
            not isinstance(decision, Decision)
            or decision.action not in allowed
            or not isinstance(decision.reason, str)
            or len(decision.reason) > 2000
        ):
            self._event(
                events, "analyze_result", fallback.action,
                "deterministic_fallback:invalid_decision",
                time.perf_counter() - start, "rejected",
            )
            return fallback, call_count + 1
        fallback_used = decision.reason.startswith("deterministic_fallback:")
        self._event(
            events, "analyze_result", decision.action,
            f"validated_action:{decision.action}",
            time.perf_counter() - start, "skipped" if fallback_used else "success",
        )
        return decision, call_count + 1

    @staticmethod
    def _fallback_decision(diagnostics: dict[str, Any], allowed: set[str]) -> Decision:
        if "use_baseline" in allowed and diagnostics.get("baseline_available"):
            return Decision("use_baseline", "deterministic_fallback:validated_baseline_available")
        if "finalize" in allowed:
            return Decision("finalize", "deterministic_fallback:quality_gate_passed")
        if "continue" in allowed:
            return Decision("continue", "deterministic_fallback:continue_validated_run")
        return Decision("abort", "deterministic_fallback:no_safe_action")

    @staticmethod
    def _analysis_diagnostics(prediction: Prediction, snapshot: WeatherSnapshot) -> dict[str, Any]:
        gate = prediction.diagnostics.get("quality_gate", {})
        return {
            "status": "quality_gate_passed",
            "model_name": prediction.model_name,
            "metrics": prediction.diagnostics.get("metrics", {}),
            "coverage": prediction.diagnostics.get("coverage", {}),
            "quality_gate": gate,
            "weather_provenance_status": snapshot.provenance.get("provenance_status"),
        }

    @staticmethod
    def _success_status(prediction: Prediction, snapshot: WeatherSnapshot) -> str:
        gate = prediction.diagnostics.get("quality_gate", {})
        diagnostics = prediction.diagnostics
        model_lower = prediction.model_name.lower()
        degraded = (
            "baseline" in model_lower
            or bool(prediction.diagnostics.get("degraded", False))
            or int(gate.get("quantile_correction_count", 0)) > 0
            or int(gate.get("clipping_count", 0)) > 0
            or int(diagnostics.get("quantile_crossing_correction_count", 0)) > 0
            or int(diagnostics.get("quantile_clipping_count", 0)) > 0
            or snapshot.provenance.get("provenance_status") != "verified"
        )
        return "degraded" if degraded else "success"

    @staticmethod
    def _mark_degraded(prediction: Prediction, reason: str) -> Prediction:
        diagnostics = dict(prediction.diagnostics or {})
        diagnostics["degraded"] = True
        diagnostics["fallback_reason"] = reason
        return Prediction(prediction.rows.copy(), prediction.model_name, diagnostics)

    def _failed(
        self,
        request: RunRequest,
        history: pd.DataFrame,
        snapshot: WeatherSnapshot | None,
        prediction: Prediction | None,
        events: list[dict[str, Any]],
        reason: str,
    ) -> RunResult:
        return self._persist(
            request, history, snapshot, prediction, events,
            status="failed", reason=reason,
        )

    def _persist(
        self,
        request: RunRequest,
        history: pd.DataFrame,
        snapshot: WeatherSnapshot | None,
        prediction: Prediction | None,
        events: list[dict[str, Any]],
        status: str,
        reason: str,
    ) -> RunResult:
        metrics = (
            prediction.diagnostics.get("metrics", {})
            if prediction is not None
            else {"schema_version": 1, "metric_status": "unavailable_no_labels", "models": [], "coverage": {}}
        )
        manifest = self._manifest(request, history, snapshot, prediction, status, reason)
        self._event(events, "persist_run", "continue", reason, 0.0, "success")
        persist_event = events[-1]
        persist_started = time.perf_counter()
        result = self.store.persist(
            request, prediction, snapshot, metrics, events,
            self._report(request, snapshot, prediction, status, reason), manifest,
        )
        persist_event["timestamp"] = _iso_now()
        persist_event["duration_ms"] = max(0, int((time.perf_counter() - persist_started) * 1000))
        persist_event["outcome"] = "success"
        return result

    def _manifest(
        self,
        request: RunRequest,
        history: pd.DataFrame,
        snapshot: WeatherSnapshot | None,
        prediction: Prediction | None,
        status: str,
        reason: str,
    ) -> dict[str, Any]:
        diagnostics = prediction.diagnostics if prediction is not None else {}
        model_name = prediction.model_name if prediction is not None else "unavailable"
        model_params = diagnostics.get("model_parameters", {})
        seed = diagnostics.get("seed")
        training_cutoff = diagnostics.get("training_cutoff")
        training_rows = diagnostics.get("training_rows")
        calibration_cutoff = diagnostics.get("calibration_cutoff")
        feature_schema = diagnostics.get("feature_schema", {})
        config_identity = {
            "config_fingerprint": diagnostics.get("config_fingerprint"),
            "model_parameters": model_params,
            "seed": seed,
            "feature_schema": feature_schema,
            "analyzer_model": getattr(getattr(self.analyzer, "settings", None), "openai_model", None),
            "analyzer_effort": getattr(
                getattr(self.analyzer, "settings", None), "openai_reasoning_effort", None
            ),
        }
        data_hash = _frame_fingerprint(history)
        model_rows_hash = diagnostics.get("model_training_rows_fingerprint") or _canonical_hash(
            {"training_rows": training_rows, "training_cutoff": training_cutoff, "data": data_hash}
        )
        weather_hash = _snapshot_fingerprint(snapshot) if snapshot is not None else None
        provenance_status = (
            snapshot.provenance.get("provenance_status") if snapshot is not None else None
        )
        return {
            "schema_version": 1,
            "run_id": "",
            "forecast_origin": pd.Timestamp(request.origin).tz_convert("UTC").isoformat().replace("+00:00", "Z"),
            "horizon": request.horizon,
            "mode": request.mode,
            "status": status,
            "competition_valid": bool(
                status != "failed"
                and request.mode == "competition"
                and provenance_status == "verified"
            ),
            "created_at": _iso_now(),
            "parent_run_id": None,
            "fingerprints": {
                "data_contents": data_hash,
                "canonical_weather": weather_hash,
                "config": _canonical_hash(config_identity),
                "feature_schema": _canonical_hash(feature_schema),
                "model_training_rows": model_rows_hash,
                **_jsonable(diagnostics.get("agent_predictor_fingerprints", {})),
            },
            "model": {
                "name": model_name,
                "parameters": _jsonable(model_params),
                "seed": _jsonable(seed),
                "training_cutoff": _jsonable(training_cutoff),
                "training_rows": _jsonable(training_rows),
                "calibration_cutoff": _jsonable(calibration_cutoff),
                "dependency_versions": _jsonable(
                    diagnostics.get("dependency_versions") or _dependency_versions()
                ),
            },
            "weather_provenance": _jsonable(snapshot.provenance) if snapshot is not None else {},
            "data_quality": {
                **_jsonable(diagnostics.get("data_quality", {})),
                "history_rows": len(history),
                "latest_available_at": self._latest_available_at(history),
                "run_reason": self._safe_reason(reason),
            },
            "calibration": _jsonable(diagnostics.get("calibration", {})),
            "files": {},
        }

    @staticmethod
    def _latest_available_at(history: pd.DataFrame) -> str | None:
        if "available_at" not in history.columns or history.empty:
            return None
        try:
            latest = pd.to_datetime(history["available_at"], utc=True, errors="coerce").max()
        except (TypeError, ValueError):
            return None
        return latest.isoformat().replace("+00:00", "Z") if pd.notna(latest) else None

    @staticmethod
    def _report(
        request: RunRequest,
        snapshot: WeatherSnapshot | None,
        prediction: Prediction | None,
        status: str,
        reason: str,
    ) -> str:
        provenance = snapshot.provenance.get("provenance_status", "unavailable") if snapshot else "unavailable"
        model = prediction.model_name if prediction is not None else "no prediction produced"
        return (
            f"# Forecast run report\n\n"
            f"- Status: `{status}`\n"
            f"- Mode: `{request.mode}`\n"
            f"- Forecast origin: `{pd.Timestamp(request.origin).isoformat()}`\n"
            f"- Horizon: `{request.horizon}` hours\n"
            f"- Model: `{model}`\n"
            f"- Weather provenance: `{provenance}`\n"
            f"- Competition valid: `{request.mode == 'competition' and provenance == 'verified' and status != 'failed'}`\n"
            f"- Agent outcome: `{ForecastAgent._safe_reason(reason)}`\n"
        )

    @staticmethod
    def _safe_reason(value: str) -> str:
        clean = " ".join(str(value).replace("\x00", "").split())
        return clean[:2000]

    @staticmethod
    def _error_code(exc: Exception) -> str:
        if isinstance(exc, ValueError) and str(exc) and len(str(exc)) <= 120:
            candidate = str(exc).split(":", 1)[0].split()[0]
            if candidate in _SAFE_ERROR_CODES:
                return candidate
        return f"operation_failed:{type(exc).__name__}"

    @staticmethod
    def _event(
        events: list[dict[str, Any]],
        state: str,
        action: str,
        reason: str,
        duration_seconds: float,
        outcome: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        event = {
            "schema_version": 1,
            "sequence": len(events) + 1,
            "state": state,
            "action": action,
            "reason": ForecastAgent._safe_reason(reason),
            "timestamp": _iso_now(),
            "duration_ms": max(0, int(duration_seconds * 1000)),
            "outcome": outcome,
        }
        if details is not None:
            event["details"] = details
        events.append(event)
