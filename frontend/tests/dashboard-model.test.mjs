import assert from 'node:assert/strict';
import test from 'node:test';
import * as dashboardModel from '../src/lib/dashboard-model.mjs';

const { apiErrorMessage, eventActionLabel, runStatusLabel } = dashboardModel;

test('names actual agent stages separately from their outcomes', () => {
  assert.equal(eventActionLabel('train_or_load_model', 'ru'), 'Расчёт модели');
  assert.equal(eventActionLabel('check_for_updates', 'ru'), 'Проверка обновлений');
  assert.equal(eventActionLabel('quality_gate', 'kk'), 'Сапаны тексеру');
  assert.equal(eventActionLabel('persist_run', 'ru'), 'Сохранение результата');
});

test('maps known transport failures into the selected interface language', () => {
  assert.equal(apiErrorMessage('connection_error', 'kk'), 'Жергілікті API-ге қосылмады. Серверді тексеріп, қайталап көріңіз.');
  assert.equal(apiErrorMessage('request_timeout', 'ru'), 'Локальный API не ответил вовремя. Повторите запрос.');
});

test('does not show untrusted API message text as a localized interface error', () => {
  assert.equal(apiErrorMessage('unknown_backend_code', 'kk'), 'Сұрауды орындау мүмкін болмады. Қайталап көріңіз.');
  assert.equal(apiErrorMessage('execution_error', 'ru'), 'Не удалось выполнить задачу прогноза.');
});

test('localizes known run status and agent action codes but hides unknown machine codes', () => {
  assert.equal(runStatusLabel('degraded', 'kk'), 'ШЕКТЕУЛІ');
  assert.equal(eventActionLabel('fetch_weather', 'kk'), 'Ауа райын алу');
  assert.equal(eventActionLabel('provider_tool_v7', 'ru'), 'Записанное действие');
});

test('maps user-facing API error codes to Russian and Kazakh text', () => {
  assert.equal(apiErrorMessage('invalid_request', 'ru'), 'Проверьте параметры прогноза и повторите запрос.');
  assert.equal(apiErrorMessage('job_already_running', 'kk'), 'Болжам тапсырмасы орындалып жатыр. Оның күйін тексеріңіз.');
  assert.equal(apiErrorMessage('run_not_found', 'kk'), 'Сұрауды орындау мүмкін болмады. Қайталап көріңіз.');
});

test('does not label an unknown API mode as demo', () => {
  assert.equal(dashboardModel.apiModeLabel('mystery', false, 'kk'), 'Қолжетімсіз');
});

test('formats forecast fractions with localized decimal separators and empty values', () => {
  assert.equal(typeof dashboardModel.formatPercentage, 'function');
  assert.equal(dashboardModel.formatPercentage(0.125, 'ru'), '12,5%');
  assert.equal(dashboardModel.formatPercentage(null, 'kk'), 'Қолжетімсіз');
});

test('interprets datetime-local values as Almaty UTC+05, regardless of machine timezone', () => {
  assert.equal(typeof dashboardModel.parseAlmatyDateTime, 'function');
  assert.equal(dashboardModel.parseAlmatyDateTime('2026-02-01T00:00')?.toISOString(), '2026-01-31T19:00:00.000Z');
  assert.equal(dashboardModel.parseAlmatyDateTime('2026-02-31T00:00'), null);
});

test('formats the default datetime-local origin in Asia/Almaty', () => {
  assert.equal(typeof dashboardModel.formatAlmatyDateTime, 'function');
  assert.equal(dashboardModel.formatAlmatyDateTime(new Date('2026-01-31T19:00:00.000Z')), '2026-02-01T00:00');
});

test('derives latest turbine medians and row count from selected API forecast rows', () => {
  assert.equal(typeof dashboardModel.forecastSummary, 'function');
  assert.deepEqual(dashboardModel.forecastSummary([
    { turbine_id: 'turbine_1', lead_hours: 1, p50: 0.14 },
    { turbine_id: 'turbine_1', lead_hours: 5, p50: 0.27 },
    { turbine_id: 'turbine_2', lead_hours: 3, p50: 0.08 },
  ], 'ru'), {
    turbine_1: '27%',
    turbine_2: '8%',
    rowCount: '3',
  });
});

test('does not treat a row without a lead hour as an actual latest median', () => {
  assert.deepEqual(dashboardModel.forecastSummary([
    { turbine_id: 'turbine_1', lead_hours: null, p50: 0.99 },
  ], 'kk'), {
    turbine_1: '—',
    turbine_2: '—',
    rowCount: '1',
  });
});
