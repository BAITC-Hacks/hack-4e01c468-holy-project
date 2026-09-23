"""Streamlit entry point for the wind-farm forecast dashboard."""

from __future__ import annotations


def render(app) -> None:
    """Render the dashboard against the shared application service."""
    from wind_forecast.ui.dashboard import render_dashboard

    render_dashboard(app)


def main() -> None:
    """Construct the shared application lazily for dashboard execution."""
    from wind_forecast.config import Settings
    from wind_forecast.service import Application

    render(Application(Settings.from_env()))


if __name__ == "__main__":
    main()
