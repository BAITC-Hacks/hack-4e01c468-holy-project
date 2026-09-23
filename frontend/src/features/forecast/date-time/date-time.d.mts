export type ForecastDateTimeLocale = 'ru' | 'kk';

export interface ForecastDateTimeSelection {
  date: string;
  hour: string;
  value: string;
}

export interface ForecastDateTimeMessages {
  dateLabel: string;
  hourLabel: string;
  datePlaceholder: string;
  hourPlaceholder: string;
  timezone: 'UTC+05';
  timezoneCaption: string;
  calendarDialogLabel: string;
  previousMonth: string;
  nextMonth: string;
}

export interface ForecastDateTimeView extends ForecastDateTimeMessages {
  locale: ForecastDateTimeLocale;
  date: string;
  dateText: string;
  hour: string;
  hourText: string;
  value: string;
}

export interface ForecastDateTimeFields {
  dateInput?: Pick<HTMLInputElement, 'value' | 'setCustomValidity'>;
  hourSelect?: Pick<HTMLSelectElement, 'value' | 'setCustomValidity'>;
  canonicalInput?: Pick<HTMLInputElement, 'value'>;
  dateValue?: Pick<HTMLElement, 'textContent'>;
  hourValue?: Pick<HTMLElement, 'textContent'>;
  datePickerRoot?: HTMLElement;
  selectRoot?: HTMLElement;
}

export interface ForecastDateTimeController {
  setLocale(locale: ForecastDateTimeLocale): void;
  sync(value: string): ForecastDateTimeView;
}

export function getForecastDateTimeMessages(locale?: ForecastDateTimeLocale): ForecastDateTimeMessages;
export function parseForecastDate(value: string): string | null;
export function formatForecastDate(value: string): string;
export function parseForecastDateTime(value: string): ForecastDateTimeSelection | null;
export function toForecastDateTime(date: string, hour: string): string | null;
export function createForecastDateTimeView(value: string, locale?: ForecastDateTimeLocale): ForecastDateTimeView;
export function validateForecastDateTimeFields(date: string, hour: string, locale?: ForecastDateTimeLocale): {
  dateMessage: string;
  hourMessage: string;
  value: string;
};
export function syncForecastDateTimeFields(
  fields: ForecastDateTimeFields,
  value: string,
  locale?: ForecastDateTimeLocale,
): ForecastDateTimeView;
export function mountForecastDateTime(
  form: HTMLFormElement,
  initialLocale?: ForecastDateTimeLocale,
): ForecastDateTimeController | null;
