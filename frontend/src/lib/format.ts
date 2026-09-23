import { displayMetric } from './forecast-workflow.mjs';
import { text } from './preferences.mjs';
import type { ApiRecord } from './types';

export function asRecord(value: unknown): ApiRecord {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as ApiRecord
    : {};
}

export function formatAlmaty(value: unknown, locale: 'ru' | 'kk' = 'ru', options: Intl.DateTimeFormatOptions = {}): string {
  if (typeof value !== 'string' || value.length === 0) return text(locale, 'unavailable');
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return text(locale, 'unavailable');
  const almatyTime = new Date(date.getTime() + 5 * 60 * 60 * 1000);
  return new Intl.DateTimeFormat(locale === 'kk' ? 'kk-KZ' : 'ru-RU', {
    year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit',
    hourCycle: 'h23', timeZone: 'UTC', ...options,
  }).format(almatyTime);
}

export function displayValue(value: unknown, locale: 'ru' | 'kk' = 'ru'): string {
  const output = displayMetric(value);
  return output === 'Unavailable' ? text(locale, 'unavailable') : output;
}

export function humanizeCode(value: unknown, locale: 'ru' | 'kk' = 'ru'): string {
  if (typeof value !== 'string' || value.length === 0) return text(locale, 'unavailable');
  return value.replaceAll('_', ' ').replace(/^./, (letter) => letter.toUpperCase());
}

export function turbineName(value: unknown, locale: 'ru' | 'kk' = 'ru'): string {
  if (value === 'turbine_1') return text(locale, 'turbineOne');
  if (value === 'turbine_2') return text(locale, 'turbineTwo');
  return displayValue(value);
}

export function asIsoRange(value: unknown, locale: 'ru' | 'kk' = 'ru'): string {
  const period = asRecord(value);
  if (typeof period.start !== 'string' || typeof period.end !== 'string') return text(locale, 'evaluationWindowUnavailable');
  const options: Intl.DateTimeFormatOptions = { year: undefined, month: 'short', day: '2-digit', hour: '2-digit' };
  return text(locale, 'dateRange', {
    start: formatAlmaty(period.start, locale, options),
    end: formatAlmaty(period.end, locale, options),
  });
}
