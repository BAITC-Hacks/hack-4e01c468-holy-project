"""Allowlisted application settings loaded from environment variables."""

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from dotenv import load_dotenv

from wind_forecast.contracts import RunMode


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _configured_path(name: str, default: Path, root: Path) -> Path:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    path = Path(value).expanduser()
    return path if path.is_absolute() else root / path


@dataclass(frozen=True)
class Settings:
    """Runtime settings. The API key is excluded from repr and comparisons."""

    root_dir: Path = field(default_factory=_project_root)
    data_dir: Path = field(default_factory=_project_root)
    cache_dir: Path = field(default_factory=lambda: _project_root() / ".cache/wind")
    model_dir: Path = field(
        default_factory=lambda: _project_root() / "artifacts/models"
    )
    run_dir: Path = field(default_factory=lambda: _project_root() / "artifacts/runs")
    fixture_dir: Path = field(
        default_factory=lambda: _project_root() / "tests/fixtures/weather"
    )
    openai_api_key: str = field(default="", repr=False, compare=False)
    openai_model: str = "gpt-6-luna"
    openai_reasoning_effort: str = "none"
    http_timeout_seconds: float = 20.0
    mode: RunMode = "competition"

    @classmethod
    def from_env(cls, root_dir: Path | None = None) -> "Settings":
        """Load only known application keys, with relative paths rooted at the project."""
        root = (root_dir or _project_root()).expanduser().resolve()
        load_dotenv(dotenv_path=root / ".env", override=False)

        try:
            timeout = float(os.environ.get("HTTP_TIMEOUT_SECONDS", "20"))
        except ValueError as exc:
            raise ValueError("HTTP_TIMEOUT_SECONDS must be a positive number") from exc
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("HTTP_TIMEOUT_SECONDS must be a positive number")

        mode = os.environ.get("RUN_MODE", "competition").strip() or "competition"
        if mode not in ("competition", "demo"):
            raise ValueError("RUN_MODE must be 'competition' or 'demo'")

        return cls(
            root_dir=root,
            data_dir=_configured_path("DATA_DIR", root, root),
            cache_dir=_configured_path("CACHE_DIR", root / ".cache/wind", root),
            model_dir=_configured_path(
                "MODEL_DIR", root / "artifacts/models", root
            ),
            run_dir=_configured_path("RUN_DIR", root / "artifacts/runs", root),
            fixture_dir=_configured_path(
                "WEATHER_FIXTURE_DIR", root / "tests/fixtures/weather", root
            ),
            openai_api_key=os.environ.get("OPENAI_API_KEY", "").strip(),
            openai_model=os.environ.get("OPENAI_MODEL", "gpt-6-luna").strip()
            or "gpt-6-luna",
            openai_reasoning_effort=os.environ.get(
                "OPENAI_REASONING_EFFORT", "none"
            ).strip()
            or "none",
            http_timeout_seconds=timeout,
            mode=cast(RunMode, mode),
        )
