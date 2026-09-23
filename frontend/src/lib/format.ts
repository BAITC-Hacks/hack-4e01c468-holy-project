import { displayMetric, percentValue } from './forecast-workflow.mjs';
import type { ApiRecord } from './types';

export function asRecord(value: unknown): ApiRecord {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as ApiRecord
    : {};
}

export function formatUtc(value: unknown, options: Intl.DateTimeFormatOptions = {}): string {
  if (typeof value !== 'string' || value.length === 0) return 'Unavailable';
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return 'Unavailable';
  return new Intl.DateTimeFormat('en', {
    year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit',
    timeZone: 'UTC', timeZoneName: 'short', ...options,
  }).format(date);
}

export function displayValue(value: unknown): string {
  return displayMetric(value);
}

export function humanizeCode(value: unknown): string {
  if (typeof value !== 'string' || value.length === 0) return 'Unavailable';
  return value.replaceAll('_', ' ').replace(/^./, (letter) => letter.toUpperCase());
}

export function turbineName(value: unknown): string {
  if (value === 'turbine_1') return 'Turbine 01';
  if (value === 'turbine_2') return 'Turbine 02';
  return displayValue(value);
}

export function asIsoRange(value: unknown): string {
  const period = asRecord(value);
  if (typeof period.start !== 'string' || typeof period.end !== 'string') return 'Evaluation dates unavailable';
  return `${formatUtc(period.start, { year: undefined, month: 'short', day: '2-digit', hour: '2-digit' })} to ${formatUtc(period.end, { year: undefined, month: 'short', day: '2-digit', hour: '2-digit' })}`;
}
