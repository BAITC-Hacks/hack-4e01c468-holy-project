export type RunStatus = 'success' | 'degraded' | 'failed';
export type JobState = 'queued' | 'running' | 'completed' | 'failed';

export type ApiRecord = Record<string, unknown>;

export interface ForecastRow extends ApiRecord {
  turbine_id?: string | null;
  valid_time?: string | null;
  lead_hours?: string | number | null;
  p10?: string | number | null;
  p50?: string | number | null;
  p90?: string | number | null;
  weather_model?: string | null;
  weather_issued_at?: string | null;
}

export interface RunView {
  run_id: string;
  status: RunStatus;
  reused: boolean;
  manifest: ApiRecord;
  metrics: ApiRecord;
  events: ApiRecord[];
  report: string;
  forecast: ForecastRow[];
}

export interface ApiJobError {
  code: string;
  message: string;
}

export interface JobView {
  job_id: string;
  state: JobState;
  result: RunView | null;
  error: ApiJobError | null;
}

export interface HealthView {
  status: 'ok';
  mode: 'demo' | 'competition';
  offline: boolean;
}

export interface ForecastRequest {
  origin: string;
  horizon: 24 | 48;
  refresh: boolean;
}

export interface DashboardWorkflow {
  form: ForecastRequest;
  selectedRun: RunView | null;
  job: JobView | null;
  active: boolean;
}
