"""Small replaceable contracts consumed by application runtime wiring."""

from typing import Any, Protocol

from wind_forecast.contracts import Decision, RunRequest, WeatherSnapshot


class WeatherSource(Protocol):
    """Fetch one weather snapshot for a forecast request."""

    def fetch(self, request: RunRequest, refresh: bool = False) -> WeatherSnapshot: ...


class DiagnosticAnalyzer(Protocol):
    """Choose an allowed workflow decision from computed diagnostics."""

    def __call__(self, diagnostics: dict[str, Any], allowed: set[str]) -> Decision: ...
