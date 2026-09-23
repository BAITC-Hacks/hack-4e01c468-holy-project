import type { ApiJobError, DashboardWorkflow, JobView, RunView } from './types.js';

export class ApiResponseError extends Error {
  code: string;
  status: number;
}

export function decodeRunView(value: unknown): RunView;
export function decodeJobView(value: unknown): JobView;
export function applyJobSnapshot(current: DashboardWorkflow, value: unknown): DashboardWorkflow;
export function displayMetric(value: unknown): string;
export function percentValue(value: unknown): string;
export function readJsonResponse(response: Response): Promise<unknown>;
