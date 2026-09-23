"""Small accessible presentation helpers for the forecast dashboard."""

from __future__ import annotations

import html
from pathlib import Path


def load_stylesheet() -> str:
    """Read the dashboard's authored CSS from its package directory."""
    return (Path(__file__).with_name("theme.css")).read_text(encoding="utf-8")


def escaped(value: object) -> str:
    """Escape values interpolated into dashboard-owned HTML."""
    return html.escape(str(value), quote=True)


def metric_card(label: str, value: str, note: str = "") -> str:
    """Render a compact metric card with escaped text content."""
    return (
        '<div class="wf-metric-card">'
        f'<div class="wf-eyebrow">{escaped(label)}</div>'
        f'<div class="wf-metric-value">{escaped(value)}</div>'
        f'<div class="wf-metric-note">{escaped(note)}</div>'
        "</div>"
    )


def detail_card(label: str, value: str, note: str = "") -> str:
    """Render an escaped two-line detail card."""
    return (
        '<div class="wf-detail-card">'
        f'<div class="wf-eyebrow">{escaped(label)}</div>'
        f'<div class="wf-detail-value">{escaped(value)}</div>'
        f'<div class="wf-detail-note">{escaped(note)}</div>'
        "</div>"
    )


def status_badge(status: str) -> str:
    """Render the run state as text and a state-specific visual badge."""
    safe_status = escaped(status)
    css_state = status.lower() if status.lower() in {"success", "degraded", "failed"} else "unknown"
    return f'<span class="wf-status wf-status-{css_state}">{safe_status}</span>'
