import { getForecastJob, getHealth, getLatestRun, submitForecast } from './api';
import { applyJobSnapshot } from './forecast-workflow.mjs';
import { renderAgentTrace } from '../features/agent-trace/render';
import { renderBacktest } from '../features/backtest/render';
import { renderDataQuality } from '../features/data-quality/render';
import { renderForecastViews } from '../features/forecast/render';
import type { DashboardWorkflow, ForecastRequest, HealthView, RunView } from './types';

const pollDelayMs = 2_000;
const maxPollsPerAttempt = 120;

function localInputValue(date: Date): string {
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : 'The request could not be completed. Retry when the API is available.';
}

function setApiStatus(connected: boolean, label: string): void {
  const text = document.querySelector<HTMLElement>('[data-api-status]');
  const dot = document.querySelector<HTMLElement>('[data-connection-dot]');
  if (text) text.textContent = label;
  if (dot) dot.dataset.connected = String(connected);
}

function setFormBusy(form: HTMLFormElement, busy: boolean): void {
  for (const control of form.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLButtonElement>('input, select, button:not([data-job-retry])')) {
    control.disabled = busy;
  }
  const recovery = form.querySelector<HTMLButtonElement>('[data-job-retry]');
  if (recovery) recovery.disabled = !busy;
}

function setFeedback(form: HTMLFormElement, message: string, outcome: 'info' | 'success' | 'warning' | 'failed' = 'info'): void {
  const feedback = form.querySelector<HTMLElement>('[data-job-feedback]');
  if (!feedback) return;
  feedback.textContent = message;
  feedback.dataset.outcome = outcome;
}

function setRecovery(form: HTMLFormElement, visible: boolean, busy: boolean): void {
  const wrapper = form.querySelector<HTMLElement>('[data-job-recovery]');
  const button = form.querySelector<HTMLButtonElement>('[data-job-retry]');
  if (wrapper) wrapper.hidden = !visible;
  if (button) button.disabled = !visible || busy;
}

function updateViews(run: RunView | null, health: HealthView | null, latestFailure: string | null): void {
  renderForecastViews(run, health, latestFailure);
  renderBacktest(run);
  renderAgentTrace(run);
  renderDataQuality(run, health);
}

function currentRunMessage(state: DashboardWorkflow): { message: string; outcome: 'info' | 'success' | 'warning' | 'failed' } {
  const job = state.job;
  if (!job) return { message: 'Preparing forecast request…', outcome: 'info' };
  if (job.state === 'queued') return { message: 'Forecast queued. Waiting for the local worker.', outcome: 'info' };
  if (job.state === 'running') return { message: 'Forecast is running. This page will update when the run finishes.', outcome: 'info' };
  if (job.state === 'failed') return { message: job.error?.message ?? 'The forecast job failed.', outcome: 'failed' };
  if (job.result?.status === 'failed') return { message: 'The run completed with a failed forecast status. Its details remain selected below.', outcome: 'failed' };
  if (job.result?.reused) return { message: 'The API reused the saved run because forecast inputs and weather were unchanged.', outcome: 'success' };
  return { message: 'Forecast finished. The returned run is selected below.', outcome: 'success' };
}

function renderJobState(state: DashboardWorkflow, form: HTMLFormElement, paused: boolean, polling: boolean): void {
  setFormBusy(form, state.active);
  setRecovery(form, state.active && paused, polling);
  if (state.job) {
    const result = currentRunMessage(state);
    setFeedback(form, paused ? 'Connection interrupted. Check the job status to continue.' : result.message, paused ? 'warning' : result.outcome);
  }
}

function observeCurrentSection(): void {
  if (!('IntersectionObserver' in window)) return;
  const links = [...document.querySelectorAll<HTMLAnchorElement>('.section-nav a[href^="#"]')];
  const sections = links
    .map((link) => document.querySelector<HTMLElement>(link.hash))
    .filter((section): section is HTMLElement => section !== null);
  const observer = new IntersectionObserver((entries) => {
    const visible = entries.filter((entry) => entry.isIntersecting)
      .sort((left, right) => right.intersectionRatio - left.intersectionRatio)[0];
    if (!visible) return;
    for (const link of links) {
      if (link.hash === `#${visible.target.id}`) link.setAttribute('aria-current', 'location');
      else link.removeAttribute('aria-current');
    }
  }, { rootMargin: '-18% 0px -70% 0px', threshold: [0, 0.15, 0.5] });
  for (const section of sections) observer.observe(section);
}

export function startDashboard(): void {
  const form = document.querySelector<HTMLFormElement>('#forecast-form');
  if (!form) return;

  const origin = form.elements.namedItem('origin') as HTMLInputElement | null;
  if (origin) origin.value = localInputValue(new Date(Math.floor(Date.now() / 3_600_000) * 3_600_000));
  const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const zoneLabel = document.querySelector<HTMLElement>('[data-timezone]');
  if (zoneLabel) zoneLabel.textContent = timeZone || 'local timezone';

  let health: HealthView | null = null;
  let latestFailure: string | null = null;
  let paused = false;
  let polling = false;
  let workflow: DashboardWorkflow = {
    form: { origin: '', horizon: 24, refresh: false },
    selectedRun: null,
    job: null,
    active: false,
  };
  const latestRetry = document.querySelector<HTMLButtonElement>('[data-latest-retry]');
  const jobRetry = form.querySelector<HTMLButtonElement>('[data-job-retry]');

  const render = (): void => {
    updateViews(workflow.selectedRun, health, latestFailure);
    renderJobState(workflow, form, paused, polling);
  };

  async function loadLatest(): Promise<void> {
    if (latestRetry) latestRetry.hidden = true;
    setApiStatus(false, 'Connecting');
    try {
      health = await getHealth();
      const run = await getLatestRun();
      latestFailure = null;
      workflow = { ...workflow, selectedRun: run, job: null, active: false };
      setApiStatus(true, 'Connected');
      render();
    } catch (error) {
      latestFailure = errorText(error);
      workflow = { ...workflow, selectedRun: null, job: null, active: false };
      if (latestRetry) latestRetry.hidden = false;
      setApiStatus(false, 'Unavailable');
      render();
    }
  }

  async function pollJob(jobId: string): Promise<void> {
    if (polling) return;
    polling = true;
    paused = false;
    renderJobState(workflow, form, paused, polling);
    for (let attempt = 0; attempt < maxPollsPerAttempt; attempt += 1) {
      await new Promise<void>((resolve) => window.setTimeout(resolve, pollDelayMs));
      try {
        const snapshot = await getForecastJob(jobId);
        workflow = applyJobSnapshot(workflow, snapshot);
        latestFailure = null;
        setApiStatus(true, 'Connected');
        render();
        if (!workflow.active) {
          polling = false;
          paused = false;
          render();
          return;
        }
      } catch {
        polling = false;
        paused = true;
        setApiStatus(false, 'Status interrupted');
        render();
        return;
      }
    }
    polling = false;
    paused = true;
    render();
  }

  form.addEventListener('submit', async (event: SubmitEvent) => {
    event.preventDefault();
    if (workflow.active || !form.reportValidity()) return;
    const originInput = form.elements.namedItem('origin') as HTMLInputElement | null;
    const horizonInput = form.elements.namedItem('horizon') as HTMLSelectElement | null;
    const intent = (event.submitter as HTMLButtonElement | null)?.value;
    const localOrigin = originInput?.value ?? '';
    const utcOrigin = new Date(localOrigin);
    if (!localOrigin || !Number.isFinite(utcOrigin.getTime())) {
      setFeedback(form, 'Choose a valid hourly origin before submitting.', 'warning');
      originInput?.focus();
      return;
    }

    const request: ForecastRequest = {
      origin: utcOrigin.toISOString(),
      horizon: horizonInput?.value === '48' ? 48 : 24,
      refresh: intent === 'refresh',
    };
    workflow = { form: request, selectedRun: null, job: null, active: true };
    latestFailure = null;
    paused = false;
    polling = false;
    updateViews(null, health, null);
    setFeedback(form, 'Submitting forecast request…', 'info');
    renderJobState(workflow, form, paused, polling);

    try {
      const firstSnapshot = await submitForecast(request);
      workflow = applyJobSnapshot(workflow, firstSnapshot);
      setApiStatus(true, 'Connected');
      render();
      if (workflow.active) await pollJob(firstSnapshot.job_id);
      else render();
    } catch (error) {
      workflow = { ...workflow, active: false, job: null, selectedRun: null };
      setApiStatus(false, 'Request failed');
      render();
      setFeedback(form, errorText(error), 'failed');
    }
  });

  latestRetry?.addEventListener('click', () => void loadLatest());
  jobRetry?.addEventListener('click', () => {
    if (workflow.job && workflow.active) void pollJob(workflow.job.job_id);
  });

  observeCurrentSection();
  void loadLatest();
}
