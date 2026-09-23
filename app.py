"""Streamlit entry point for the wind-farm forecast dashboard."""

from __future__ import annotations

import os


def render(app) -> None:
    """Render the dashboard against the shared application service."""
    from wind_forecast.ui.dashboard import render_dashboard

    render_dashboard(app)


def main() -> None:
    """Construct the shared application lazily for dashboard execution."""
    from wind_forecast.config import Settings
    from wind_forecast.service import Application

    settings = Settings.from_env()
    offline_setting = os.environ.get("DEMO_OFFLINE", "0").strip()
    if offline_setting not in {"0", "1"}:
        raise ValueError("DEMO_OFFLINE must be '0' or '1'")
    offline = offline_setting == "1"
    if offline and settings.mode != "demo":
        raise ValueError("DEMO_OFFLINE requires RUN_MODE=demo")
    render(Application(settings, offline=offline))


if __name__ == "__main__":
    main()
