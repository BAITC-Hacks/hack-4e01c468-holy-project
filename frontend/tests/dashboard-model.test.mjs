import assert from 'node:assert/strict';
import test from 'node:test';
import * as dashboardModel from '../src/lib/dashboard-model.mjs';

const { apiErrorMessage, eventActionLabel, runStatusLabel } = dashboardModel;

test('displays readable weather names in Russian and Kazakh', () => {
  assert.equal(dashboardModel.weatherSourceLabel('noaa_gfs_0p25', 'ru'), 'NOAA GFS · сетка 0,25°');
  assert.equal(dashboardModel.weatherSourceLabel('noaa_gfs_0p25', 'kk'), 'NOAA GFS · 0,25° тор');
  assert.equal(dashboardModel.weatherSourceLabel('noaa-gfs-public-s3-range', 'ru'), 'NOAA · открытый архив прогнозов');
  assert.equal(dashboardModel.weatherSourceLabel('ecmwf_ifs', 'ru'), 'ECMWF IFS');
  assert.equal(dashboardModel.weatherSourceLabel('synthetic-demo', 'ru'), 'Синтетическая модель погоды');
  assert.equal(dashboardModel.weatherSourceLabel('synthetic-test', 'kk'), 'Синтетикалық ауа райы моделі');
  assert.equal(dashboardModel.weatherSourceLabel(null, 'ru'), 'Недоступно');
});

test('detects synthetic provenance without using API mode as a proxy', () => {
  assert.equal(dashboardModel.isSyntheticWeatherProvenance({ provenance_status: 'synthetic' }), true);
  assert.equal(dashboardModel.isSyntheticWeatherProvenance({ weather_model: 'synthetic-demo' }), true);
  assert.equal(dashboardModel.isSyntheticWeatherProvenance({ provider: 'local-synthetic-generator' }), true);
  assert.equal(dashboardModel.isSyntheticWeatherProvenance({ weather_model: 'noaa_gfs_0p25' }), false);
});

test('recognizes only verified fresh CatBoost runs degraded solely by corrected quantile crossings', () => {
  const manifest = {
    status: 'degraded',
    competition_valid: true,
    model: { name: 'catboost_quantile' },
    weather_provenance: { provenance_status: 'verified', competition_valid: true },
    data_quality: {
      history_rows: 100,
      latest_available_at: '2026-09-22T18:00:00Z',
      observation_age_hours: 6,
      stale_observations: false,
      degrade_reasons: ['model_quantile_crossings_corrected'],
      prediction_quality: {
        model: { quantile_crossing_correction_count: 15, quantile_clipping_count: 0, quantile_crossing_policy: 'sort_per_row_then_clip' },
        quality_gate: { quantile_correction_count: 0, clipping_count: 0 },
      },
    },
  };

  assert.equal(dashboardModel.isQuantileCorrectionOnlyDegradation(manifest), true);
  assert.equal(dashboardModel.isQuantileCorrectionOnlyDegradation({
    ...manifest,
    data_quality: { ...manifest.data_quality, degrade_reasons: [
      'model_quantile_crossings_corrected', 'quality_gate_quantile_crossings_corrected',
    ], prediction_quality: {
      ...manifest.data_quality.prediction_quality,
      quality_gate: { quantile_correction_count: 2, clipping_count: 0 },
    } },
  }), true);

  const rejected = [
    { ...manifest, model: { name: 'persistence' } },
    { ...manifest, weather_provenance: { provenance_status: 'synthetic' } },
    { ...manifest, competition_valid: false },
    { ...manifest, competition_valid: undefined },
    { ...manifest, weather_provenance: { ...manifest.weather_provenance, competition_valid: false } },
    { ...manifest, weather_provenance: { provenance_status: 'verified' } },
    { ...manifest, data_quality: { ...manifest.data_quality, stale_observations: true } },
    { ...manifest, data_quality: { ...manifest.data_quality, observation_age_hours: 25 } },
    { ...manifest, data_quality: { ...manifest.data_quality, degrade_reasons: ['model_quantile_crossings_corrected', 'weather_provenance_unverified'] } },
    { ...manifest, data_quality: { ...manifest.data_quality, prediction_quality: {
      ...manifest.data_quality.prediction_quality,
      model: { ...manifest.data_quality.prediction_quality.model, quantile_clipping_count: 1 },
    } } },
    { ...manifest, data_quality: { ...manifest.data_quality, prediction_quality: {
      ...manifest.data_quality.prediction_quality,
      model: { ...manifest.data_quality.prediction_quality.model, quantile_crossing_correction_count: 0 },
    } } },
    { ...manifest, status: 'success' },
    { status: 'degraded', model: { name: 'catboost_quantile' }, data_quality: {} },
  ];
  for (const candidate of rejected) assert.equal(dashboardModel.isQuantileCorrectionOnlyDegradation(candidate), false);
});

test('names actual agent stages separately from their outcomes', () => {
  assert.equal(eventActionLabel('train_or_load_model', 'ru'), 'Расчёт модели');
  assert.equal(eventActionLabel('check_for_updates', 'ru'), 'Проверка обновлений');
  assert.equal(eventActionLabel('quality_gate', 'kk'), 'Сапаны тексеру');
  assert.equal(eventActionLabel('persist_run', 'ru'), 'Сохранение результата');
});

test('names the baseline action without changing its recorded outcome', () => {
  assert.equal(eventActionLabel('use_baseline', 'ru'), 'Применение базовой модели');
  assert.equal(eventActionLabel('use_baseline', 'kk'), 'Негізгі модельді қолдану');
  assert.equal(dashboardModel.eventOutcomeLabel('skipped', 'ru'), 'пропущено');
});

test('maps known transport failures into the selected interface language', () => {
  assert.equal(apiErrorMessage('connection_error', 'kk'), 'Жергілікті API-ге қосылмады. Серверді тексеріп, қайталап көріңіз.');
  assert.equal(apiErrorMessage('request_timeout', 'ru'), 'Локальный API не ответил вовремя. Повторите запрос.');
});

test('does not show untrusted API message text as a localized interface error', () => {
  assert.equal(apiErrorMessage('unknown_backend_code', 'kk'), 'Сұрауды орындау мүмкін болмады. Қайталап көріңіз.');
  assert.equal(apiErrorMessage('execution_error', 'ru'), 'Не удалось выполнить задачу прогноза.');
});

test('localizes known run status and agent action codes but hides unknown machine codes', () => {
  assert.equal(runStatusLabel('degraded', 'kk'), 'ШЕКТЕУЛІ');
  assert.equal(eventActionLabel('fetch_weather', 'kk'), 'Ауа райын алу');
  assert.equal(eventActionLabel('provider_tool_v7', 'ru'), 'Записанное действие');
});

test('maps user-facing API error codes to Russian and Kazakh text', () => {
  assert.equal(apiErrorMessage('invalid_request', 'ru'), 'Проверьте параметры прогноза и повторите запрос.');
  assert.equal(apiErrorMessage('job_already_running', 'kk'), 'Болжам тапсырмасы орындалып жатыр. Оның күйін тексеріңіз.');
  assert.equal(apiErrorMessage('run_not_found', 'kk'), 'Сұрауды орындау мүмкін болмады. Қайталап көріңіз.');
});

test('formats forecast fractions with localized decimal separators and empty values', () => {
  assert.equal(typeof dashboardModel.formatPercentage, 'function');
  assert.equal(dashboardModel.formatPercentage(0.125, 'ru'), '12,5%');
  assert.equal(dashboardModel.formatPercentage(null, 'kk'), 'Қолжетімсіз');
});

test('interprets datetime-local values as Almaty UTC+05, regardless of machine timezone', () => {
  assert.equal(typeof dashboardModel.parseAlmatyDateTime, 'function');
  assert.equal(dashboardModel.parseAlmatyDateTime('2026-02-01T00:00')?.toISOString(), '2026-01-31T19:00:00.000Z');
  assert.equal(dashboardModel.parseAlmatyDateTime('2026-02-31T00:00'), null);
});

test('formats the default datetime-local origin in Asia/Almaty', () => {
  assert.equal(typeof dashboardModel.formatAlmatyDateTime, 'function');
  assert.equal(dashboardModel.formatAlmatyDateTime(new Date('2026-01-31T19:00:00.000Z')), '2026-02-01T00:00');
});

test('derives latest turbine medians and row count from selected API forecast rows', () => {
  assert.equal(typeof dashboardModel.forecastSummary, 'function');
  assert.deepEqual(dashboardModel.forecastSummary([
    { turbine_id: 'turbine_1', lead_hours: 1, p50: 0.14 },
    { turbine_id: 'turbine_1', lead_hours: 5, p50: 0.27 },
    { turbine_id: 'turbine_2', lead_hours: 3, p50: 0.08 },
  ], 'ru'), {
    turbine_1: '27%',
    turbine_2: '8%',
    rowCount: '3',
  });
});

test('does not treat a row without a lead hour as an actual latest median', () => {
  assert.deepEqual(dashboardModel.forecastSummary([
    { turbine_id: 'turbine_1', lead_hours: null, p50: 0.99 },
  ], 'kk'), {
    turbine_1: '—',
    turbine_2: '—',
    rowCount: '1',
  });
});
