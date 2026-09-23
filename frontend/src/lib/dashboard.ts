import { getForecastJob, getHealth, getLatestRun, submitForecast } from './api';
import { ApiResponseError, applyJobSnapshot } from './forecast-workflow.mjs';
import { apiErrorMessage, formatAlmatyDateTime, parseAlmatyDateTime } from './dashboard-model.mjs';
import { renderAgentTrace } from '../features/agent-trace/render';
import { renderBacktest } from '../features/backtest/render';
import { renderDataQuality } from '../features/data-quality/render';
import { renderForecastViews } from '../features/forecast/render';
import { readLocale, readTheme, resolveTheme, saveLocale, text, toggleTheme } from './preferences.mjs';
import type { DashboardWorkflow, ForecastRequest, RunView } from './types';

const pollDelayMs = 2_000;
const maxPollsPerAttempt = 120;

function errorCode(error: unknown): string {
  return error instanceof ApiResponseError ? error.code : 'request_failed';
}

function setApiStatus(connected: boolean, labelKey: string, locale: 'ru' | 'kk'): void {
  const status = document.querySelector<HTMLElement>('[data-api-status]');
  const dot = document.querySelector<HTMLElement>('[data-connection-dot]');
  if (status) status.textContent = text(locale, labelKey);
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

function updateViews(run: RunView | null, latestFailure: string | null, locale: 'ru' | 'kk'): void {
  renderForecastViews(run, latestFailure, locale);
  renderBacktest(run, locale);
  renderAgentTrace(run, locale);
  renderDataQuality(run, locale);
}

function currentRunMessage(state: DashboardWorkflow, locale: 'ru' | 'kk'): { message: string; outcome: 'info' | 'success' | 'warning' | 'failed' } {
  const job = state.job;
  if (!job) return { message: text(locale, 'requestPreparing'), outcome: 'info' };
  if (job.state === 'queued') return { message: text(locale, 'jobQueued'), outcome: 'info' };
  if (job.state === 'running') return { message: text(locale, 'jobRunning'), outcome: 'info' };
  if (job.state === 'failed') return { message: apiErrorMessage(job.error?.code ?? 'execution_error', locale), outcome: 'failed' };
  if (job.result?.status === 'failed') return { message: text(locale, 'failedRunSelected'), outcome: 'failed' };
  if (job.result?.reused) return { message: text(locale, 'reusedRun'), outcome: job.result.status === 'degraded' ? 'warning' : 'success' };
  if (job.result?.status === 'degraded') return { message: text(locale, 'runDegraded'), outcome: 'warning' };
  return { message: text(locale, 'forecastFinished'), outcome: 'success' };
}

function renderJobState(state: DashboardWorkflow, form: HTMLFormElement, paused: boolean, polling: boolean, locale: 'ru' | 'kk'): void {
  setFormBusy(form, state.active);
  setRecovery(form, state.active && paused, polling);
  if (state.job) {
    const result = currentRunMessage(state, locale);
    setFeedback(form, paused ? text(locale, 'connectionInterrupted') : result.message, paused ? 'warning' : result.outcome);
  } else if (state.active) {
    setFeedback(form, text(locale, 'requestSubmitting'), 'info');
  }
}

function applyStaticTranslations(locale: 'ru' | 'kk', theme: 'light' | 'dark'): void {
  document.documentElement.lang = locale;
  for (const node of document.querySelectorAll<HTMLElement>('[data-i18n]')) {
    const key = node.dataset.i18n;
    if (key) node.textContent = text(locale, key);
  }
  for (const node of document.querySelectorAll<HTMLElement>('[data-i18n-aria]')) {
    const key = node.dataset.i18nAria;
    if (key) node.setAttribute('aria-label', text(locale, key));
  }
  for (const node of document.querySelectorAll<HTMLElement>('[data-i18n-title]')) {
    const key = node.dataset.i18nTitle;
    if (key) node.title = text(locale, key);
  }
  for (const node of document.querySelectorAll<HTMLElement>('[data-l10n-stat-label]')) {
    const key = node.dataset.l10nStatLabel;
    const label = node.querySelector<HTMLElement>('[data-slot="stat-label"]');
    if (key && label) label.textContent = text(locale, key);
  }
  const boundary = document.querySelector<HTMLElement>('[data-i18n="hourlyBoundary"]');
  if (boundary) boundary.textContent = text(locale, 'hourlyBoundary', { timezone: 'UTC+05' });
  const timezone = document.querySelector<HTMLElement>('[data-local-timezone]');
  if (timezone) timezone.textContent = 'Asia/Almaty';
  const horizon = document.querySelector<HTMLSelectElement>('select[name="horizon"]');
  if (horizon) {
    const option24 = horizon.querySelector<HTMLOptionElement>('option[value="24"]');
    const option48 = horizon.querySelector<HTMLOptionElement>('option[value="48"]');
    if (option24) option24.textContent = text(locale, 'hours24');
    if (option48) option48.textContent = text(locale, 'hours48');

    const selectRoot = horizon.closest<HTMLElement>('[data-ui-select]');
    const labels = new Map([...horizon.options].map((option) => [option.value, option.label]));
    for (const option of selectRoot?.querySelectorAll<HTMLElement>('[data-ui-select-option]') ?? []) {
      const label = labels.get(option.dataset.value ?? '');
      if (label) option.textContent = label;
    }
    const selectedValue = selectRoot?.querySelector<HTMLElement>('[data-ui-select-value]');
    if (selectedValue && horizon.selectedOptions[0]) selectedValue.textContent = horizon.selectedOptions[0].label;
  }
  const title = document.querySelector<HTMLElement>('title');
  if (title) title.textContent = text(locale, 'appTitle');
  const description = document.querySelector<HTMLMetaElement>('meta[name="description"]');
  if (description) description.content = text(locale, 'appDescription');

  for (const button of document.querySelectorAll<HTMLButtonElement>('[data-locale-switch]')) {
    button.setAttribute('aria-pressed', String(button.dataset.localeSwitch === locale));
  }
  const themeButton = document.querySelector<HTMLButtonElement>('[data-theme-toggle]');
  if (themeButton) {
    themeButton.setAttribute('aria-label', text(locale, theme === 'dark' ? 'switchToLight' : 'switchToDark'));
    themeButton.setAttribute('aria-pressed', String(theme === 'dark'));
    themeButton.dataset.currentTheme = theme;
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
  if (typeof window === 'undefined' || typeof document === 'undefined') return;
  const formElement = document.querySelector<HTMLFormElement>('#forecast-form');
  if (!formElement) return;
  const form: HTMLFormElement = formElement;

  let storage: Storage | null = null;
  try {
    storage = window.localStorage;
  } catch {
    // User preferences remain active for this visit if browser storage is blocked.
  }
  let locale = readLocale(storage);
  let themePreference = readTheme(storage);
  const colorScheme = window.matchMedia('(prefers-color-scheme: dark)');
  let theme = resolveTheme(themePreference, colorScheme.matches);
  document.documentElement.dataset.theme = theme;

  const origin = form.elements.namedItem('origin') as HTMLInputElement | null;
  if (origin) origin.value = formatAlmatyDateTime(new Date(Math.floor(Date.now() / 3_600_000) * 3_600_000));
  let formTouched = false;
  form.addEventListener('input', () => { formTouched = true; });
  form.addEventListener('change', () => { formTouched = true; });

  let latestFailure: string | null = null;
  let requestFailure: string | null = null;
  let validationMessageKey: string | null = null;
  let apiStatusKey = 'connecting';
  let apiConnected = false;
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

  const updateApiStatus = (connected: boolean, key: string): void => {
    apiConnected = connected;
    apiStatusKey = key;
    setApiStatus(connected, key, locale);
  };

  const render = (): void => {
    updateApiStatus(apiConnected, apiStatusKey);
    updateViews(workflow.selectedRun, latestFailure, locale);
    renderJobState(workflow, form, paused, polling, locale);
    if (requestFailure && !workflow.job) setFeedback(form, apiErrorMessage(requestFailure, locale), 'failed');
    else if (validationMessageKey) setFeedback(form, text(locale, validationMessageKey), 'warning');
  };

  const localize = (): void => {
    applyStaticTranslations(locale, theme);
    render();
  };

  applyStaticTranslations(locale, theme);
  for (const button of document.querySelectorAll<HTMLButtonElement>('[data-locale-switch]')) {
    button.addEventListener('click', () => {
      locale = button.dataset.localeSwitch === 'kk' ? 'kk' : 'ru';
      saveLocale(locale, storage);
      localize();
    });
  }
  const themeButton = document.querySelector<HTMLButtonElement>('[data-theme-toggle]');
  themeButton?.addEventListener('click', () => {
    theme = toggleTheme(theme, storage);
    themePreference = theme;
    document.documentElement.dataset.theme = theme;
    localize();
  });
  colorScheme.addEventListener('change', (event) => {
    if (themePreference !== 'system') return;
    theme = resolveTheme('system', event.matches);
    document.documentElement.dataset.theme = theme;
    localize();
  });

  async function loadLatest(): Promise<void> {
    if (latestRetry) latestRetry.hidden = true;
    updateApiStatus(false, 'connecting');
    try {
      await getHealth();
      const run = await getLatestRun();
      if (run && !formTouched) {
        const savedOrigin = run.manifest.forecast_origin ?? run.forecast[0]?.forecast_origin;
        if (origin && typeof savedOrigin === 'string') origin.value = formatAlmatyDateTime(new Date(savedOrigin));
        const horizon = form.elements.namedItem('horizon') as HTMLSelectElement | null;
        if (horizon && (run.manifest.horizon === 24 || run.manifest.horizon === 48)) {
          horizon.value = String(run.manifest.horizon);
          // Keep Lumen's native-select sync listener informed without treating this
          // saved-run restoration as a user edit on the parent form.
          horizon.dispatchEvent(new Event('change'));
        }
      }
      latestFailure = null;
      requestFailure = null;
      validationMessageKey = null;
      workflow = { ...workflow, selectedRun: run, job: null, active: false };
      updateApiStatus(true, 'connected');
      render();
    } catch (error) {
      latestFailure = errorCode(error);
      workflow = { ...workflow, selectedRun: null, job: null, active: false };
      if (latestRetry) latestRetry.hidden = false;
      updateApiStatus(false, 'apiUnavailable');
      render();
    }
  }

  async function pollJob(jobId: string): Promise<void> {
    if (polling) return;
    polling = true;
    paused = false;
    renderJobState(workflow, form, paused, polling, locale);
    for (let attempt = 0; attempt < maxPollsPerAttempt; attempt += 1) {
      await new Promise<void>((resolve) => window.setTimeout(resolve, pollDelayMs));
      try {
        const snapshot = await getForecastJob(jobId);
        workflow = applyJobSnapshot(workflow, snapshot);
        latestFailure = null;
        updateApiStatus(true, 'connected');
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
        updateApiStatus(false, 'apiInterrupted');
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
    if (workflow.active) return;
    const originInput = form.elements.namedItem('origin') as HTMLInputElement | null;
    const horizonInput = form.elements.namedItem('horizon') as HTMLSelectElement | null;
    if (!form.reportValidity()) {
      if (originInput && !originInput.validity.valid) {
        validationMessageKey = 'invalidOrigin';
        setFeedback(form, text(locale, validationMessageKey), 'warning');
      }
      return;
    }
    const intent = (event.submitter as HTMLButtonElement | null)?.value;
    const localOrigin = originInput?.value ?? '';
    const utcOrigin = parseAlmatyDateTime(localOrigin);
    if (!utcOrigin) {
      validationMessageKey = 'invalidOrigin';
      setFeedback(form, text(locale, validationMessageKey), 'warning');
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
    requestFailure = null;
    validationMessageKey = null;
    paused = false;
    polling = false;
    updateViews(null, null, locale);
    setFeedback(form, text(locale, 'requestSubmitting'), 'info');
    renderJobState(workflow, form, paused, polling, locale);

    try {
      const firstSnapshot = await submitForecast(request);
      workflow = applyJobSnapshot(workflow, firstSnapshot);
      updateApiStatus(true, 'connected');
      render();
      if (workflow.active) await pollJob(firstSnapshot.job_id);
      else render();
    } catch (error) {
      workflow = { ...workflow, active: false, job: null, selectedRun: null };
      requestFailure = errorCode(error);
      updateApiStatus(false, 'apiRequestFailed');
      render();
    }
  });

  latestRetry?.addEventListener('click', () => void loadLatest());
  jobRetry?.addEventListener('click', () => {
    if (workflow.job && workflow.active) void pollJob(workflow.job.job_id);
  });

  observeCurrentSection();
  void loadLatest();
}
