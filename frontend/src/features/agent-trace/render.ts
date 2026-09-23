import { escapeHtml } from '../forecast/chart.mjs';
import { asRecord, displayValue, formatUtc, humanizeCode } from '../../lib/format';
import type { RunView } from '../../lib/types';

export function renderAgentTrace(run: RunView | null): void {
  const list = document.querySelector<HTMLElement>('[data-event-list]');
  const empty = document.querySelector<HTMLElement>('[data-event-empty]');
  const summary = document.querySelector<HTMLElement>('[data-trace-summary]');
  const events = run?.events ?? [];
  if (summary) summary.textContent = run ? `${events.length} recorded events · ${run.run_id}` : 'No selected run';

  if (!list || !empty) return;
  if (events.length === 0) {
    list.hidden = true;
    empty.hidden = false;
    const title = empty.querySelector<HTMLElement>('h3');
    const description = empty.querySelector<HTMLElement>('p');
    if (title) title.textContent = run ? 'No event trace' : 'No run selected';
    if (description) description.textContent = run
      ? 'The selected run did not include agent events.'
      : 'Load a saved forecast or generate a new run to inspect its decisions.';
    return;
  }

  empty.hidden = true;
  list.hidden = false;
  list.innerHTML = events.map((event, index) => {
    const sequence = escapeHtml(displayValue(event.sequence));
    const state = escapeHtml(humanizeCode(event.state));
    const action = escapeHtml(humanizeCode(event.action));
    const outcome = escapeHtml(humanizeCode(event.outcome));
    const reason = escapeHtml(displayValue(event.reason));
    const timestamp = escapeHtml(formatUtc(event.timestamp));
    const duration = typeof event.duration_ms === 'number' && Number.isFinite(event.duration_ms)
      ? `${event.duration_ms} ms`
      : 'Duration unavailable';
    const eventRunId = escapeHtml(displayValue(asRecord(event).run_id));
    return `<li class="event-item"><span class="event-sequence">${sequence}</span><div class="event-content"><div class="event-heading"><strong>${state}</strong><span class="event-outcome" data-outcome="${outcome.toLowerCase()}">${outcome}</span><time datetime="${timestamp}">${timestamp}</time></div><p>${reason}</p><div class="event-meta"><span>Action: ${action}</span><span>${escapeHtml(duration)}</span><span class="event-run-id">${eventRunId}</span></div></div></li>`;
  }).join('');
}
