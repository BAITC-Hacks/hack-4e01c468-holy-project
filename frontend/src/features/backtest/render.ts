import { escapeHtml } from '../forecast/chart.mjs';
import { asRecord, asIsoRange, displayValue, humanizeCode, turbineName } from '../../lib/format';
import { percentValue } from '../../lib/forecast-workflow.mjs';
import type { RunView } from '../../lib/types';

const allowedMetricNames = ['mae', 'rmse', 'interval_coverage', 'n'] as const;

function measure(value: unknown, name: string): string {
  if (name === 'interval_coverage') return percentValue(value);
  if (typeof value !== 'number' || !Number.isFinite(value)) return 'Unavailable';
  return value.toFixed(3);
}

function rowScope(row: Record<string, unknown>): string {
  const turbine = row.turbine_id === null || row.turbine_id === undefined
    ? 'All turbines'
    : turbineName(row.turbine_id);
  const lead = row.lead_hours === null || row.lead_hours === undefined
    ? 'All leads'
    : `${displayValue(row.lead_hours)} h`;
  return `${turbine} · ${lead}`;
}

function emptyMessage(status: unknown): { title: string; description: string } {
  if (status === 'unavailable_no_labels') {
    return {
      title: 'No matching turbine labels',
      description: 'Evaluation metrics are unavailable because the selected run has no aligned observed turbine output.',
    };
  }
  if (status === 'unavailable_unverified_weather') {
    return {
      title: 'Backtest withheld by provenance',
      description: 'Weather availability before each forecast origin was not verified for this evaluation.',
    };
  }
  return {
    title: 'Historical metrics unavailable',
    description: 'This run does not include usable historical evaluation records.',
  };
}

export function renderBacktest(run: RunView | null): void {
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

  if (period) period.textContent = run ? asIsoRange(metrics.evaluation_period) : 'No selected run';
  if (badge) {
    badge.textContent = status === 'available' && availableRows.length > 0 ? 'Metrics available' : humanizeCode(status);
    badge.dataset.outcome = status === 'available' && availableRows.length > 0 ? 'success' : 'unknown';
  }

  if (availableRows.length > 0 && body && tableWrap && empty) {
    body.innerHTML = availableRows.map((row) => {
      const model = escapeHtml(humanizeCode(row.model));
      const scope = escapeHtml(rowScope(row));
      return `<tr><td>${model}</td><td>${scope}</td><td>${escapeHtml(measure(row.mae, 'mae'))}</td><td>${escapeHtml(measure(row.rmse, 'rmse'))}</td><td>${escapeHtml(measure(row.interval_coverage, 'interval_coverage'))}</td><td>${escapeHtml(displayValue(row.n))}</td></tr>`;
    }).join('');
    empty.hidden = true;
    tableWrap.hidden = false;
    return;
  }

  if (tableWrap) tableWrap.hidden = true;
  if (empty) empty.hidden = false;
  const message = emptyMessage(status);
  if (emptyTitle) emptyTitle.textContent = run ? message.title : 'No run selected';
  if (emptyDescription) emptyDescription.textContent = run ? message.description : 'Load a saved forecast or generate a new run to inspect its historical evaluation status.';
}
