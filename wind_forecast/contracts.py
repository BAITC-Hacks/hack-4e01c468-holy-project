"""Shared types and request parsing for the forecast application."""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import pandas as pd

Action = Literal[
    "continue",
    "retry_weather",
    "use_cached_weather",
    "use_baseline",
    "abort",
    "finalize",
]
RunMode = Literal["competition", "demo"]
RunStatus = Literal["success", "degraded", "failed"]


@dataclass(frozen=True)
class RunRequest:
    """A timezone-aware forecast origin and the requested horizon."""

    origin: datetime
    horizon: int = 48
    mode: RunMode = "competition"


@dataclass
class WeatherSnapshot:
    """Weather input together with its raw responses and provenance."""

    rows: pd.DataFrame
    raw_responses: list[dict[str, Any]]
    fingerprint: str
    provenance: dict[str, Any]


@dataclass
class Prediction:
    """Forecast rows and model diagnostics."""

    rows: pd.DataFrame
    model_name: str
    diagnostics: dict[str, Any]


@dataclass(frozen=True)
class Decision:
    """An agent decision restricted to the declared action set."""

    action: Action
    reason: str


@dataclass
class RunResult:
    """Result of one persisted forecast run."""

    run_id: str
    directory: Path
    status: RunStatus
    reused: bool


def parse_request(
    origin: str, horizon: int, mode: RunMode = "competition"
) -> RunRequest:
    """Parse a zoned ISO-8601 origin and enforce supported run parameters."""
    try:
        value = datetime.fromisoformat(origin.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("origin must be a valid ISO-8601 datetime") from exc

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("origin must include a timezone")

    value = value.astimezone(timezone.utc)
    if value.minute or value.second or value.microsecond:
        raise ValueError("origin must be an hour boundary")
    if (
        not isinstance(horizon, int)
        or isinstance(horizon, bool)
        or horizon not in (24, 48)
        or mode not in ("competition", "demo")
    ):
        raise ValueError("invalid horizon or mode")

    return RunRequest(origin=value, horizon=horizon, mode=mode)
