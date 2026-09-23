const datePattern = /^(\d{4})-(\d{2})-(\d{2})$/;
const displayDatePattern = /^(\d{2})\.(\d{2})\.(\d{4})$/;
const dateTimePattern = /^(\d{4}-\d{2}-\d{2})T(\d{2}):00$/;
const hourPattern = /^(?:0\d|1\d|2[0-3])$/;

const messages = Object.freeze({
  ru: Object.freeze({
    dateLabel: 'Дата прогноза',
    hourLabel: 'Час',
    datePlaceholder: 'Выберите дату',
    hourPlaceholder: 'Выберите час',
    requiredDateMessage: 'Выберите дату прогноза.',
    invalidDateMessage: 'Укажите существующую дату.',
    requiredHourMessage: 'Выберите час прогноза.',
    timezone: 'UTC+05',
    timezoneCaption: 'UTC+05 · время Алматы',
    calendarDialogLabel: 'Выбрать дату',
    previousMonth: 'Предыдущий месяц',
    nextMonth: 'Следующий месяц',
    months: ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'],
    weekdays: ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'],
  }),
  kk: Object.freeze({
    dateLabel: 'Болжам күні',
    hourLabel: 'Сағат',
    datePlaceholder: 'Күнді таңдаңыз',
    hourPlaceholder: 'Сағатты таңдаңыз',
    requiredDateMessage: 'Болжам күнін таңдаңыз.',
    invalidDateMessage: 'Жарамды күнді таңдаңыз.',
    requiredHourMessage: 'Болжам сағатын таңдаңыз.',
    timezone: 'UTC+05',
    timezoneCaption: 'UTC+05 · Алматы уақыты',
    calendarDialogLabel: 'Күнді таңдау',
    previousMonth: 'Алдыңғы ай',
    nextMonth: 'Келесі ай',
    months: ['Қаңтар', 'Ақпан', 'Наурыз', 'Сәуір', 'Мамыр', 'Маусым', 'Шілде', 'Тамыз', 'Қыркүйек', 'Қазан', 'Қараша', 'Желтоқсан'],
    weekdays: ['Дс', 'Сс', 'Ср', 'Бс', 'Жм', 'Сб', 'Жс'],
  }),
});

function isValidIsoDate(value) {
  const match = datePattern.exec(value ?? '');
  if (!match) return false;

  const [, yearText, monthText, dayText] = match;
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > 31) return false;

  const date = new Date(0);
  date.setUTCHours(0, 0, 0, 0);
  date.setUTCFullYear(year, month - 1, day);
  return date.getUTCFullYear() === year &&
    date.getUTCMonth() === month - 1 &&
    date.getUTCDate() === day;
}

export function getForecastDateTimeMessages(locale = 'ru') {
  return messages[locale === 'kk' ? 'kk' : 'ru'];
}

export function parseForecastDate(value) {
  const match = displayDatePattern.exec(value ?? '');
  if (!match) return null;

  const [, day, month, year] = match;
  const isoDate = `${year}-${month}-${day}`;
  return isValidIsoDate(isoDate) ? isoDate : null;
}

export function formatForecastDate(value) {
  if (!isValidIsoDate(value)) return '';
  const [, year, month, day] = datePattern.exec(value);
  return `${day}.${month}.${year}`;
}

export function parseForecastDateTime(value) {
  const match = dateTimePattern.exec(value ?? '');
  if (!match || !isValidIsoDate(match[1]) || !hourPattern.test(match[2])) return null;

  return { date: match[1], hour: match[2], value };
}

export function toForecastDateTime(date, hour) {
  if (!isValidIsoDate(date) || !hourPattern.test(hour ?? '')) return null;
  return `${date}T${hour}:00`;
}

export function validateForecastDateTimeFields(date, hour, locale = 'ru') {
  const localized = getForecastDateTimeMessages(locale);
  const validDate = isValidIsoDate(date);
  const validHour = hourPattern.test(hour ?? '');

  return {
    dateMessage: validDate ? '' : date ? localized.invalidDateMessage : localized.requiredDateMessage,
    hourMessage: validHour ? '' : localized.requiredHourMessage,
    value: validDate && validHour ? `${date}T${hour}:00` : '',
  };
}

export function createForecastDateTimeView(value, locale = 'ru') {
  const selection = parseForecastDateTime(value);
  const localized = getForecastDateTimeMessages(locale);

  return {
    ...localized,
    locale: locale === 'kk' ? 'kk' : 'ru',
    date: selection?.date ?? '',
    dateText: selection ? formatForecastDate(selection.date) : localized.datePlaceholder,
    hour: selection?.hour ?? '',
    hourText: selection ? `${selection.hour}:00` : localized.hourPlaceholder,
    value: selection?.value ?? '',
  };
}

/** Update a restored origin and its visible Lumen control labels without firing edit events. */
export function syncForecastDateTimeFields(fields, value, locale = 'ru') {
  const view = createForecastDateTimeView(value, locale);

  if (fields.dateInput) fields.dateInput.value = view.date;
  if (fields.hourSelect) fields.hourSelect.value = view.hour;
  if (fields.canonicalInput) fields.canonicalInput.value = view.value;
  const validation = validateForecastDateTimeFields(view.date, view.hour, locale);
  fields.dateInput?.setCustomValidity?.(validation.dateMessage);
  fields.hourSelect?.setCustomValidity?.(validation.hourMessage);
  if (fields.dateValue) fields.dateValue.textContent = view.dateText;
  if (fields.hourValue) fields.hourValue.textContent = view.hourText;
  if (fields.datePickerRoot) {
    fields.datePickerRoot.dataset.placeholder = view.date ? 'false' : 'true';
  }
  if (fields.selectRoot) {
    fields.selectRoot.dataset.placeholder = view.hour ? 'false' : 'true';
    for (const option of fields.selectRoot.querySelectorAll?.('[data-ui-select-option]') ?? []) {
      option.setAttribute('aria-selected', String(option.dataset.value === view.hour));
    }
  }

  return view;
}

export function mountForecastDateTime(form, initialLocale = 'ru') {
  const root = form.querySelector('[data-forecast-date-time]');
  const datePickerRoot = root?.querySelector('[data-ui-date-picker]');
  const dateInput = datePickerRoot?.querySelector('[data-ui-date-picker-native]');
  const dateValue = datePickerRoot?.querySelector('[data-ui-date-picker-value]');
  const dateTrigger = datePickerRoot?.querySelector('[data-ui-date-picker-trigger]');
  const dateError = root?.querySelector('[data-forecast-date-error]');
  const dialog = datePickerRoot?.querySelector('[data-ui-date-picker-popover]');
  const calendar = datePickerRoot?.querySelector('[data-ui-calendar]');
  const selectRoot = root?.querySelector('[data-ui-select]');
  const hourSelect = selectRoot?.querySelector('[data-ui-select-native]');
  const hourValue = selectRoot?.querySelector('[data-ui-select-value]');
  const hourTrigger = selectRoot?.querySelector('[data-ui-select-trigger]');
  const hourError = root?.querySelector('[data-forecast-hour-error]');
  const canonicalInput = root?.querySelector('input[name="origin"]');

  if (!root || !datePickerRoot || !dateInput || !dateValue || !selectRoot || !hourSelect || !hourValue || !canonicalInput) {
    return null;
  }

  const fields = { dateInput, hourSelect, canonicalInput, dateValue, hourValue, datePickerRoot, selectRoot };
  let locale = initialLocale === 'kk' ? 'kk' : 'ru';

  const updateCalendarLabels = () => {
    const localized = getForecastDateTimeMessages(locale);
    if (dialog) dialog.setAttribute('aria-label', localized.calendarDialogLabel);
    calendar?.querySelector('[data-ui-calendar-prev]')?.setAttribute('aria-label', localized.previousMonth);
    calendar?.querySelector('[data-ui-calendar-next]')?.setAttribute('aria-label', localized.nextMonth);
    const month = calendar?.dataset.uiCalendarMonth?.match(/^(\d{4})-(\d{2})/);
    const heading = calendar?.querySelector('[data-ui-calendar-label]');
    if (month && heading) {
      const label = `${localized.months[Number(month[2]) - 1]} ${month[1]}`;
      if (heading.textContent !== label) heading.textContent = label;
    }
    calendar?.querySelectorAll('thead th').forEach((cell, index) => {
      const label = localized.weekdays[index];
      if (label && cell.textContent !== label) cell.textContent = label;
    });
    calendar?.querySelectorAll('[data-ui-calendar-day]').forEach((cell) => {
      const date = cell.dataset.date?.match(/^(\d{4})-(\d{2})-(\d{2})$/);
      if (date) cell.setAttribute('aria-label', `${Number(date[3])} ${localized.months[Number(date[2]) - 1]} ${date[1]}`);
    });
  };

  const updateSelectionDisplay = () => {
    const localized = getForecastDateTimeMessages(locale);
    const validation = validateForecastDateTimeFields(dateInput.value, hourSelect.value, locale);
    canonicalInput.value = validation.value;
    dateInput.setCustomValidity(validation.dateMessage);
    hourSelect.setCustomValidity(validation.hourMessage);
    dateValue.textContent = dateInput.value ? formatForecastDate(dateInput.value) : localized.datePlaceholder;
    hourValue.textContent = hourSelect.value ? `${hourSelect.value}:00` : localized.hourPlaceholder;
    datePickerRoot.dataset.placeholder = dateInput.value ? 'false' : 'true';
    selectRoot.dataset.placeholder = hourSelect.value ? 'false' : 'true';
    if (dateError && !dateError.hidden) dateError.textContent = validation.dateMessage;
    if (hourError && !hourError.hidden) hourError.textContent = validation.hourMessage;
    if (!validation.dateMessage) {
      dateError?.setAttribute('hidden', '');
      dateTrigger?.removeAttribute('aria-invalid');
    }
    if (!validation.hourMessage) {
      hourError?.setAttribute('hidden', '');
      hourTrigger?.removeAttribute('aria-invalid');
    }
    updateCalendarLabels();
  };

  const sync = (value) => syncForecastDateTimeFields(fields, value, locale);

  const setLocale = (nextLocale) => {
    const normalizedLocale = nextLocale === 'kk' ? 'kk' : 'ru';
    const localeChanged = locale !== normalizedLocale;
    locale = normalizedLocale;
    const localized = getForecastDateTimeMessages(locale);
    const openCalendar = datePickerRoot.querySelector('[data-ui-date-picker-popover]:not([hidden])');
    if (localeChanged && openCalendar) datePickerRoot.querySelector('[data-ui-date-picker-trigger]')?.click();

    root.querySelector('[data-forecast-date-label]').textContent = localized.dateLabel;
    root.querySelector('[data-forecast-hour-label]').textContent = localized.hourLabel;
    root.querySelector('[data-forecast-timezone]').textContent = localized.timezoneCaption;
    root.dataset.locale = locale;
    dateInput.setAttribute('placeholder', localized.datePlaceholder);
    const selectPlaceholder = hourSelect.querySelector('[data-ui-select-placeholder]');
    if (selectPlaceholder) selectPlaceholder.textContent = localized.hourPlaceholder;
    for (const option of selectRoot.querySelectorAll('[data-ui-select-option][data-value=""]')) {
      option.textContent = localized.hourPlaceholder;
    }
    if (!hourSelect.value) hourValue.textContent = localized.hourPlaceholder;
    if (!dateInput.value) dateValue.textContent = localized.datePlaceholder;
    updateSelectionDisplay();
    updateCalendarLabels();
  };

  dateInput.addEventListener('input', updateSelectionDisplay);
  dateInput.addEventListener('change', updateSelectionDisplay);
  hourSelect.addEventListener('input', updateSelectionDisplay);
  hourSelect.addEventListener('change', updateSelectionDisplay);
  dateInput.addEventListener('invalid', (event) => {
    event.preventDefault();
    dateTrigger?.setAttribute('aria-invalid', 'true');
    if (dateError) {
      dateError.hidden = false;
      dateError.textContent = dateInput.validationMessage;
    }
    dateTrigger?.focus({ preventScroll: true });
  });
  hourSelect.addEventListener('invalid', (event) => {
    event.preventDefault();
    hourTrigger?.setAttribute('aria-invalid', 'true');
    if (hourError) {
      hourError.hidden = false;
      hourError.textContent = hourSelect.validationMessage;
    }
    hourTrigger?.focus({ preventScroll: true });
  });
  root.addEventListener('click', updateCalendarLabels);
  // Some browsers ship incomplete Kazakh Intl data. Localize after Lumen redraws too.
  if (calendar) new MutationObserver(updateCalendarLabels).observe(calendar, {
    childList: true, subtree: true, attributes: true, attributeFilter: ['data-ui-calendar-month'],
  });

  sync(canonicalInput.value);
  setLocale(locale);

  return { setLocale, sync };
}
