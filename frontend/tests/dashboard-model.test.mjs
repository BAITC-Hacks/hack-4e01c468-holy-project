import assert from 'node:assert/strict';
import test from 'node:test';
import { apiErrorMessage, eventActionLabel, runStatusLabel } from '../src/lib/dashboard-model.mjs';

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
