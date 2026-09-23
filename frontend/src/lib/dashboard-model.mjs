import { text } from './preferences.mjs';

const errorMessages = new Map([
  ['request_timeout', 'requestTimeout'],
  ['connection_error', 'connectionError'],
  ['invalid_response', 'invalidApiResponse'],
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
  const key = mode === 'competition' ? 'competitionMode' : 'demoMode';
  const label = text(locale, key);
  return offline ? `${label} · ${text(locale, 'offlineMode')}` : label;
}
