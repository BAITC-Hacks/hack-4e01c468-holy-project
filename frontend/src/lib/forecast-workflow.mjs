const runStatuses = new Set(['success', 'degraded', 'failed']);
const jobStates = new Set(['queued', 'running', 'completed', 'failed']);

export class ApiResponseError extends Error {
  constructor(message, code = 'request_failed', status = 0) {
    super(message);
    this.name = 'ApiResponseError';
    this.code = code;
    this.status = status;
  }
}

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function contractError(field) {
  return new TypeError(`Invalid API response: ${field} is missing or malformed.`);
}

export function decodeRunView(value) {
  if (!isRecord(value)) throw contractError('run');
  if (typeof value.run_id !== 'string' || value.run_id.length === 0) throw contractError('run_id');
  if (!runStatuses.has(value.status)) throw contractError('status');
  if (typeof value.reused !== 'boolean') throw contractError('reused');
  if (!isRecord(value.manifest)) throw contractError('manifest');
  if (!isRecord(value.metrics)) throw contractError('metrics');
  if (!Array.isArray(value.events) || !value.events.every(isRecord)) throw contractError('events');
  if (typeof value.report !== 'string') throw contractError('report');
  if (!Array.isArray(value.forecast) || !value.forecast.every(isRecord)) throw contractError('forecast');
  return value;
}

export function decodeJobView(value) {
  if (!isRecord(value)) throw contractError('job');
  if (typeof value.job_id !== 'string' || value.job_id.length === 0) throw contractError('job_id');
  if (!jobStates.has(value.state)) throw contractError('state');

  let result = null;
  if (value.result !== null) result = decodeRunView(value.result);

  let error = null;
  if (value.error !== null) {
    if (!isRecord(value.error) || typeof value.error.code !== 'string' || typeof value.error.message !== 'string') {
      throw contractError('error');
    }
    error = value.error;
  }

  if (value.state === 'completed' && result === null) throw contractError('result');
  if (value.state === 'failed' && error === null) throw contractError('error');
  return { ...value, result, error };
}

export function applyJobSnapshot(current, value) {
  const job = decodeJobView(value);
  if (current.job && current.job.job_id !== job.job_id) {
    throw new TypeError('The forecast status belongs to a different job.');
  }

  const terminal = job.state === 'completed' || job.state === 'failed';
  return {
    ...current,
    job,
    active: !terminal,
    selectedRun: job.result ?? (job.state === 'failed' ? null : current.selectedRun),
  };
}

export function displayMetric(value) {
  if (value === null || value === undefined) return 'Unavailable';
  if (typeof value === 'number' && !Number.isFinite(value)) return 'Unavailable';
  if (typeof value === 'string' && value.trim().length === 0) return 'Unavailable';
  if (typeof value !== 'string' && typeof value !== 'number' && typeof value !== 'boolean') return 'Unavailable';
  return String(value);
}

export function percentValue(value) {
  const numeric = typeof value === 'number'
    ? value
    : typeof value === 'string' && value.trim().length > 0 ? Number(value) : Number.NaN;
  if (!Number.isFinite(numeric)) return 'Unavailable';
  return `${(numeric * 100).toFixed(1)}%`;
}

export async function readJsonResponse(response) {
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new ApiResponseError('The server returned an unreadable response.', 'invalid_response', response.status);
  }

  if (!response.ok) {
    const error = isRecord(payload) && isRecord(payload.error) ? payload.error : null;
    const message = error && typeof error.message === 'string'
      ? error.message
      : 'The request could not be completed.';
    const code = error && typeof error.code === 'string' ? error.code : 'request_failed';
    throw new ApiResponseError(message, code, response.status);
  }
  return payload;
}
