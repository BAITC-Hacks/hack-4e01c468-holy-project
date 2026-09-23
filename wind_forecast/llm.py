"""OpenAI Responses adapter restricted to one validated workflow tool."""

from __future__ import annotations

import json
import math
import numbers
from typing import Any

from wind_forecast.config import Settings
from wind_forecast.contracts import Decision

_DIAGNOSTIC_KEYS = {
    "status",
    "model_name",
    "metrics",
    "coverage",
    "quality_gate",
    "weather_provenance_status",
    "baseline_available",
    "failure_code",
}


def _compact_value(key: str, value: Any, allow_string: bool = False) -> Any:
    """Keep only small scalar diagnostics, never raw rows or environment values."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value if allow_string else _DROP
    if isinstance(value, numbers.Real):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for child_key, child in value.items():
            if not isinstance(child_key, str):
                continue
            cleaned = _compact_value(child_key, child)
            if cleaned is not _DROP:
                result[child_key] = cleaned
        return result
    if isinstance(value, (list, tuple)):
        return [cleaned for item in value if (cleaned := _compact_value(key, item)) is not _DROP]
    return _DROP


class _Drop:
    pass


_DROP = _Drop()


def _compact_diagnostics(diagnostics: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in _DIAGNOSTIC_KEYS:
        if key not in diagnostics:
            continue
        value = _compact_value(
            key,
            diagnostics[key],
            allow_string=key in {"status", "model_name", "weather_provenance_status", "failure_code"},
        )
        if value is not _DROP:
            compact[key] = value
    return compact


def _fallback(diagnostics: dict[str, Any], allowed: set[str], reason: str) -> Decision:
    if "use_baseline" in allowed and diagnostics.get("baseline_available") is True:
        return Decision("use_baseline", reason)
    if "finalize" in allowed:
        return Decision("finalize", reason)
    if "continue" in allowed:
        return Decision("continue", reason)
    return Decision("abort", reason)


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


class OpenAIAnalyzer:
    """Choose one permitted transition, with deterministic outage/malformed fallbacks."""

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self.settings = settings
        self.client = client

    def __call__(self, diagnostics: dict[str, Any], allowed: set[str]) -> Decision:
        compact = _compact_diagnostics(diagnostics)
        safe_allowed = {action for action in allowed if action in {
            "continue", "retry_weather", "use_cached_weather", "use_baseline", "abort", "finalize"
        }}
        if not safe_allowed:
            return Decision("abort", "deterministic_fallback:no_allowed_action")
        if self.client is None and not self.settings.openai_api_key:
            return _fallback(compact, safe_allowed, "deterministic_fallback:api_unavailable")
        if not self.settings.openai_model or not self.settings.openai_reasoning_effort:
            return _fallback(compact, safe_allowed, "deterministic_fallback:model_configuration_unavailable")

        try:
            client = self.client or self._create_client()
            response = client.responses.create(
                model=self.settings.openai_model,
                reasoning={"effort": self.settings.openai_reasoning_effort},
                input=json.dumps(compact, ensure_ascii=False, sort_keys=True, allow_nan=False),
                tools=[self._tool(sorted(safe_allowed))],
                tool_choice={"type": "function", "name": "choose_action"},
                parallel_tool_calls=False,
            )
        except Exception:
            return _fallback(compact, safe_allowed, "deterministic_fallback:api_unavailable")

        calls = [item for item in (_field(response, "output", []) or []) if _field(item, "type") == "function_call"]
        if len(calls) != 1 or _field(calls[0], "name") != "choose_action":
            return _fallback(compact, safe_allowed, "deterministic_fallback:invalid_tool_response")
        arguments = _field(calls[0], "arguments")
        try:
            parsed = json.loads(arguments) if isinstance(arguments, str) else None
        except (TypeError, json.JSONDecodeError):
            parsed = None
        if (
            not isinstance(parsed, dict)
            or set(parsed) != {"action", "reason"}
            or parsed.get("action") not in safe_allowed
            or not isinstance(parsed.get("reason"), str)
            or len(parsed["reason"]) > 2000
        ):
            return _fallback(compact, safe_allowed, "deterministic_fallback:invalid_tool_response")
        return Decision(parsed["action"], parsed["reason"])

    def _create_client(self) -> Any:
        from openai import OpenAI

        return OpenAI(
            api_key=self.settings.openai_api_key,
            timeout=self.settings.http_timeout_seconds,
            max_retries=0,
        )

    @staticmethod
    def _tool(actions: list[str]) -> dict[str, Any]:
        return {
            "type": "function",
            "name": "choose_action",
            "description": "Choose a permitted workflow action from computed diagnostics.",
            "strict": True,
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "action": {"type": "string", "enum": actions},
                    "reason": {"type": "string"},
                },
                "required": ["action", "reason"],
            },
        }
