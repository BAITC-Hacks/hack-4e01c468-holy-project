"""Consistent Plotly figures for forecast and turbine location data."""

from __future__ import annotations

from collections.abc import Iterable

import pandas as pd
import plotly.graph_objects as go


_REQUIRED_FORECAST_COLUMNS = {"turbine_id", "valid_time", "p10", "p50", "p90"}
_TURBINE_STYLE = {
    "turbine_1": {"label": "Turbine 1", "color": "#087F73", "fill": "rgba(8,127,115,0.13)"},
    "turbine_2": {"label": "Turbine 2", "color": "#2563EB", "fill": "rgba(37,99,235,0.12)"},
}


def _values(frame: pd.DataFrame, column: str) -> list[float]:
    """Return finite, bounded power values for display."""
    values = pd.to_numeric(frame[column], errors="coerce")
    return values.where(values.between(0, 1)).tolist()


def build_forecast_chart(forecast: pd.DataFrame) -> go.Figure:
    """Build p10–p90 bands and p50 curves for each turbine and farm proxy."""
    missing = _REQUIRED_FORECAST_COLUMNS - set(forecast.columns)
    if missing:
        raise ValueError(f"forecast is missing chart columns: {', '.join(sorted(missing))}")

    frame = forecast.loc[:, ["turbine_id", "valid_time", "p10", "p50", "p90"]].copy()
    frame["valid_time"] = pd.to_datetime(frame["valid_time"], utc=True, errors="coerce")
    for column in ("p10", "p50", "p90"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["valid_time", "p10", "p50", "p90"])
    frame = frame.loc[
        frame[["p10", "p50", "p90"]].ge(0).all(axis=1)
        & frame[["p10", "p50", "p90"]].le(1).all(axis=1)
    ]
    frame = frame.sort_values("valid_time")

    figure = go.Figure()
    for turbine_id, group in frame.groupby("turbine_id", sort=True):
        style = _TURBINE_STYLE.get(
            str(turbine_id),
            {"label": str(turbine_id), "color": "#526871", "fill": "rgba(82,104,113,0.12)"},
        )
        figure.add_trace(
            go.Scatter(
                x=group["valid_time"], y=group["p10"], mode="lines",
                line={"width": 0}, hoverinfo="skip", showlegend=False,
                legendgroup=str(turbine_id), name=f"{style['label']} p10",
            )
        )
        figure.add_trace(
            go.Scatter(
                x=group["valid_time"], y=group["p90"], mode="lines",
                line={"width": 0}, fill="tonexty", fillcolor=style["fill"],
                hoverinfo="skip", showlegend=False, legendgroup=str(turbine_id),
                name=f"{style['label']} p10–p90",
            )
        )
        figure.add_trace(
            go.Scatter(
                x=group["valid_time"], y=group["p50"], mode="lines+markers",
                line={"color": style["color"], "width": 2.6},
                marker={"size": 5}, legendgroup=str(turbine_id),
                name=f"{style['label']} · p50",
                customdata=group["turbine_id"],
                hovertemplate=(
                    "%{customdata}<br>%{x|%d %b %H:%M UTC}"
                    "<br>Normalized power: %{y:.1%}<extra></extra>"
                ),
            )
        )

    complete_hours = frame.groupby("valid_time")["turbine_id"].nunique()
    complete_times = complete_hours[complete_hours == 2].index
    farm = frame.loc[frame["valid_time"].isin(complete_times)].groupby("valid_time", as_index=False)[
        ["p10", "p50", "p90"]
    ].mean()
    if not farm.empty:
        figure.add_trace(
            go.Scatter(
                x=farm["valid_time"], y=farm["p10"], mode="lines",
                line={"width": 0}, hoverinfo="skip", showlegend=False,
                legendgroup="farm-proxy", name="Farm proxy p10",
            )
        )
        figure.add_trace(
            go.Scatter(
                x=farm["valid_time"], y=farm["p90"], mode="lines",
                line={"width": 0}, fill="tonexty", fillcolor="rgba(20,45,53,0.10)",
                hoverinfo="skip", showlegend=False, legendgroup="farm-proxy",
                name="Farm proxy p10–p90",
            )
        )
        figure.add_trace(
            go.Scatter(
                x=farm["valid_time"], y=farm["p50"], mode="lines+markers",
                line={"color": "#142D35", "width": 2.4, "dash": "dash"},
                marker={"size": 4}, legendgroup="farm-proxy", name="Farm proxy · p50",
                hovertemplate=(
                    "Farm proxy · p50<br>%{x|%d %b %H:%M UTC}"
                    "<br>Mean normalized power: %{y:.1%}<extra></extra>"
                ),
            )
        )

    figure.update_layout(
        height=390,
        margin={"l": 56, "r": 20, "t": 18, "b": 55},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#FFFFFF",
        font={"family": "ui-sans-serif, system-ui, sans-serif", "color": "#526871", "size": 12},
        legend={"orientation": "h", "y": 1.12, "x": 0, "font": {"size": 11}},
        hoverlabel={"bgcolor": "#102B34", "font": {"color": "#FFFFFF"}},
        xaxis={
            "title": "Valid time · UTC",
            "showgrid": False,
            "tickformat": "%d %b\n%H:%M",
            "linecolor": "#D9E3E6",
            "automargin": True,
        },
        yaxis={
            "title": "Normalized active power",
            "range": [0, 1],
            "tickformat": ".0%",
            "gridcolor": "#E8EFF1",
            "zeroline": False,
        },
    )
    return figure


def build_site_map() -> go.Figure:
    """Plot the two fixed turbine coordinates without a remote map tile source."""
    longitudes = [78.535604, 78.538828]
    latitudes = [43.645150, 43.643198]
    padding = 0.0012
    figure = go.Figure(
        go.Scatter(
            x=longitudes,
            y=latitudes,
            mode="markers+text",
            text=["Turbine 1", "Turbine 2"],
            textposition=["top left", "bottom right"],
            marker={"size": 15, "color": ["#087F73", "#2563EB"], "line": {"width": 3, "color": "#FFFFFF"}},
            customdata=[[43.645150, 78.535604], [43.643198, 78.538828]],
            hovertemplate="%{text}<br>%{customdata[0]:.6f}° N, %{customdata[1]:.6f}° E<extra></extra>",
            showlegend=False,
        )
    )
    figure.update_layout(
        height=240,
        margin={"l": 10, "r": 10, "t": 10, "b": 32},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#F4F7F8",
        font={"family": "ui-sans-serif, system-ui, sans-serif", "color": "#526871", "size": 10},
        xaxis={"title": "Longitude · °E", "range": [min(longitudes) - padding, max(longitudes) + padding], "showgrid": True, "gridcolor": "#E1E9EB", "ticksuffix": "°"},
        yaxis={"title": "Latitude · °N", "range": [min(latitudes) - padding, max(latitudes) + padding], "showgrid": True, "gridcolor": "#E1E9EB", "ticksuffix": "°", "scaleanchor": "x", "scaleratio": 1},
    )
    return figure
