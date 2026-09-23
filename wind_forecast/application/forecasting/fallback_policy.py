"""Deterministic decisions used when no external advisor is configured."""

from typing import Any

from wind_forecast.contracts import Decision


class DeterministicAnalyzer:
    """Select only safe agent actions and never make an external request."""

    def __call__(self, diagnostics: dict[str, Any], allowed: set[str]) -> Decision:
        if (
            "use_cached_weather" in allowed
            and diagnostics.get("cached_weather_available") is True
        ):
            return Decision(
                "use_cached_weather", "deterministic_fallback:validated_cached_weather"
            )
        if "use_baseline" in allowed and diagnostics.get("baseline_available") is True:
            return Decision("use_baseline", "deterministic_fallback:model_unavailable")
        if "finalize" in allowed:
            return Decision("finalize", "deterministic_fallback:validated_forecast")
        if "continue" in allowed:
            return Decision("continue", "deterministic_fallback:continue_validated_run")
        return Decision("abort", "deterministic_fallback:no_safe_action")
