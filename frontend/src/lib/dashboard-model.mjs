import { text } from './preferences.mjs';

const errorMessages = new Map([
  ['request_timeout', 'requestTimeout'],
  ['connection_error', 'connectionError'],
  ['invalid_response', 'invalidApiResponse'],
  ['invalid_request', 'invalidRequest'],
  ['job_already_running', 'jobAlreadyRunning'],
  ['execution_error', 'jobFailed'],
  ['forecast_failed', 'jobFailed'],
]);

const runStatuses = new Map([
  ['success', 'runSuccess'],
  ['degraded', 'runDegraded'],
  ['failed', 'runFailed'],
]);

const eventActions = new Map([
  ['start', 'eventStart'],
  ['start_forecast', 'eventStart'],
  ['fetch_weather', 'eventWeather'],
  ['get_weather', 'eventWeather'],
  ['retrieve_weather', 'eventWeather'],
  ['validate_inputs', 'eventValidate'],
  ['validate_data', 'eventValidate'],
  ['build_features', 'eventFeatures'],
  ['prepare_features', 'eventFeatures'],
  ['load_model', 'eventModel'],
  ['train_or_load_model', 'eventModel'],
  ['check_for_updates', 'eventCheckUpdates'],
  ['quality_gate', 'eventQualityGate'],
  ['analyze_result', 'eventAnalyze'],
  ['persist_run', 'eventPersist'],
  ['continue', 'eventContinue'],
  ['finalize', 'eventComplete'],
  ['generate_forecast', 'eventForecast'],
  ['predict', 'eventForecast'],
  ['backtest', 'eventBacktest'],
  ['persist', 'eventPersist'],
  ['save_result', 'eventPersist'],
  ['save_artifact', 'eventPersist'],
  ['reuse', 'eventReuse'],
  ['reuse_run', 'eventReuse'],
  ['complete', 'eventComplete'],
]);

export function apiErrorMessage(code, locale) {
  return text(locale, errorMessages.get(code) ?? 'genericApiError');
}

export function runStatusLabel(status, locale) {
  return text(locale, runStatuses.get(status) ?? 'runUnknown');
}

export function eventActionLabel(action, locale) {
  const key = eventActions.get(typeof action === 'string' ? action.toLowerCase() : '');
  return text(locale, key ?? 'agentRecorded');
}

export function eventOutcomeLabel(outcome, locale) {
  const normalized = typeof outcome === 'string' ? outcome.toLowerCase() : '';
  const key = normalized === 'success' || normalized === 'succeeded' || normalized === 'completed'
    ? 'eventSucceeded'
    : normalized === 'failed' || normalized === 'error'
      ? 'eventFailed'
      : normalized === 'skipped'
        ? 'eventSkipped'
        : normalized === 'started' || normalized === 'running'
          ? 'eventStarted'
          : 'eventUnknown';
  return text(locale, key);
}

export function metricStatusLabel(status, locale) {
  const key = status === 'available'
    ? 'metricStatusAvailable'
    : status === 'unavailable_no_labels'
      ? 'statusNoLabels'
      : status === 'unavailable_unverified_weather'
        ? 'statusUnverifiedWeather'
        : 'evaluationUnavailable';
  return text(locale, key);
}

export function apiModeLabel(mode, offline, locale) {
  const key = mode === 'competition' ? 'competitionMode' : mode === 'demo' ? 'demoMode' : null;
  if (!key) return text(locale, 'unavailable');
  const label = text(locale, key);
  return offline ? `${label} · ${text(locale, 'offlineMode')}` : label;
}

export function formatPercentage(value, locale) {
  const numeric = typeof value === 'number'
    ? value
    : typeof value === 'string' && value.trim() !== '' ? Number(value) : Number.NaN;
  if (!Number.isFinite(numeric)) return text(locale, 'unavailable');
  const language = locale === 'kk' ? 'kk-KZ' : 'ru-RU';
  return `${new Intl.NumberFormat(language, { maximumFractionDigits: 1 }).format(numeric * 100)}%`;
}

export function parseAlmatyDateTime(value) {
  if (typeof value !== 'string') return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(value);
  if (!match) return null;
  const [, year, month, day, hour, minute] = match.map(Number);
  if (month < 1 || month > 12 || day < 1 || day > 31 || hour > 23 || minute > 59) return null;
  const wallTime = Date.UTC(year, month - 1, day, hour, minute);
  const check = new Date(wallTime);
  if (check.getUTCFullYear() !== year || check.getUTCMonth() !== month - 1 || check.getUTCDate() !== day) return null;
  return new Date(wallTime - 5 * 60 * 60 * 1000);
}

export function formatAlmatyDateTime(value) {
  if (!(value instanceof Date) || !Number.isFinite(value.getTime())) return '';
  const almatyWallTime = new Date(value.getTime() + 5 * 60 * 60 * 1000);
  const pad = (part) => String(part).padStart(2, '0');
  return `${almatyWallTime.getUTCFullYear()}-${pad(almatyWallTime.getUTCMonth() + 1)}-${pad(almatyWallTime.getUTCDate())}T${pad(almatyWallTime.getUTCHours())}:${pad(almatyWallTime.getUTCMinutes())}`;
}

export function forecastSummary(rows, locale) {
  const forecast = Array.isArray(rows) ? rows : [];
  const latest = new Map();
  for (const row of forecast) {
    if (!row || (row.turbine_id !== 'turbine_1' && row.turbine_id !== 'turbine_2')) continue;
    if (row.lead_hours === null || row.lead_hours === undefined || (typeof row.lead_hours === 'string' && row.lead_hours.trim() === '')) continue;
    const lead = Number(row.lead_hours);
    if (row.p50 === null || row.p50 === undefined || (typeof row.p50 === 'string' && row.p50.trim() === '') || !Number.isFinite(Number(row.p50))) continue;
    const current = latest.get(row.turbine_id);
    if (!Number.isFinite(lead) || (current && lead <= current.lead)) continue;
    latest.set(row.turbine_id, { lead, p50: row.p50 });
  }

  const formatMedian = (row) => {
    return row ? formatPercentage(row.p50, locale) : '—';
  };

  return {
    turbine_1: formatMedian(latest.get('turbine_1')),
    turbine_2: formatMedian(latest.get('turbine_2')),
    rowCount: String(forecast.length),
  };
}
