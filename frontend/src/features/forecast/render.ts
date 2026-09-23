import { escapeHtml, renderForecastChart } from './chart.mjs';
import { forecastCsvUrl } from '../../lib/api';
import { asRecord, displayValue, formatUtc, turbineName } from '../../lib/format';
import { percentValue } from '../../lib/forecast-workflow.mjs';
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

function updateStat(name: string, value: string): void {
  const stat = document.querySelector<HTMLElement>(`[data-stat="${name}"]`);
  const output = stat?.querySelector<HTMLElement>('[data-slot="stat-value"]');
  if (output) output.textContent = value;
}

function forecastRows(run: RunView | null): ForecastRow[] {
  return run?.forecast ?? [];
}

function getOrigin(run: RunView): unknown {
  const manifest = asRecord(run.manifest);
  return manifest.forecast_origin ?? run.forecast[0]?.forecast_origin ?? null;
}

function weatherName(run: RunView | null): string {
  if (!run) return 'Unavailable';
  const manifestWeather = asRecord(asRecord(run.manifest).weather_provenance);
  const rowWeather = run.forecast.find((row) => typeof row.weather_model === 'string')?.weather_model;
  return displayValue(manifestWeather.weather_model ?? rowWeather);
}

function evaluationLabel(run: RunView | null): string {
  if (!run) return 'Unavailable';
  const metrics = asRecord(run.metrics);
  const status = metrics.metric_status;
  const models = Array.isArray(metrics.models) ? metrics.models : [];
  if (models.length > 0) return 'Available';
  if (status === 'unavailable_no_labels') return 'No labels';
  return status === 'available' ? 'Unavailable' : displayValue(status);
}

function alertContent(run: RunView | null, health: HealthView | null, failure: string | null): { outcome: string; message: string } | null {
  if (failure) return { outcome: 'failed', message: failure };
  if (!run) return { outcome: 'info', message: 'No saved forecast is available yet. Set an hourly origin and generate a run.' };
  if (run.status === 'failed') {
    return { outcome: 'failed', message: 'This forecast run failed. Its run ID remains selected for inspection.' };
  }
  if (run.status === 'degraded') {
    return { outcome: 'degraded', message: 'This run is degraded. Review weather provenance and observation quality before relying on it.' };
  }
  const competitionValid = asRecord(run.manifest).competition_valid;
  if (health?.mode === 'demo' || health?.offline) {
    return { outcome: 'info', message: 'The API is in demonstration mode. Forecast output is for demonstration use.' };
  }
  if (competitionValid === false) {
    return { outcome: 'degraded', message: 'Point-in-time weather eligibility was not verified for this forecast.' };
  }
  return null;
}

function showAlert(run: RunView | null, health: HealthView | null, failure: string | null): void {
  const alert = document.querySelector<HTMLElement>('[data-run-alert]');
  const message = alert?.querySelector<HTMLElement>('[data-run-alert-text]');
  const retry = alert?.querySelector<HTMLButtonElement>('[data-latest-retry]');
  const content = alertContent(run, health, failure);
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

function formatRow(row: ForecastRow): string {
  const time = escapeHtml(formatUtc(row.valid_time));
  const turbine = escapeHtml(turbineName(row.turbine_id));
  const lead = escapeHtml(displayValue(row.lead_hours));
  return `<tr><td><time>${time}</time></td><td>${turbine}</td><td>${lead === 'Unavailable' ? lead : `${lead} h`}</td><td>${escapeHtml(percentValue(row.p10))}</td><td class="median-cell">${escapeHtml(percentValue(row.p50))}</td><td>${escapeHtml(percentValue(row.p90))}</td></tr>`;
}

function renderForecastTable(run: RunView | null, failure: string | null): void {
  const body = document.querySelector<HTMLElement>('[data-forecast-rows]');
  const count = document.querySelector<HTMLElement>('[data-record-count]');
  const rows = forecastRows(run);
  if (count) count.textContent = failure ? 'Records unavailable' : `${rows.length} forecast rows`;
  if (body) {
    body.innerHTML = rows.length > 0
      ? rows.map(formatRow).join('')
      : `<tr><td colspan="6" class="empty-cell">${escapeHtml(failure ?? 'No forecast rows are available for this run.')}</td></tr>`;
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

export function renderForecastViews(run: RunView | null, health: HealthView | null, failure: string | null = null): void {
  const statusLabel = run ? run.status.toUpperCase() : failure ? 'API UNAVAILABLE' : 'NO SAVED RUN';
  const outcome = run?.status ?? (failure ? 'failed' : 'unknown');
  updateBadge(document.querySelector('.heading-outcome .run-status'), statusLabel, outcome);
  updateBadge(document.querySelector('.chart-context-detail .run-status'), run ? run.status.toUpperCase() : 'NO RUN', outcome);
  updateBadge(document.querySelector('.context-heading .run-status'), run ? run.status.toUpperCase() : 'NO RUN', outcome);

  const modeBadge = document.querySelector<HTMLElement>('[data-mode-badge]');
  if (modeBadge && health) modeBadge.textContent = `${health.mode.toUpperCase()}${health.offline ? ' · OFFLINE' : ''}`;
  setText('[data-run-id]', run?.run_id ?? '—');
  setText('[data-selected-run]', run?.run_id ?? (failure ? 'Latest run unavailable' : 'No saved run yet'));
  setText('[data-run-origin]', run ? formatUtc(getOrigin(run)) : '—');
  setText('[data-run-reuse]', run ? run.reused ? 'Reused · weather unchanged' : 'New forecast run' : '—');
  const chartHorizon = run ? asRecord(run.manifest).horizon : null;
  setText('[data-chart-window]', chartHorizon === null || chartHorizon === undefined ? '—' : `${displayValue(chartHorizon)} h · UTC`);

  const rows = forecastRows(run);
  const chart = document.querySelector<HTMLElement>('[data-forecast-chart]');
  if (chart) {
    chart.innerHTML = rows.length > 0
      ? renderForecastChart(rows)
      : `<p class="chart-empty">${escapeHtml(failure ?? 'No forecast data is available for the selected run.')}</p>`;
  }

  const horizon = run ? asRecord(run.manifest).horizon : null;
  updateStat('horizon', horizon === null || horizon === undefined ? 'Unavailable' : `${displayValue(horizon)} h`);
  updateStat('weather', weatherName(run));
  updateStat('evaluation', evaluationLabel(run));
  showAlert(run, health, failure);
  renderForecastTable(run, failure);
}
