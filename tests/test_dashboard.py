from __future__ import annotations

from datetime import datetime, timezone
from itertools import chain
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from wind_forecast.contracts import RunResult


class FakeApplication:
    def __init__(
        self,
        initial: RunResult | None = None,
        *,
        next_result: RunResult | None = None,
        next_error: Exception | None = None,
    ):
        self.calls: list[tuple[object, bool]] = []
        self.latest_calls = 0
        self.read_run_ids: list[str] = []
        self.current = initial
        self.next_result = next_result
        self.next_error = next_error
        self.data = {
            "run-degraded": sample_run_data("degraded"),
            "run-same": sample_run_data("degraded"),
            "run-failed": sample_run_data("failed"),
            "run-new": sample_run_data("success"),
        }

    def latest(self) -> RunResult | None:
        self.latest_calls += 1
        return self.current

    def read_run(self, run_id: str) -> dict:
        self.read_run_ids.append(run_id)
        return self.data[run_id]

    def run(self, request, refresh: bool = False) -> RunResult:
        self.calls.append((request, refresh))
        if self.next_error is not None:
            raise self.next_error
        if self.next_result is None:
            raise AssertionError("this fixture does not define a forecast result")
        run_data = self.data[self.next_result.run_id]
        run_data["manifest"]["run_id"] = self.next_result.run_id
        run_data["manifest"]["forecast_origin"] = request.origin.isoformat()
        run_data["manifest"]["horizon"] = request.horizon
        if self.next_result.status != "failed":
            self.current = self.next_result
        return self.next_result


def sample_result(run_id: str, status: str = "degraded", reused: bool = False) -> RunResult:
    return RunResult(run_id, Path("/tmp") / run_id, status, reused)


def sample_run_data(status: str) -> dict:
    origin = datetime(2026, 2, 1, tzinfo=timezone.utc)
    rows = []
    for turbine_id, center in (("turbine_1", 0.42), ("turbine_2", 0.56)):
        for lead in range(3):
            value = center + lead * 0.02
            rows.append(
                {
                    "run_id": "fixture-run",
                    "forecast_origin": origin,
                    "turbine_id": turbine_id,
                    "valid_time": origin + pd.Timedelta(hours=lead),
                    "lead_hours": lead,
                    "p10": value - 0.08,
                    "p50": value,
                    "p90": value + 0.08,
                    "weather_issued_at": origin - pd.Timedelta(hours=6),
                    "weather_model": "fixture-weather",
                    "run_status": status,
                }
            )
    return {
        "forecast": pd.DataFrame(rows),
        "metrics": {
            "metric_status": "unavailable_no_labels",
            "evaluation_period": None,
            "models": [],
            "coverage": {},
        },
        "manifest": {
            "run_id": "fixture-run",
            "forecast_origin": origin.isoformat(),
            "horizon": 24,
            "mode": "demo",
            "status": status,
            "competition_valid": False,
            "model": {"name": "persistence-baseline"},
            "weather_provenance": {"provenance_status": "synthetic"},
            "data_quality": {
                "latest_observation_age_hours": 744,
                "leakage_status": "passed",
                "missing_hours": 0,
            },
        },
        "events": [],
        "report": "Deterministic fixture forecast.",
    }


def render_fake(app):
    from app import render

    render(app)


def test_empty_dashboard_explains_how_to_start_a_forecast() -> None:
    app = AppTest.from_function(render_fake, args=(FakeApplication(),)).run()

    assert not app.exception
    assert any("Выберите начало прогноза" in item.value for item in app.info)
    assert {item.label for item in app.button} >= {"Запустить прогноз", "Обновить погоду"}


def test_naive_forecast_origin_is_rejected_before_calling_application() -> None:
    service = FakeApplication()
    app = AppTest.from_function(render_fake, args=(service,)).run()
    app.text_input[0].set_value("2026-02-01T00:00:00").run()
    app.button[0].click().run()

    assert not app.exception
    assert service.calls == []
    assert any("timezone" in item.value.lower() for item in app.error)


def test_demo_run_shows_both_turbines_and_degraded_state() -> None:
    result = sample_result("run-degraded")
    service = FakeApplication(next_result=result)
    app = AppTest.from_function(render_fake, args=(service,)).run()
    app.button[0].click().run()

    assert not app.exception
    assert service.calls[0][0].mode == "demo"
    rendered = " ".join(item.value for item in app.markdown)
    assert "degraded" in rendered.lower()
    assert "обе турбины" in rendered.lower()
    assert "run-degraded" in rendered
    assert any("demo mode" in item.value.lower() for item in app.caption)
    assert app.session_state["wf_run_id"] == "run-degraded"
    assert len(app.get("plotly_chart")) == 2


def test_refresh_with_reused_artifacts_keeps_the_run_and_says_it_is_unchanged() -> None:
    current = sample_result("run-same")
    reused = sample_result("run-same", reused=True)
    service = FakeApplication(initial=current, next_result=reused)
    app = AppTest.from_function(render_fake, args=(service,)).run()
    app.button[1].click().run()

    assert not app.exception
    assert service.calls[0][1] is True
    assert app.session_state["wf_run_id"] == "run-same"
    assert app.session_state["wf_run_result"] == reused
    assert service.latest_calls == 1
    assert service.read_run_ids[-1] == "run-same"
    visible_text = " ".join(item.value for item in chain(app.markdown, app.info, app.success))
    assert "та же версия прогноза" in visible_text.lower()


def test_backtest_page_reports_unavailable_february_truth_without_zero_metrics() -> None:
    service = FakeApplication(initial=sample_result("run-degraded"))
    app = AppTest.from_function(render_fake, args=(service,)).run()
    app.radio[0].set_value("Backtest").run()

    visible_text = " ".join(item.value for item in chain(app.markdown, app.info, app.warning))
    assert "недоступны" in visible_text.lower()
    assert "февраля" in visible_text.lower() and "меток" in visible_text.lower()
    assert "0.000" not in visible_text


def test_forecast_chart_has_two_turbine_curves_and_a_normalized_farm_proxy() -> None:
    from wind_forecast.ui.charts import build_forecast_chart

    figure = build_forecast_chart(sample_run_data("degraded")["forecast"])
    names = {trace.name for trace in figure.data}

    assert "Turbine 1 · p50" in names
    assert "Turbine 2 · p50" in names
    assert "Farm proxy · p50" in names
    assert all(0 <= value <= 1 for trace in figure.data for value in trace.y)
    farm_p50 = next(trace for trace in figure.data if trace.name == "Farm proxy · p50")
    assert list(farm_p50.y) == [0.49, 0.51, 0.53]


def test_site_map_uses_the_two_turbine_source_coordinates() -> None:
    from wind_forecast.ui.charts import build_site_map

    figure = build_site_map()

    assert list(figure.data[0].x) == [78.535604, 78.538828]
    assert list(figure.data[0].y) == [43.645150, 43.643198]


def test_site_map_points_labels_toward_the_open_plot_area() -> None:
    from wind_forecast.ui.charts import build_site_map

    figure = build_site_map()

    assert list(figure.data[0].textposition) == ["top right", "bottom left"]


def test_page_title_is_semantic_and_precedes_forecast_controls() -> None:
    app = AppTest.from_function(render_fake, args=(FakeApplication(),)).run()
    rendered = "\n".join(item.value for item in app.markdown)

    title_position = rendered.index('<h1 class="wf-title">Обзор прогноза</h1>')
    controls_position = rendered.index("Новый расчёт")

    assert title_position < controls_position


def test_latest_observation_age_uses_manifest_availability_when_age_is_missing() -> None:
    service = FakeApplication(initial=sample_result("run-degraded"))
    quality = service.data["run-degraded"]["manifest"]["data_quality"]
    quality.pop("latest_observation_age_hours")
    quality["latest_available_at"] = "2026-01-31T23:00:00Z"

    app = AppTest.from_function(render_fake, args=(service,)).run()

    rendered = "\n".join(item.value for item in app.markdown)
    assert "1 ч" in rendered


def test_overview_does_not_repeat_run_state_as_a_metric_card() -> None:
    service = FakeApplication(initial=sample_result("run-degraded"))
    app = AppTest.from_function(render_fake, args=(service,)).run()

    rendered = "\n".join(item.value for item in app.markdown)
    assert "wf-status-degraded" in rendered
    assert "Run state" not in rendered


def test_overview_shows_four_operational_metrics_including_hourly_step() -> None:
    service = FakeApplication(initial=sample_result("run-degraded"))
    app = AppTest.from_function(render_fake, args=(service,)).run()

    rendered = "\n".join(item.value for item in app.markdown)
    assert rendered.count('class="wf-metric-card"') == 4
    assert "Шаг прогноза" in rendered
    assert "1 час" in rendered


def test_dashboard_metric_card_escapes_user_supplied_html() -> None:
    from wind_forecast.ui.components import metric_card

    rendered = metric_card("Weather source", "<img src=x onerror=alert(1)>")

    assert "<img" not in rendered
    assert "&lt;img" in rendered


def test_failed_latest_run_does_not_render_forecast_charts() -> None:
    service = FakeApplication(initial=sample_result("run-failed", status="failed"))
    app = AppTest.from_function(render_fake, args=(service,)).run()

    assert not app.exception
    assert any("расчёт завершился с ошибкой" in item.value.lower() for item in app.error)
    assert len(app.get("plotly_chart")) == 0


@pytest.mark.parametrize(
    ("button_index", "refresh", "reused"),
    [(0, False, False), (1, True, False), (1, True, True)],
)
def test_failed_attempt_selects_its_trace_without_showing_previous_success(
    button_index: int, refresh: bool, reused: bool
) -> None:
    previous = sample_result("run-new", status="success")
    failed = sample_result("run-failed", status="failed", reused=reused)
    service = FakeApplication(initial=previous, next_result=failed)
    service.data["run-failed"]["events"] = [
        {
            "schema_version": 1,
            "run_id": "run-failed",
            "sequence": 1,
            "state": "finalize",
            "action": "abort",
            "reason": "failed run trace: run-failed",
            "timestamp": "2026-02-01T00:00:00Z",
            "duration_ms": 8,
            "outcome": "failed",
        }
    ]
    app = AppTest.from_function(render_fake, args=(service,)).run()
    app.button[button_index].click().run()

    assert not app.exception
    assert service.calls[0][1] is refresh
    assert service.latest_calls == 1
    assert service.read_run_ids[-1] == "run-failed"
    assert app.session_state["wf_run_id"] == "run-failed"
    assert app.session_state["wf_run_result"] == failed
    rendered = " ".join(item.value for item in app.markdown)
    assert "run-failed" in rendered
    assert "run-new" not in rendered
    assert "wf-status-failed" in rendered
    assert not app.success
    assert not app.info
    assert len(app.get("plotly_chart")) == 0

    app.radio[0].set_value("Agent Trace").run()
    assert not app.exception
    assert service.read_run_ids[-1] == "run-failed"
    trace = pd.concat([item.value for item in app.dataframe], ignore_index=True)
    assert trace.iloc[0]["action"] == "abort"
    assert trace.iloc[0]["reason"] == "failed run trace: run-failed"


def test_changed_refresh_shows_new_run_version() -> None:
    service = FakeApplication(
        initial=sample_result("run-same"),
        next_result=sample_result("run-new", status="success"),
    )
    app = AppTest.from_function(render_fake, args=(service,)).run()
    app.button[1].click().run()

    assert not app.exception
    assert app.session_state["wf_run_id"] == "run-new"
    assert app.session_state["wf_run_result"] == service.next_result
    assert service.latest_calls == 1
    assert service.read_run_ids[-1] == "run-new"
    assert any("новая версия прогноза" in item.value.lower() for item in app.success)


def test_failed_run_exception_does_not_reselect_the_previous_success() -> None:
    previous = sample_result("run-new", status="success")
    service = FakeApplication(initial=previous, next_error=RuntimeError("private failure detail"))
    app = AppTest.from_function(render_fake, args=(service,)).run()
    app.button[0].click().run()

    assert not app.exception
    assert any("не удалось завершить" in item.value.lower() for item in app.error)
    assert service.latest_calls == 1
    assert app.session_state["wf_run_id"] is None
    assert app.session_state["wf_run_result"] is None
    rendered = " ".join(item.value for item in app.markdown)
    assert "run-new" not in rendered
    assert '<span class="wf-status-success">' not in rendered
    assert "Начало прогноза ·" not in rendered
    assert not app.success
    assert len(app.get("plotly_chart")) == 0


def test_run_uses_the_returned_artifact_origin_and_horizon() -> None:
    selected = sample_result("run-new", status="success")
    service = FakeApplication(next_result=selected)
    app = AppTest.from_function(render_fake, args=(service,)).run()
    app.text_input[0].set_value("2026-02-03T06:00:00+05:00")
    app.button[0].click().run()

    assert not app.exception
    rendered = " ".join(item.value for item in app.markdown)
    assert "03 Feb 2026 · 01:00 UTC" in rendered
    assert "48 часов" in rendered
    assert service.read_run_ids[-1] == "run-new"


def test_agent_trace_renders_persisted_event_order() -> None:
    service = FakeApplication(initial=sample_result("run-degraded"))
    service.data["run-degraded"]["events"] = [
        {
            "schema_version": 1,
            "run_id": "run-degraded",
            "sequence": 1,
            "state": "fetch_weather",
            "action": "continue",
            "reason": "load point-in-time weather",
            "timestamp": "2026-02-01T00:00:00Z",
            "duration_ms": 12,
            "outcome": "success",
        }
    ]
    app = AppTest.from_function(render_fake, args=(service,)).run()
    app.radio[0].set_value("Agent Trace").run()

    assert not app.exception
    assert len(app.dataframe) == 1
    assert app.dataframe[0].value.iloc[0]["action"] == "continue"


def test_data_quality_page_keeps_synthetic_provenance_visible() -> None:
    service = FakeApplication(initial=sample_result("run-degraded"))
    app = AppTest.from_function(render_fake, args=(service,)).run()
    app.radio[0].set_value("Data Quality").run()

    rendered = " ".join(item.value for item in chain(app.markdown, app.warning, app.caption))
    tables = " ".join(str(item.value) for item in app.dataframe)
    assert "synthetic" in tables.lower()
    assert "не является подтверждением для соревнования" in rendered.lower()
