"""Streamlit composition for the five wind-farm operations views."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd

from wind_forecast.contracts import parse_request
from wind_forecast.ui.charts import build_forecast_chart, build_site_map
from wind_forecast.ui.components import (
    escaped,
    load_stylesheet,
    metric_card,
    status_badge,
)


_SECTIONS = ("Overview", "Forecast", "Backtest", "Agent Trace", "Data Quality")
_SECTION_LABELS = {
    "Overview": "◫  Обзор",
    "Forecast": "⌁  Прогноз",
    "Backtest": "▥  Бэктест",
    "Agent Trace": "◇  Журнал агента",
    "Data Quality": "◎  Качество данных",
}
_SECTION_TITLES = {
    "Overview": "Обзор прогноза",
    "Forecast": "Прогноз",
    "Backtest": "Бэктест",
    "Agent Trace": "Журнал агента",
    "Data Quality": "Качество данных",
}
_SECTION_COPY = {
    "Overview": "Ожидаемая выработка ветропарка Алматы с проверкой происхождения данных.",
    "Forecast": "Почасовой прогноз для каждой турбины с интервалами неопределённости.",
    "Backtest": "Качество модели за периоды, где доступны фактические данные турбин.",
    "Agent Trace": "Порядок решений и событий, сохранённых во время расчёта.",
    "Data Quality": "Полнота входных данных, происхождение и проверка времени.",
}


def _iso_label(value: Any) -> str:
    """Format timestamps with an explicit timezone suffix."""
    if value is None:
        return "Недоступно"
    try:
        parsed = pd.Timestamp(value)
    except (TypeError, ValueError):
        return "Недоступно"
    if parsed.tzinfo is None:
        return "Часовой пояс не указан"
    return parsed.tz_convert("UTC").strftime("%d %b %Y · %H:%M UTC")


def _metric_rows(metrics: Mapping[str, Any]) -> pd.DataFrame:
    """Build the documented long-form model metrics table."""
    rows = metrics.get("models", [])
    if not isinstance(rows, list) or not rows:
        return pd.DataFrame()
    return pd.DataFrame.from_records(rows)


def _observation_age_hours(manifest: Mapping[str, Any], quality: Mapping[str, Any]) -> float | None:
    """Return the recorded age or derive it at the forecast's point-in-time origin."""
    age = quality.get("latest_observation_age_hours")
    if isinstance(age, (int, float)):
        return float(age)

    latest_available_at = quality.get("latest_available_at")
    forecast_origin = manifest.get("forecast_origin")
    if latest_available_at is None or forecast_origin is None:
        return None
    try:
        latest = pd.Timestamp(latest_available_at)
        origin = pd.Timestamp(forecast_origin)
    except (TypeError, ValueError):
        return None
    if latest.tzinfo is None or origin.tzinfo is None:
        return None
    return (origin.tz_convert("UTC") - latest.tz_convert("UTC")).total_seconds() / 3600


def _render_page_heading(st, section: str) -> None:
    st.markdown(
        f'<div class="wf-kicker"><span class="wf-kicker-line"></span> WIND POWER / OPERATIONS</div>'
        f'<div class="wf-title-row"><div><h1 class="wf-title">{_SECTION_TITLES[section]}</h1>'
        f'<div class="wf-subtitle">{_SECTION_COPY[section]}</div></div></div>',
        unsafe_allow_html=True,
    )


def _render_topbar(st, section: str) -> None:
    st.markdown(
        '<div id="overview" class="wf-topbar"><div class="wf-breadcrumbs">Объекты <span>/</span> '
        f'ВЭС Алматы <span>/</span> <strong>{_SECTION_TITLES[section]}</strong></div>'
        '<div class="wf-topbar-right"><span class="wf-demo-pill">ALMATY · UTC+5</span>'
        '<span class="wf-avatar" aria-label="Рабочее пространство Windline">W</span></div></div>',
        unsafe_allow_html=True,
    )


def _render_controls(st, app) -> None:
    """Render labeled controls and validate requests before service calls."""
    with st.container(border=True, key="wf-control-card"):
        st.markdown('<div class="wf-eyebrow">НОВЫЙ РАСЧЁТ</div>', unsafe_allow_html=True)
        st.markdown('<div class="wf-panel-title">Новый прогноз</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="wf-panel-copy">Выберите точку отсчёта и длительность расчёта.</div>',
            unsafe_allow_html=True,
        )
        st.text_input(
            "Начало прогноза (ISO 8601)",
            value="2026-02-01T00:00:00+05:00",
            key="wf_origin",
            help="Укажите целый час и часовой пояс, например +05:00.",
        )
        st.selectbox(
            "Горизонт",
            [24, 48],
            index=1,
            format_func=lambda hours: f"{hours} часа" if hours == 24 else f"{hours} часов",
            key="wf_horizon",
        )
        busy = bool(st.session_state.get("wf_running", False))
        run_clicked = st.button(
            "Запустить прогноз", type="primary", width="stretch",
            key="wf_run", disabled=busy,
        )
        refresh_clicked = st.button(
            "Обновить погоду", width="stretch",
            key="wf_refresh", disabled=busy,
        )

    if not (run_clicked or refresh_clicked):
        return

    mode = getattr(getattr(app, "settings", None), "mode", "demo")
    try:
        request = parse_request(
            st.session_state["wf_origin"], st.session_state["wf_horizon"], mode
        )
    except (TypeError, ValueError) as exc:
        st.error(f"Проверьте параметры прогноза: {exc}")
        return

    st.session_state["wf_running"] = True
    try:
        with st.spinner("Проверяем погоду и рассчитываем прогноз…"):
            result = app.run(request, refresh=refresh_clicked)
        st.session_state["wf_run_result"] = result
        st.session_state["wf_run_id"] = result.run_id
        st.session_state["wf_selection_initialized"] = True
        st.session_state["wf_reused"] = result.reused
        st.session_state["wf_last_action"] = "refresh" if refresh_clicked else "run"
        st.session_state["wf_last_attempt_failed"] = False
    except Exception as exc:  # UI boundary: do not expose arbitrary service details.
        st.session_state["wf_run_result"] = None
        st.session_state["wf_run_id"] = None
        st.session_state["wf_selection_initialized"] = True
        st.session_state["wf_reused"] = False
        st.session_state["wf_last_action"] = None
        st.session_state["wf_last_attempt_failed"] = True
        reason_code = getattr(exc, "reason_code", "run_failed")
        st.error(
            f"Не удалось завершить расчёт ({reason_code}). "
            "Откройте журнал агента и повторите попытку."
        )
    finally:
        st.session_state["wf_running"] = False


def _render_status(st, result, manifest: Mapping[str, Any]) -> None:
    status = getattr(result, "status", None) or manifest.get("status", "unknown")
    st.markdown(status_badge(str(status)), unsafe_allow_html=True)
    warnings = []
    if status == "degraded":
        warnings.append("Расчёт завершён с ограничениями")
    elif status == "failed":
        st.error("Расчёт завершился с ошибкой. Результаты этого запуска нельзя использовать.")
    if manifest.get("mode") == "demo":
        warnings.append("Демо-данные не подтверждают качество для соревнования")
    elif manifest.get("competition_valid") is False:
        warnings.append("Происхождение данных не подтверждено для соревнования")
    if warnings:
        st.warning(" · ".join(warnings) + ". Проверьте модель и источник погоды перед использованием.")
    if manifest.get("mode") == "demo":
        st.caption("ДЕМО-РЕЖИМ · Не использовать как подтверждённый результат.")

    if (
        st.session_state.get("wf_last_action") == "refresh"
        and status in {"success", "degraded"}
        and st.session_state.get("wf_reused")
    ):
        st.info("Входные данные не изменились. Та же версия прогноза остаётся актуальной.")
    elif (
        st.session_state.get("wf_last_action") == "refresh"
        and result is not None
        and status in {"success", "degraded"}
    ):
        st.success("Входные данные изменились. Новая версия прогноза готова.")
    st.session_state["wf_last_action"] = None


def _render_summary_metrics(st, run_data: Mapping[str, Any] | None) -> None:
    """Render operational facts only; unavailable metrics remain explicitly unavailable."""
    run_data = run_data or {}
    manifest = run_data.get("manifest") or {}
    metrics = run_data.get("metrics") or {}
    quality = manifest.get("data_quality") or {}
    horizon = manifest.get("horizon")
    age = _observation_age_hours(manifest, quality)
    metric_status = metrics.get("metric_status", "unavailable_no_labels")
    has_metrics = not str(metric_status).startswith("unavailable")
    cards = [
        metric_card(
            "Горизонт прогноза",
            f"{horizon} часов" if isinstance(horizon, int) else "—",
            "Для каждой турбины",
        ),
        metric_card("Шаг прогноза", "1 час", "Почасовой расчёт"),
        metric_card(
            "Бэктест",
            "Доступен" if has_metrics else "Нет метрик",
            "Сравнение по фактическим данным" if has_metrics else "Для февраля нет меток турбин",
        ),
        metric_card(
            "Последнее наблюдение",
            f"{age:g} ч" if isinstance(age, (int, float)) else "—",
            "Возраст на момент прогноза",
        ),
    ]
    with st.container(key="wf-overview-metrics"):
        columns = st.columns(4, gap="small")
        for column, card in zip(columns, cards, strict=True):
            column.markdown(card, unsafe_allow_html=True)


def _render_forecast_panel(st, result, run_data: Mapping[str, Any] | None) -> None:
    with st.container(border=True, key="wf-forecast-panel"):
        _render_forecast_panel_content(st, result, run_data)


def _render_forecast_panel_content(st, result, run_data: Mapping[str, Any] | None) -> None:
    run_data = run_data or {}
    forecast = run_data.get("forecast")
    manifest = run_data.get("manifest") or {}
    status = getattr(result, "status", manifest.get("status", "unknown"))
    st.markdown(
        '<div class="wf-eyebrow">ПРОГНОЗ ВЫРАБОТКИ</div>'
        '<div class="wf-panel-title">Почасовая мощность обеих турбин</div>'
        '<div class="wf-panel-copy">p50 — медианный прогноз, заливка показывает диапазон p10–p90.</div>',
        unsafe_allow_html=True,
    )
    if status == "failed":
        return
    if not isinstance(forecast, pd.DataFrame) or forecast.empty:
        st.info("В этом запуске пока нет строк прогноза.")
        return
    st.plotly_chart(
        build_forecast_chart(forecast),
        width="stretch",
        config={"displayModeBar": False, "responsive": True},
        key="wf_overview_forecast",
    )
    st.markdown(
        '<div class="wf-chart-note"><span aria-hidden="true">i</span>'
        'Медиана объединённого профиля — среднее двух нормированных турбин. '
        'Интервал p10–p90 не является калиброванным интервалом ветропарка.</div>',
        unsafe_allow_html=True,
    )


def _render_site_panel(st, result, run_data: Mapping[str, Any] | None) -> None:
    with st.container(border=True, key="wf-site-panel"):
        _render_site_panel_content(st, result, run_data)


def _render_site_panel_content(st, result, run_data: Mapping[str, Any] | None) -> None:
    run_data = run_data or {}
    manifest = run_data.get("manifest") or {}
    weather = manifest.get("weather_provenance") or {}
    status = getattr(result, "status", manifest.get("status", "unknown"))
    weather_status = weather.get("provenance_status", "неизвестно")
    st.markdown(
        '<div class="wf-eyebrow">ПРОИСХОЖДЕНИЕ ДАННЫХ</div>'
        '<div class="wf-panel-title">Погода и объект</div>'
        '<div class="wf-panel-copy">ВЭС Алматы · две турбины · Казахстан</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="wf-source-row"><span>Погодная модель</span>'
        f'<strong>{escaped(weather.get("weather_model", "Не указана"))}</strong></div>'
        '<div class="wf-source-row"><span>Проверка по времени</span>'
        f'<strong>{escaped(weather_status)}</strong></div>',
        unsafe_allow_html=True,
    )
    if manifest and status != "failed":
        st.plotly_chart(
            build_site_map(),
            width="stretch",
            config={"displayModeBar": False, "responsive": True},
            key="wf_site_map",
        )
    st.markdown(
        '<div class="wf-location-list">'
        '<div><i class="wf-site-dot wf-site-dot-one"></i><span><strong>Турбина 01</strong>'
        '<small>43.645150° N · 78.535604° E</small></span></div>'
        '<div><i class="wf-site-dot wf-site-dot-two"></i><span><strong>Турбина 02</strong>'
        '<small>43.643198° N · 78.538828° E</small></span></div></div>',
        unsafe_allow_html=True,
    )


def _render_forecast(st, result, run_data: Mapping[str, Any]) -> None:
    forecast = run_data.get("forecast")
    manifest = run_data.get("manifest") or {}
    if getattr(result, "status", manifest.get("status")) == "failed":
        st.error("Кривые прогноза недоступны: запуск завершился с ошибкой.")
        return
    if not isinstance(forecast, pd.DataFrame) or forecast.empty:
        st.info("В этом запуске нет строк прогноза.")
        return
    st.markdown(
        '<div class="wf-panel-title">Прогноз турбин и неопределённость</div>'
        '<div class="wf-panel-copy">Сплошные линии p50 — медианный прогноз; заливка показывает p10–p90.</div>',
        unsafe_allow_html=True,
    )
    st.plotly_chart(
        build_forecast_chart(forecast),
        width="stretch",
        config={"displayModeBar": False, "responsive": True},
        key="wf_forecast_detail",
    )
    st.warning(
        "Показатель ветропарка — среднее нормированных значений, не МВт и не МВт·ч. "
        "Среднее квантилей турбин не является калиброванным интервалом для их суммы."
    )
    csv_bytes = forecast.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Скачать прогноз CSV",
        data=csv_bytes,
        file_name="wind-forecast.csv",
        mime="text/csv",
        key="wf_download_forecast",
    )


def _render_backtest(st, metrics: Mapping[str, Any]) -> None:
    metric_status = metrics.get("metric_status", "unavailable_no_labels")
    evaluation_period = metrics.get("evaluation_period")
    if str(metric_status).startswith("unavailable"):
        st.info(
            "Метрики бэктеста недоступны: для февраля нет фактических меток турбин. "
            "Оценки не подменяются нулями и рейтинг за февраль не рассчитывается."
        )
        return
    st.markdown(
        '<div class="wf-panel-title">Исторический бэктест</div>'
        f'<div class="wf-panel-copy">Период оценки: {_iso_label(evaluation_period.get("start")) if isinstance(evaluation_period, dict) else "Недоступен"} '
        f'— {_iso_label(evaluation_period.get("end")) if isinstance(evaluation_period, dict) else "Недоступен"}</div>',
        unsafe_allow_html=True,
    )
    table = _metric_rows(metrics)
    if table.empty:
        st.info("Для этого периода нет метрик модели по горизонтам прогноза.")
    else:
        st.dataframe(table, width="stretch", hide_index=True)
    coverage = metrics.get("coverage")
    if coverage:
        st.caption(f"Общее покрытие оценки: {coverage}")


def _render_trace(st, run_data: Mapping[str, Any]) -> None:
    events = run_data.get("events") or []
    st.markdown(
        '<div class="wf-panel-title">Хронология событий агента</div>'
        '<div class="wf-panel-copy">События показаны в сохранённом порядке из журнала запуска.</div>',
        unsafe_allow_html=True,
    )
    if events:
        frame = pd.DataFrame.from_records(events)
        wanted = [column for column in ("sequence", "state", "action", "reason", "duration_ms", "outcome", "timestamp") if column in frame]
        st.dataframe(frame.loc[:, wanted], width="stretch", hide_index=True)
    else:
        st.info("В этом запуске нет записанных событий агента.")
    report = run_data.get("report")
    if report:
        with st.expander("Сводка запуска", expanded=False):
            st.text(str(report))


def _render_quality(st, manifest: Mapping[str, Any]) -> None:
    weather = manifest.get("weather_provenance") or {}
    quality = manifest.get("data_quality") or {}
    leakage_status = quality.get("leakage_status", "unavailable")
    competition_valid = manifest.get("competition_valid")
    if competition_valid is False:
        st.warning("Это демо-запуск или происхождение данных не подтверждено; он не является подтверждением для соревнования.")
    st.markdown(
        '<div class="wf-panel-title">Проверка данных на момент прогноза</div>'
        f'<div class="wf-panel-copy">Проверка утечки: {escaped(leakage_status)} · Начало прогноза: {_iso_label(manifest.get("forecast_origin"))}</div>',
        unsafe_allow_html=True,
    )
    st.markdown("**Происхождение погоды**")
    if weather:
        st.dataframe(pd.DataFrame([weather]), width="stretch", hide_index=True)
    else:
        st.info("В манифесте запуска нет сведений о происхождении погоды.")
    st.markdown("**Полнота входных данных и качество наблюдений**")
    if quality:
        st.dataframe(pd.DataFrame([quality]), width="stretch", hide_index=True)
    else:
        st.info("В манифесте запуска нет сведений о качестве данных.")


def render_dashboard(app) -> None:
    """Render the dashboard using only the public Application service methods."""
    import streamlit as st

    st.set_page_config(
        page_title="Windline · Forecast desk",
        page_icon="◉",
        layout="wide",
        initial_sidebar_state="auto",
    )
    st.markdown(
        f"<style>{load_stylesheet()}"
        '@media (max-width: 640px) { [data-testid="stMainBlockContainer"] { padding-top: 2.8rem; } }'
        "</style>",
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.markdown(
            '<a class="wf-brand" href="#overview" aria-label="Windline, обзор">'
            '<span class="wf-brand-symbol" aria-hidden="true">✳</span><span>'
            '<span class="wf-brand-mark">WINDLINE<span class="wf-brand-period">.</span></span>'
            '<span class="wf-brand-sub">FORECAST DESK · ALMATY</span></span></a>',
            unsafe_allow_html=True,
        )
        st.caption("РАБОЧЕЕ ПРОСТРАНСТВО")
        st.radio(
            "Раздел панели", _SECTIONS,
            format_func=lambda section: _SECTION_LABELS[section],
            label_visibility="collapsed", key="wf_section",
        )
        st.markdown(
            '<div class="wf-side-bottom"><span class="wf-side-site-dot"></span><div>'
            '<strong>ВЭС Алматы</strong><small>2 турбины · Казахстан</small></div></div>',
            unsafe_allow_html=True,
        )

    section = st.session_state.get("wf_section", "Overview")
    _render_topbar(st, section)
    _render_page_heading(st, section)

    status_slot = metrics_slot = left_slot = source_slot = None
    if section == "Overview":
        status_slot = st.empty()
        metrics_slot = st.empty()
        with st.container(key="wf-overview-workspace"):
            left, right = st.columns([2.1, 1], gap="large")
            with left:
                left_slot = st.empty()
            with right:
                controls_slot = st.empty()
                source_slot = st.empty()
        with controls_slot.container():
            _render_controls(st, app)
    else:
        _render_controls(st, app)

    load_error = None
    if not st.session_state.get("wf_selection_initialized", False):
        try:
            result = app.latest()
        except Exception:
            result = None
            load_error = "Не удалось загрузить последний прогноз. Проверьте каталог запусков."
        st.session_state["wf_run_result"] = result
        st.session_state["wf_run_id"] = result.run_id if result is not None else None
        st.session_state["wf_selection_initialized"] = True
    result = st.session_state.get("wf_run_result")

    run_data = None
    manifest: Mapping[str, Any] = {}
    if result is not None:
        try:
            run_data = app.read_run(result.run_id)
            manifest = run_data.get("manifest") or {}
        except Exception:
            load_error = "Не удалось прочитать файлы выбранного запуска."

    if section == "Overview":
        with status_slot.container():
            if load_error:
                st.error(load_error)
            if result is not None:
                origin_label = _iso_label(manifest.get("forecast_origin"))
                st.markdown(
                    f'<div class="wf-run-meta">Начало прогноза · {escaped(origin_label)} '
                    f'&nbsp; · &nbsp; Запуск · {escaped(result.run_id)}</div>',
                    unsafe_allow_html=True,
                )
                _render_status(st, result, manifest)
            elif not load_error and not st.session_state.get("wf_last_attempt_failed"):
                st.warning("Проверьте происхождение погодных данных перед использованием результатов.")

        with metrics_slot.container():
            _render_summary_metrics(st, run_data)

        with left_slot.container():
            if result is None:
                st.markdown(
                    '<div class="wf-empty-state"><span class="wf-empty-symbol">⌁</span><div>'
                    '<strong>Прогнозная панель готова</strong><p>Выберите начало и горизонт, '
                    'чтобы рассчитать почасовую мощность двух турбин.</p></div></div>',
                    unsafe_allow_html=True,
                )
                if not st.session_state.get("wf_last_attempt_failed"):
                    st.info("Выберите начало прогноза и горизонт, затем запустите расчёт.")
            elif run_data is None:
                st.info("Данные прогноза для выбранного запуска недоступны.")
            else:
                _render_forecast_panel(st, result, run_data)

        with source_slot.container():
            _render_site_panel(st, result, run_data)
        return

    if load_error:
        st.error(load_error)
    if result is None:
        st.markdown(
            '<div class="wf-panel"><div class="wf-panel-title">Прогнозная панель готова</div>'
            '<div class="wf-panel-copy">Выберите начало и горизонт прогноза. В результате будут '
            'почасовые значения обеих турбин, происхождение погоды и журнал агента.</div></div>',
            unsafe_allow_html=True,
        )
        st.info("Выберите начало прогноза и горизонт, затем запустите расчёт.")
        return
    if run_data is None:
        return

    origin_label = _iso_label(manifest.get("forecast_origin"))
    st.markdown(
        f'<div class="wf-run-meta">Начало прогноза · {escaped(origin_label)} '
        f'&nbsp; · &nbsp; Запуск · {escaped(result.run_id)}</div>',
        unsafe_allow_html=True,
    )
    _render_status(st, result, manifest)
    if section == "Forecast":
        _render_forecast(st, result, run_data)
    elif section == "Backtest":
        _render_backtest(st, run_data.get("metrics") or {})
    elif section == "Agent Trace":
        _render_trace(st, run_data)
    elif section == "Data Quality":
        _render_quality(st, manifest)
