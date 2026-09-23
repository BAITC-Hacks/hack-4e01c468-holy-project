import { escapeHtml } from '../forecast/chart.mjs';
import { asRecord, displayValue, formatAlmaty, humanizeCode } from '../../lib/format';
import { isSyntheticWeatherProvenance, weatherSourceLabel } from '../../lib/dashboard-model.mjs';
import { text } from '../../lib/preferences.mjs';
import type { RunView } from '../../lib/types';

function ageLabel(value: unknown, locale: 'ru' | 'kk'): string {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return text(locale, 'unavailable');
  return text(locale, 'hours', { value: new Intl.NumberFormat(locale === 'kk' ? 'kk-KZ' : 'ru-RU', { maximumFractionDigits: 1 }).format(value) });
}

export function renderDataQuality(run: RunView | null, locale: 'ru' | 'kk' = 'ru'): void {
  const badge = document.querySelector<HTMLElement>('[data-provenance-badge]');
  const summary = document.querySelector<HTMLElement>('[data-provenance-summary]');
  const content = document.querySelector<HTMLElement>('[data-quality-content]');
  if (!content) return;

  if (!run) {
    if (badge) {
      badge.textContent = text(locale, 'unavailable');
      badge.dataset.outcome = 'unknown';
    }
    if (summary) summary.textContent = text(locale, 'noRunSelected');
    content.innerHTML = `<p class="empty-cell">${escapeHtml(text(locale, 'qualityNotSelected'))}</p>`;
    return;
  }

  const manifest = asRecord(run.manifest);
  const weather = asRecord(manifest.weather_provenance);
  const quality = asRecord(manifest.data_quality);
  const rowWeather = run.forecast.find((row) => typeof row.weather_model === 'string');
  const valid = manifest.competition_valid === true || weather.competition_valid === true;
  const synthetic = isSyntheticWeatherProvenance(weather, rowWeather?.weather_model);
  const badgeText = synthetic ? text(locale, 'syntheticDataOnly') : valid ? text(locale, 'verified') : text(locale, 'unverified');
  if (badge) {
    badge.textContent = badgeText;
    badge.dataset.outcome = valid && !synthetic ? 'success' : 'degraded';
  }
  if (summary) summary.textContent = synthetic
    ? text(locale, 'syntheticProvenanceWarning')
    : valid
      ? text(locale, 'provenancePassed')
      : text(locale, 'provenanceUnverified');

  const details: Array<[string, string]> = [
    ['weatherProvider', weatherSourceLabel(weather.provider, locale)],
    ['weatherModel', weatherSourceLabel(weather.weather_model ?? rowWeather?.weather_model, locale)],
    ['archiveClassification', humanizeCode(weather.provenance_status, locale)],
    ['weatherIssueTime', formatAlmaty(weather.issued_at, locale)],
    ['weatherAvailableAt', formatAlmaty(weather.available_at, locale)],
    ['retrievedAt', formatAlmaty(weather.retrieved_at, locale)],
    ['latestObservation', formatAlmaty(quality.latest_available_at, locale)],
    ['observationAge', ageLabel(quality.observation_age_hours, locale)],
    ['staleObservations', typeof quality.stale_observations === 'boolean' ? quality.stale_observations ? text(locale, 'yes') : text(locale, 'no') : text(locale, 'unavailable')],
    ['historyRows', displayValue(quality.history_rows, locale)],
  ];
  content.innerHTML = `<dl class="quality-list">${details.map(([key, value]) => `<div><dt>${escapeHtml(text(locale, key))}</dt><dd>${escapeHtml(value)}</dd></div>`).join('')}</dl>`;
}
