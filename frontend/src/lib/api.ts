import {
  ApiResponseError,
  decodeJobView,
  decodeRunView,
  readJsonResponse,
} from './forecast-workflow.mjs';
import type { ForecastRequest, HealthView, JobView, RunView } from './types';

const requestTimeoutMs = 12_000;

async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), requestTimeoutMs);
  try {
    const response = await window.fetch(path, { ...init, signal: controller.signal });
    return await readJsonResponse(response) as T;
  } catch (error) {
    if (error instanceof ApiResponseError) throw error;
    if (controller.signal.aborted) {
      throw new ApiResponseError('The local API did not respond in time. Retry the request.', 'request_timeout', 0);
    }
    throw new ApiResponseError('The local API could not be reached. Check the backend and retry.', 'connection_error', 0);
  } finally {
    window.clearTimeout(timer);
  }
}

export async function getHealth(): Promise<HealthView> {
  const value = await requestJson<unknown>('/api/health');
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new ApiResponseError('The API readiness response is malformed.', 'invalid_response', 200);
  }
  const health = value as Partial<HealthView>;
  if (health.status !== 'ok' || (health.mode !== 'demo' && health.mode !== 'competition') || typeof health.offline !== 'boolean') {
    throw new ApiResponseError('The API readiness response is malformed.', 'invalid_response', 200);
  }
  return health as HealthView;
}

export async function getLatestRun(): Promise<RunView | null> {
  const payload = await requestJson<{ run: unknown | null }>('/api/runs/latest');
  if (!payload || !Object.hasOwn(payload, 'run')) {
    throw new ApiResponseError('The latest run response is malformed.', 'invalid_response', 200);
  }
  return payload.run === null ? null : decodeRunView(payload.run);
}

export async function submitForecast(input: ForecastRequest): Promise<JobView> {
  const value = await requestJson<unknown>('/api/forecast-jobs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(input),
  });
  return decodeJobView(value);
}

export async function getForecastJob(jobId: string): Promise<JobView> {
  return decodeJobView(await requestJson<unknown>(`/api/forecast-jobs/${encodeURIComponent(jobId)}`));
}

export function forecastCsvUrl(runId: string): string {
  return `/api/runs/${encodeURIComponent(runId)}/forecast.csv`;
}
