import { escapeHtml } from '../forecast/chart.mjs';
import { asRecord, displayValue, formatUtc, humanizeCode } from '../../lib/format';
import type { HealthView, RunView } from '../../lib/types';

function ageLabel(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return 'Unavailable';
  return `${value.toFixed(1)} hours`;
}

export function renderDataQuality(run: RunView | null, health: HealthView | null): void {
  const badge = document.querySelector<HTMLElement>('[data-provenance-badge]');
  const summary = document.querySelector<HTMLElement>('[data-provenance-summary]');
  const content = document.querySelector<HTMLElement>('[data-quality-content]');
  if (!content) return;

  if (!run) {
    if (badge) {
      badge.textContent = 'Unavailable';
      badge.dataset.outcome = 'unknown';
    }
    if (summary) summary.textContent = 'No selected run';
    content.innerHTML = '<p class="empty-cell">Load a saved forecast or generate a new run to inspect data provenance.</p>';
    return;
  }

  const manifest = asRecord(run.manifest);
  const weather = asRecord(manifest.weather_provenance);
  const quality = asRecord(manifest.data_quality);
  const rowWeather = run.forecast.find((row) => typeof row.weather_model === 'string');
  const valid = manifest.competition_valid === true || weather.competition_valid === true;
  const mode = health?.mode ?? displayValue(manifest.mode);
  const badgeText = mode === 'demo' || health?.offline ? 'Demo only' : valid ? 'Verified' : 'Unverified';
  if (badge) {
    badge.textContent = badgeText;
    badge.dataset.outcome = valid ? 'success' : 'degraded';
  }
  if (summary) summary.textContent = valid
    ? 'Backend provenance gate passed for this run.'
    : 'Original weather release availability is not verified.';

  const details: Array<[string, string]> = [
    ['API mode', `${mode}${health?.offline ? ' · offline' : ''}`],
    ['Weather provider', displayValue(weather.provider)],
    ['Weather model', displayValue(weather.weather_model ?? rowWeather?.weather_model)],
    ['Archive classification', humanizeCode(weather.provenance_status)],
    ['Weather issue time', formatUtc(weather.issued_at)],
    ['Weather available at', formatUtc(weather.available_at)],
    ['Retrieved at', formatUtc(weather.retrieved_at)],
    ['Latest observation', formatUtc(quality.latest_available_at)],
    ['Observation age', ageLabel(quality.observation_age_hours)],
    ['Stale observations', typeof quality.stale_observations === 'boolean' ? quality.stale_observations ? 'Yes' : 'No' : 'Unavailable'],
    ['History rows', displayValue(quality.history_rows)],
    ['Competition eligibility', valid ? 'Verified' : manifest.competition_valid === false || weather.competition_valid === false ? 'Not verified' : 'Unavailable'],
  ];
  content.innerHTML = `<dl class="quality-list">${details.map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join('')}</dl>`;
}
