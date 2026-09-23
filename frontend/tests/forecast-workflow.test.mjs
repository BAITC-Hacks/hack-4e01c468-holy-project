import assert from 'node:assert/strict';
import test from 'node:test';
import {
  applyJobSnapshot,
  decodeRunView,
  displayMetric,
  percentValue,
  readJsonResponse,
} from '../src/lib/forecast-workflow.mjs';

const successfulRun = {
  run_id: 'run-20260131T190000Z-h24-a1b2c3d4',
  status: 'degraded',
  reused: false,
  manifest: { mode: 'demo' },
  metrics: { metric_status: 'unavailable_no_labels', models: [] },
  events: [],
  report: 'Forecast persisted.',
  forecast: [
    {
      turbine_id: 'turbine_1',
      lead_hours: 0,
      p10: 0.02,
      p50: 0.05,
      p90: 0.11,
    },
  ],
};

test('decodes a domain-failed run without losing its run identity or rows', () => {
  const run = decodeRunView({ ...successfulRun, status: 'failed' });

  assert.equal(run.status, 'failed');
  assert.equal(run.run_id, successfulRun.run_id);
  assert.deepEqual(run.forecast, successfulRun.forecast);
});

test('rejects a malformed run instead of rendering an incomplete response', () => {
  assert.throws(() => decodeRunView({ ...successfulRun, forecast: {} }), /forecast/);
});

test('shows unavailable for absent and non-finite metrics while preserving zero', () => {
  assert.equal(displayMetric(null), 'Unavailable');
  assert.equal(displayMetric(Number.NaN), 'Unavailable');
  assert.equal(displayMetric(0), '0');
});

test('formats numeric forecast fractions as percentages without inventing capacity units', () => {
  assert.equal(percentValue(0.125), '12.5%');
  assert.equal(percentValue('0.125'), '12.5%');
  assert.equal(percentValue(null), 'Unavailable');
});

test('completed jobs select the returned failed run and retain form values', () => {
  const current = {
    form: { origin: '2026-02-01T00:00', horizon: 48, refresh: true },
    selectedRun: successfulRun,
    job: { job_id: 'job-1', state: 'running', result: null, error: null },
  };
  const failedRun = { ...successfulRun, run_id: 'run-new', status: 'failed' };
  const next = applyJobSnapshot(current, {
    job_id: 'job-1', state: 'completed', result: failedRun, error: null,
  });

  assert.equal(next.selectedRun.run_id, 'run-new');
  assert.equal(next.selectedRun.status, 'failed');
  assert.deepEqual(next.form, current.form);
  assert.equal(next.job.state, 'completed');
});

test('polling a transport failure clears the previous run instead of showing it as current', () => {
  const current = {
    form: { origin: '2026-02-01T00:00', horizon: 24, refresh: false },
    selectedRun: successfulRun,
    job: { job_id: 'job-2', state: 'running', result: null, error: null },
  };
  const next = applyJobSnapshot(current, {
    job_id: 'job-2', state: 'failed', result: null,
    error: { code: 'execution_error', message: 'The forecast run failed.' },
  });

  assert.equal(next.selectedRun, null);
  assert.deepEqual(next.form, current.form);
});

test('reads API error messages from the frozen error envelope', async () => {
  const response = new Response(JSON.stringify({
    error: { code: 'conflict', message: 'A forecast is already running.' },
  }), { status: 409, headers: { 'content-type': 'application/json' } });

  await assert.rejects(readJsonResponse(response), /A forecast is already running/);
});
