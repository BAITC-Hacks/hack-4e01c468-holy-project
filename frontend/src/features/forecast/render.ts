import { apiErrorMessage, apiModeLabel, forecastSummary, formatPercentage, runStatusLabel } from '../../lib/dashboard-model.mjs';
import { forecastCsvUrl } from '../../lib/api';
import { escapeHtml, renderForecastChart } from './chart.mjs';
import { asRecord, displayValue, formatAlmaty, turbineName } from '../../lib/format';
import { text } from '../../lib/preferences.mjs';
import type { ForecastRow, HealthView, RunView } from '../../lib/types';

function setText(selector: string, value: string): void {
  const node = document.querySelector<HTMLElement>(selector);
  if (node) node.textContent = value;
}

function updateBadge(node: Element | null, label: string, outcome: string): void {
  if (!node) return;
  node.textContent = label;
  node.setAttribute('data-outcome', outcome);
}

function updateStat(name: string, labelKey: string, value: string, locale: 'ru' | 'kk'): void {
  const stat = document.querySelector<HTMLElement>(`[data-stat="${name}"]`);
  const label = stat?.querySelector<HTMLElement>('[data-slot="stat-label"]');
  const output = stat?.querySelector<HTMLElement>('[data-slot="stat-value"]');
  if (label) label.textContent = text(locale, labelKey);
  if (output) output.textContent = value;
}

function forecastRows(run: RunView | null): ForecastRow[] {
  return run?.forecast ?? [];
}

function getOrigin(run: RunView): unknown {
  const manifest = asRecord(run.manifest);
  return manifest.forecast_origin ?? run.forecast[0]?.forecast_origin ?? null;
}

function weatherName(run: RunView | null, locale: 'ru' | 'kk'): string {
  if (!run) return text(locale, 'unavailable');
  const manifestWeather = asRecord(asRecord(run.manifest).weather_provenance);
  const rowWeather = run.forecast.find((row) => typeof row.weather_model === 'string')?.weather_model;
  return displayValue(manifestWeather.weather_model ?? rowWeather, locale);
}

function alertContent(
  run: RunView | null,
  health: HealthView | null,
  failure: string | null,
  locale: 'ru' | 'kk',
): { outcome: string; message: string } | null {
  if (failure) return { outcome: 'failed', message: apiErrorMessage(failure, locale) };
  if (!run) return { outcome: 'info', message: text(locale, 'noSavedRunAlert') };
  if (run.status === 'failed') {
    return { outcome: 'failed', message: text(locale, 'forecastFailedAlert') };
  }
  if (run.status === 'degraded') {
    const model = asRecord(asRecord(run.manifest).model).name;
    if (typeof model === 'string' && ['persistence', 'seasonal', 'power_curve'].includes(model)) {
      return { outcome: 'degraded', message: text(locale, 'baselineDegradedAlert', { model }) };
    }
    return { outcome: 'degraded', message: text(locale, 'forecastDegradedAlert') };
  }
  const competitionValid = asRecord(run.manifest).competition_valid;
  if (health?.mode === 'demo' || health?.offline) {
    return { outcome: 'info', message: text(locale, 'demoAlert') };
  }
  if (competitionValid === false) {
    return { outcome: 'degraded', message: text(locale, 'eligibilityAlert') };
  }
  return null;
}

function showAlert(run: RunView | null, health: HealthView | null, failure: string | null, locale: 'ru' | 'kk'): void {
  const alert = document.querySelector<HTMLElement>('[data-run-alert]');
  const message = alert?.querySelector<HTMLElement>('[data-run-alert-text]');
  const retry = alert?.querySelector<HTMLButtonElement>('[data-latest-retry]');
  const content = alertContent(run, health, failure, locale);
  if (!alert || !message) return;
  if (!content) {
    alert.hidden = true;
    return;
  }
  alert.hidden = false;
  alert.dataset.outcome = content.outcome;
  message.textContent = content.message;
  if (retry) retry.hidden = !failure;
}

function formatRow(row: ForecastRow, locale: 'ru' | 'kk'): string {
  const time = escapeHtml(formatAlmaty(row.valid_time, locale));
  const turbine = escapeHtml(turbineName(row.turbine_id, locale));
  const lead = escapeHtml(displayValue(row.lead_hours, locale));
  return `<tr><td><time>${time}</time></td><td>${turbine}</td><td>${lead === text(locale, 'unavailable') ? lead : `${lead} ${text(locale, 'unitHours')}`}</td><td>${escapeHtml(formatPercentage(row.p10, locale))}</td><td class="median-cell">${escapeHtml(formatPercentage(row.p50, locale))}</td><td>${escapeHtml(formatPercentage(row.p90, locale))}</td></tr>`;
}

function renderForecastTable(run: RunView | null, failure: string | null, locale: 'ru' | 'kk'): void {
  const body = document.querySelector<HTMLElement>('[data-forecast-rows]');
  const count = document.querySelector<HTMLElement>('[data-record-count]');
  const rows = forecastRows(run);
  if (count) count.textContent = failure ? text(locale, 'recordsUnavailable') : text(locale, 'rowCount', { count: rows.length });
  if (body) {
    const emptyMessage = failure
      ? apiErrorMessage(failure, locale)
      : text(locale, 'noForecastRows');
    body.innerHTML = rows.length > 0
      ? rows.map((row) => formatRow(row, locale)).join('')
      : `<tr><td colspan="6" class="empty-cell">${escapeHtml(emptyMessage)}</td></tr>`;
  }

  const hasDownload = Boolean(run && rows.length > 0);
  for (const selector of ['[data-csv-link]', '[data-table-csv-link]']) {
    const link = document.querySelector<HTMLAnchorElement>(selector);
    if (!link) continue;
    link.hidden = !hasDownload;
    if (hasDownload && run) {
      link.href = forecastCsvUrl(run.run_id);
      link.download = `${run.run_id}-forecast.csv`;
    }
  }
}

export function renderForecastViews(
  run: RunView | null,
  health: HealthView | null,
  failure: string | null = null,
  locale: 'ru' | 'kk' = 'ru',
): void {
  const statusLabel = run ? runStatusLabel(run.status, locale) : failure ? text(locale, 'apiFailed') : text(locale, 'noSavedRun');
  const outcome = run?.status ?? (failure ? 'failed' : 'unknown');
  updateBadge(document.querySelector('.heading-outcome .run-status'), statusLabel, outcome);
  updateBadge(document.querySelector('.chart-context-detail .run-status'), run ? runStatusLabel(run.status, locale) : text(locale, 'noRun'), outcome);
  updateBadge(document.querySelector('.context-heading .run-status'), run ? runStatusLabel(run.status, locale) : text(locale, 'noRun'), outcome);

  const modeBadge = document.querySelector<HTMLElement>('[data-mode-badge]');
  if (modeBadge && health) modeBadge.textContent = apiModeLabel(health.mode, health.offline, locale);
  setText('[data-run-id]', run?.run_id ?? '—');
  setText('[data-selected-run]', run?.run_id ?? (failure ? text(locale, 'apiUnavailableAlert') : text(locale, 'noRunSelected')));
  setText('[data-run-origin]', run ? formatAlmaty(getOrigin(run), locale) : '—');
  setText('[data-run-reuse]', run ? text(locale, run.reused ? 'reusedUnchanged' : 'newForecastRun') : '—');
  const chartHorizon = run ? asRecord(run.manifest).horizon : null;
  setText('[data-chart-window]', chartHorizon === null || chartHorizon === undefined ? '—' : text(locale, 'hoursUtc05', { hours: displayValue(chartHorizon, locale) }));

  const rows = forecastRows(run);
  const chart = document.querySelector<HTMLElement>('[data-forecast-chart]');
  if (chart) {
    chart.innerHTML = rows.length > 0
      ? renderForecastChart(rows, locale)
      : `<p class="chart-empty">${escapeHtml(text(locale, failure ? 'emptyForecastFailure' : 'emptyForecast'))}</p>`;
  }

  const horizon = run ? asRecord(run.manifest).horizon : null;
  updateStat('horizon', 'forecastHorizon', horizon === null || horizon === undefined ? text(locale, 'unavailable') : text(locale, 'hoursUtc05', { hours: displayValue(horizon, locale) }), locale);
  updateStat('weather', 'weatherSource', weatherName(run, locale), locale);
  updateStat('turbines', 'turbineCount', run ? String(new Set(rows.map((row) => row.turbine_id).filter(Boolean)).size) : text(locale, 'unavailable'), locale);
  updateStat('status', 'currentRun', run ? runStatusLabel(run.status, locale) : failure ? text(locale, 'apiFailed') : text(locale, 'noSavedRun'), locale);

  const summary = forecastSummary(rows, locale);
  setText('[data-summary-output="turbine_1"]', summary.turbine_1);
  setText('[data-summary-output="turbine_2"]', summary.turbine_2);
  setText('[data-summary-row-count]', summary.rowCount);
  showAlert(run, health, failure, locale);
  renderForecastTable(run, failure, locale);
}
