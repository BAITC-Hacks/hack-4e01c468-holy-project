import assert from 'node:assert/strict';
import test from 'node:test';

import {
  createForecastDateTimeView,
  formatForecastDate,
  parseForecastDate,
  parseForecastDateTime,
  syncForecastDateTimeFields,
  toForecastDateTime,
  validateForecastDateTimeFields,
} from '../src/features/forecast/date-time/date-time.mjs';

test('date display is day.month.year and accepts leap days only in leap years', () => {
  assert.equal(parseForecastDate('29.02.2024'), '2024-02-29');
  assert.equal(formatForecastDate('2024-02-29'), '29.02.2024');
  assert.equal(parseForecastDate('29.02.2023'), null);
  assert.equal(parseForecastDate('31.04.2026'), null);
  assert.equal(parseForecastDate('1.04.2026'), null);
});

test('the selected hour stays a wall time and serializes as the existing canonical origin', () => {
  assert.equal(toForecastDateTime('2026-01-31', '00'), '2026-01-31T00:00');
  assert.deepEqual(parseForecastDateTime('2026-01-31T23:00'), {
    date: '2026-01-31',
    hour: '23',
    value: '2026-01-31T23:00',
  });
  assert.equal(parseForecastDateTime('2026-01-31T23:30'), null);
  assert.equal(parseForecastDateTime('2026-01-31T24:00'), null);
  assert.equal(toForecastDateTime('2026-02-29', '12'), null);
  assert.equal(toForecastDateTime('2024-02-29', ''), null);
});

test('incomplete and impossible canonical dates do not become selected forecast times', () => {
  assert.equal(parseForecastDateTime(''), null);
  assert.equal(parseForecastDateTime('2026-02-29T12:00'), null);
  assert.equal(parseForecastDateTime('2026-01-31T12:'), null);

  const view = createForecastDateTimeView('2026-02-29T12:00', 'ru');
  assert.equal(view.value, '');
  assert.equal(view.dateText, 'Выберите дату');
  assert.equal(view.hour, '');
});

test('missing date or hour stays invalid and never leaves a submit-ready hidden origin', () => {
  const empty = validateForecastDateTimeFields('', '', 'ru');
  assert.equal(empty.value, '');
  assert.notEqual(empty.dateMessage, '');
  assert.notEqual(empty.hourMessage, '');

  const dateOnly = validateForecastDateTimeFields('2026-01-31', '', 'kk');
  assert.equal(dateOnly.value, '');
  assert.equal(dateOnly.dateMessage, '');
  assert.notEqual(dateOnly.hourMessage, '');

  const hourOnly = validateForecastDateTimeFields('', '08', 'kk');
  assert.equal(hourOnly.value, '');
  assert.notEqual(hourOnly.dateMessage, '');
  assert.equal(hourOnly.hourMessage, '');

  const impossible = validateForecastDateTimeFields('2026-02-29', '08', 'ru');
  assert.equal(impossible.value, '');
  assert.notEqual(impossible.dateMessage, '');

  const complete = validateForecastDateTimeFields('2024-02-29', '08', 'ru');
  assert.equal(complete.value, '2024-02-29T08:00');
  assert.equal(complete.dateMessage, '');
  assert.equal(complete.hourMessage, '');
});

test('locale changes keep the selected wall date and hour while translating calendar guidance', () => {
  const russian = createForecastDateTimeView('2024-02-29T07:00', 'ru');
  const kazakh = createForecastDateTimeView('2024-02-29T07:00', 'kk');

  assert.equal(russian.dateText, '29.02.2024');
  assert.equal(kazakh.dateText, '29.02.2024');
  assert.equal(russian.hour, '07');
  assert.equal(kazakh.hour, '07');
  assert.equal(russian.value, '2024-02-29T07:00');
  assert.equal(kazakh.value, '2024-02-29T07:00');
  assert.equal(russian.timezone, 'UTC+05');
  assert.equal(kazakh.timezone, 'UTC+05');
  assert.notEqual(russian.calendarDialogLabel, kazakh.calendarDialogLabel);
});

test('programmatic restoration updates controls without emitting user edit events', () => {
  const fields = {
    dateInput: { value: '' },
    hourSelect: { value: '' },
    canonicalInput: { value: '' },
    dateValue: { textContent: '' },
    hourValue: { textContent: '' },
    events: [],
    dispatchEvent(event) { this.events.push(event); },
  };

  const view = syncForecastDateTimeFields(fields, '2026-01-31T00:00', 'kk');

  assert.equal(fields.dateInput.value, '2026-01-31');
  assert.equal(fields.hourSelect.value, '00');
  assert.equal(fields.canonicalInput.value, '2026-01-31T00:00');
  assert.equal(fields.dateValue.textContent, '31.01.2026');
  assert.equal(fields.hourValue.textContent, '00:00');
  assert.deepEqual(fields.events, []);
  assert.equal(view.value, '2026-01-31T00:00');
});

test('invalid restoration clears the hidden canonical origin and visible selection', () => {
  const fields = {
    dateInput: { value: '2024-02-29' },
    hourSelect: { value: '08' },
    canonicalInput: { value: '2024-02-29T08:00' },
    dateValue: { textContent: '29.02.2024' },
    hourValue: { textContent: '08:00' },
  };

  syncForecastDateTimeFields(fields, '2023-02-29T08:00', 'ru');

  assert.equal(fields.dateInput.value, '');
  assert.equal(fields.hourSelect.value, '');
  assert.equal(fields.canonicalInput.value, '');
  assert.equal(fields.dateValue.textContent, 'Выберите дату');
  assert.equal(fields.hourValue.textContent, 'Выберите час');
});
