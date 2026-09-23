import { escapeHtml } from '../forecast/chart.mjs';
import { asRecord, displayValue, formatAlmaty } from '../../lib/format';
import { eventActionLabel, eventOutcomeLabel } from '../../lib/dashboard-model.mjs';
import { text } from '../../lib/preferences.mjs';
import type { RunView } from '../../lib/types';
import { eventDiagnosticLabels, eventReasonExplanation } from './reasons.mjs';

export function renderAgentTrace(run: RunView | null, locale: 'ru' | 'kk' = 'ru'): void {
  const list = document.querySelector<HTMLElement>('[data-event-list]');
  const empty = document.querySelector<HTMLElement>('[data-event-empty]');
  const summary = document.querySelector<HTMLElement>('[data-trace-summary]');
  const events = run?.events ?? [];
  if (summary) summary.textContent = run
    ? text(locale, 'eventCount', { count: events.length, runId: run.run_id })
    : text(locale, 'noRunSelected');

  if (!list || !empty) return;
  if (events.length === 0) {
    list.hidden = true;
    empty.hidden = false;
    const title = empty.querySelector<HTMLElement>('h3');
    const description = empty.querySelector<HTMLElement>('p');
    if (title) title.textContent = run ? text(locale, 'noEventTrace') : text(locale, 'noRunSelected');
    if (description) description.textContent = run
      ? text(locale, 'noEventTraceDescription')
      : text(locale, 'noRunForTrace');
    return;
  }

  empty.hidden = true;
  list.hidden = false;
  list.innerHTML = events.map((event) => {
    const diagnosticLabels = eventDiagnosticLabels(locale);
    const sequence = escapeHtml(displayValue(event.sequence, locale));
    const state = escapeHtml(eventActionLabel(event.state, locale));
    const action = escapeHtml(eventActionLabel(event.action, locale));
    const outcomeLabel = eventOutcomeLabel(event.outcome, locale);
    const outcome = escapeHtml(outcomeLabel);
    const explanation = escapeHtml(eventReasonExplanation(event, locale));
    const reason = escapeHtml(displayValue(event.reason, locale));
    const timestamp = escapeHtml(formatAlmaty(event.timestamp, locale));
    const duration = typeof event.duration_ms === 'number' && Number.isFinite(event.duration_ms)
      ? `${new Intl.NumberFormat(locale === 'kk' ? 'kk-KZ' : 'ru-RU').format(event.duration_ms)} ${text(locale, 'unitMilliseconds')}`
      : text(locale, 'durationUnavailable');
    const eventRunId = escapeHtml(displayValue(asRecord(event).run_id ?? run?.run_id, locale));
    const rawTimestamp = typeof event.timestamp === 'string' ? escapeHtml(event.timestamp) : '';
    const outcomeCode = typeof event.outcome === 'string' ? event.outcome.toLowerCase() : 'unknown';
    const outcomeState = outcomeCode === 'success' || outcomeCode === 'succeeded' || outcomeCode === 'completed'
      ? 'success'
      : outcomeCode === 'failed' || outcomeCode === 'error' ? 'failed'
        : outcomeCode === 'skipped' ? 'warning'
          : outcomeCode === 'started' || outcomeCode === 'running' ? 'info' : 'unknown';
    return `<li class="event-item"><span class="event-sequence">${sequence}</span><div class="event-content"><div class="event-heading"><strong>${state}</strong><span class="event-outcome" data-outcome="${outcomeState}">${outcome}</span><time datetime="${rawTimestamp}">${timestamp}</time></div><p class="event-explanation">${explanation}</p><div class="event-meta"><span>${escapeHtml(text(locale, 'action'))}: ${action}</span><span>${escapeHtml(duration)}</span></div><details class="event-details"><summary>${escapeHtml(diagnosticLabels.disclosure)}</summary><div class="event-diagnostics"><p class="event-diagnostic-row"><span>${escapeHtml(diagnosticLabels.reason)}</span><code>${reason}</code></p><p class="event-diagnostic-row"><span>${escapeHtml(diagnosticLabels.runId)}</span><code>${eventRunId}</code></p></div></details></div></li>`;
  }).join('');
}
