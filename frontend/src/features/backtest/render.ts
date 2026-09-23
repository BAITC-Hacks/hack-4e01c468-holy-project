import { escapeHtml } from '../forecast/chart.mjs';
import { asRecord, asIsoRange, displayValue, humanizeCode, turbineName } from '../../lib/format';
import { formatPercentage, metricStatusLabel } from '../../lib/dashboard-model.mjs';
import { text } from '../../lib/preferences.mjs';
import type { RunView } from '../../lib/types';

const allowedMetricNames = ['mae', 'rmse', 'interval_coverage', 'n'] as const;

function measure(value: unknown, locale: 'ru' | 'kk'): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return text(locale, 'unavailable');
  return new Intl.NumberFormat(locale === 'kk' ? 'kk-KZ' : 'ru-RU', { maximumFractionDigits: 3 }).format(value);
}

function rowScope(row: Record<string, unknown>, locale: 'ru' | 'kk'): string {
  const turbine = row.turbine_id === null || row.turbine_id === undefined
    ? text(locale, 'allTurbines')
    : turbineName(row.turbine_id, locale);
  const lead = row.lead_hours === null || row.lead_hours === undefined
    ? text(locale, 'allLeads')
    : `${displayValue(row.lead_hours, locale)} ${text(locale, 'unitHours')}`;
  return `${turbine} · ${lead}`;
}

function emptyMessage(status: unknown, locale: 'ru' | 'kk'): { title: string; description: string } {
  if (status === 'unavailable_no_labels') {
    return {
      title: text(locale, 'noMatchingLabels'),
      description: text(locale, 'noMatchingLabelsDescription'),
    };
  }
  if (status === 'unavailable_unverified_weather') {
    return {
      title: text(locale, 'provenanceWithheld'),
      description: text(locale, 'provenanceWithheldDescription'),
    };
  }
  return {
    title: text(locale, 'historicalUnavailable'),
    description: text(locale, 'historicalUnavailableDescription'),
  };
}

export function renderBacktest(run: RunView | null, locale: 'ru' | 'kk' = 'ru'): void {
  const badge = document.querySelector<HTMLElement>('[data-backtest-badge]');
  const period = document.querySelector<HTMLElement>('[data-evaluation-period]');
  const empty = document.querySelector<HTMLElement>('[data-backtest-empty]');
  const emptyTitle = empty?.querySelector<HTMLElement>('h3');
  const emptyDescription = empty?.querySelector<HTMLElement>('p');
  const tableWrap = document.querySelector<HTMLElement>('[data-backtest-table-wrap]');
  const body = document.querySelector<HTMLElement>('[data-backtest-rows]');
  const metrics = run ? asRecord(run.metrics) : {};
  const rows = Array.isArray(metrics.models)
    ? metrics.models.filter((item): item is Record<string, unknown> => item !== null && typeof item === 'object' && !Array.isArray(item))
    : [];
  const availableRows = rows.filter((row) => allowedMetricNames.some((name) => row[name] !== undefined));
  const status = metrics.metric_status;

  if (period) period.textContent = run ? asIsoRange(metrics.evaluation_period, locale) : text(locale, 'noRunSelected');
  if (badge) {
    badge.textContent = status === 'available' && availableRows.length > 0 ? text(locale, 'metricsAvailable') : metricStatusLabel(status, locale);
    badge.dataset.outcome = status === 'available' && availableRows.length > 0 ? 'success' : 'unknown';
  }

  if (availableRows.length > 0 && body && tableWrap && empty) {
    body.innerHTML = availableRows.map((row) => {
      const model = escapeHtml(humanizeCode(row.model, locale));
      const scope = escapeHtml(rowScope(row, locale));
      return `<tr><td>${model}</td><td>${scope}</td><td>${escapeHtml(measure(row.mae, locale))}</td><td>${escapeHtml(measure(row.rmse, locale))}</td><td>${escapeHtml(formatPercentage(row.interval_coverage, locale))}</td><td>${escapeHtml(displayValue(row.n, locale))}</td></tr>`;
    }).join('');
    empty.hidden = true;
    tableWrap.hidden = false;
    return;
  }

  if (tableWrap) tableWrap.hidden = true;
  if (empty) empty.hidden = false;
  const message = emptyMessage(status, locale);
  if (emptyTitle) emptyTitle.textContent = run ? message.title : text(locale, 'noRunSelected');
  if (emptyDescription) emptyDescription.textContent = run ? message.description : text(locale, 'loadRunForEvaluation');
}
