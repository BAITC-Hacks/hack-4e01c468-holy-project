from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from wind_forecast.config import Settings
from wind_forecast.contracts import Decision
from wind_forecast.llm import OpenAIAnalyzer


class _Responses:
    def __init__(self, output: list[Any]) -> None:
        self.output = output
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(output=self.output)


class _Client:
    def __init__(self, output: list[Any]) -> None:
        self.responses = _Responses(output)


def _call(name: str = "choose_action", arguments: str = '{"action":"finalize","reason":"gate passed"}'):
    return SimpleNamespace(type="function_call", name=name, arguments=arguments)


def test_openai_analyzer_uses_one_strict_named_tool_and_configured_model() -> None:
    client = _Client([_call()])
    settings = Settings(openai_api_key="not-a-real-key", openai_model="configured-model",
                        openai_reasoning_effort="none", http_timeout_seconds=9)
    analyzer = OpenAIAnalyzer(settings, client=client)

    decision = analyzer(
        {"status": "quality_gate_passed", "model_name": "catboost",
         "metrics": {"mae": 0.12, "debug_secret": "must-not-be-sent"},
         "api_key": "must-not-be-sent", "raw_csv": "must-not-be-sent"},
        {"finalize", "abort"},
    )

    assert decision == Decision("finalize", "gate passed")
    call = client.responses.calls[0]
    assert call["model"] == "configured-model"
    assert call["reasoning"] == {"effort": "none"}
    assert call["parallel_tool_calls"] is False
    assert call["tool_choice"] == {"type": "function", "name": "choose_action"}
    assert call["tools"] == [
        {
            "type": "function",
            "name": "choose_action",
            "description": "Choose a permitted workflow action from computed diagnostics.",
            "strict": True,
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "action": {"type": "string", "enum": ["abort", "finalize"]},
                    "reason": {"type": "string"},
                },
                "required": ["action", "reason"],
            },
        }
    ]
    request_input = call["input"]
    serialized = request_input if isinstance(request_input, str) else json.dumps(request_input)
    assert "must-not-be-sent" not in serialized
    assert "quality_gate_passed" in serialized


def test_openai_analyzer_rejects_an_unknown_tool_and_uses_safe_finalization() -> None:
    analyzer = OpenAIAnalyzer(
        Settings(openai_api_key="unused"),
        client=_Client([_call(name="write_file", arguments='{"action":"finalize","reason":"x"}')]),
    )

    decision = analyzer({"status": "quality_gate_passed"}, {"finalize", "abort"})

    assert decision.action == "finalize"
    assert decision.reason == "deterministic_fallback:invalid_tool_response"


def test_openai_analyzer_rejects_actions_outside_allowlist() -> None:
    analyzer = OpenAIAnalyzer(
        Settings(openai_api_key="unused"),
        client=_Client([_call(arguments='{"action":"continue","reason":"skip gate"}')]),
    )

    decision = analyzer({"status": "quality_gate_passed"}, {"finalize", "abort"})

    assert decision == Decision("finalize", "deterministic_fallback:invalid_tool_response")


def test_openai_analyzer_rejects_malformed_json_and_overlong_reason() -> None:
    malformed = OpenAIAnalyzer(Settings(openai_api_key="unused"), client=_Client([_call(arguments="{" )]))
    too_long = OpenAIAnalyzer(
        Settings(openai_api_key="unused"),
        client=_Client([_call(arguments=json.dumps({"action": "finalize", "reason": "x" * 2001}))]),
    )

    assert malformed({"status": "quality_gate_passed"}, {"finalize"}).reason == (
        "deterministic_fallback:invalid_tool_response"
    )
    assert too_long({"status": "quality_gate_passed"}, {"finalize"}).reason == (
        "deterministic_fallback:invalid_tool_response"
    )


def test_missing_api_key_uses_deterministic_action_without_network_call() -> None:
    analyzer = OpenAIAnalyzer(Settings(openai_api_key=""))

    decision = analyzer(
        {"status": "prediction_failed", "baseline_available": True},
        {"use_baseline", "abort"},
    )

    assert decision.action == "use_baseline"
    assert decision.reason == "deterministic_fallback:api_unavailable"


def test_sdk_client_is_created_without_automatic_retries(monkeypatch: Any) -> None:
    import openai

    constructed: list[dict[str, Any]] = []
    fake_client = _Client([_call()])

    def fake_openai(**kwargs: Any) -> Any:
        constructed.append(kwargs)
        return fake_client

    monkeypatch.setattr(openai, "OpenAI", fake_openai)
    analyzer = OpenAIAnalyzer(
        Settings(openai_api_key="not-a-real-key", http_timeout_seconds=7),
    )

    analyzer({"status": "quality_gate_passed"}, {"finalize"})

    assert constructed == [{"api_key": "not-a-real-key", "timeout": 7, "max_retries": 0}]
