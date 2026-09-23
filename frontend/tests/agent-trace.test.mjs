import assert from 'node:assert/strict';
import test from 'node:test';
import { eventDiagnosticLabels, eventReasonExplanation } from '../src/features/agent-trace/reasons.mjs';

test('explains successful forecast stages in Russian and Kazakh', () => {
  const cases = [
    ['fetch_weather', 'Погодные данные получены для интервала прогноза.', 'Болжам аралығына арналған ауа райы деректері алынды.'],
    ['validate_inputs', 'Проверены полнота погодных данных и их доступность к началу прогноза.', 'Ауа райы деректерінің толықтығы мен болжам басталғанға дейінгі қолжетімділігі тексерілді.'],
    ['check_for_updates', 'Проверены условия повторного использования прежнего прогноза.', 'Алдыңғы болжамды қайта пайдалану шарттары тексерілді.'],
    ['prepare_features', 'Признаки подготовлены по доступной истории и погодным данным.', 'Белгілер қолжетімді тарих пен ауа райы деректері бойынша дайындалды.'],
    ['train_or_load_model', 'Модель подготовлена к расчёту прогноза.', 'Болжамды есептеуге модель дайындалды.'],
    ['generate_forecast', 'Рассчитаны почасовые квантили прогноза.', 'Болжамның сағаттық квантильдері есептелді.'],
    ['quality_gate', 'Проверены почасовая сетка, конечность квантилей, их границы и происхождение данных.', 'Сағаттық тор, квантильдердің ақырлылығы мен шектері және деректердің шығу тегі тексерілді.'],
    ['analyze_result', 'Анализ завершён: выбрано сохранение прогноза.', 'Талдау аяқталды: болжамды сақтау таңдалды.'],
    ['persist_run', 'Артефакты запуска сохранены.', 'Іске қосу артефактілері сақталды.'],
  ];
  assert.equal(typeof eventReasonExplanation, 'function');
  for (const [state, ru, kk] of cases) {
    const event = { state, action: state === 'analyze_result' ? 'finalize' : undefined, outcome: 'success' };
    assert.equal(eventReasonExplanation(event, 'ru'), ru, state);
    assert.equal(eventReasonExplanation(event, 'kk'), kk, state);
  }
});

test('explains reuse checks, deterministic decisions, and known failures without hiding diagnostics', () => {
  assert.equal(
    eventReasonExplanation({ state: 'check_for_updates', outcome: 'skipped', reason: 'reuse_identity_unavailable; forecast_required' }, 'ru'),
    'Не хватает идентификаторов для безопасного повторного использования; будет рассчитан новый прогноз.',
  );
  assert.equal(
    eventReasonExplanation({ state: 'check_for_updates', outcome: 'skipped', reason: 'fingerprints_changed; forecast_required' }, 'kk'),
    'Кіріс деректер немесе модель өзгерген; жаңа болжам есептеледі.',
  );
  assert.equal(
    eventReasonExplanation({ state: 'check_for_updates', outcome: 'skipped', reason: 'update_check_failed; forecast_required' }, 'ru'),
    'Не удалось проверить возможность повторного использования; расчёт нового прогноза продолжается.',
  );
  assert.equal(
    eventReasonExplanation({ state: 'analyze_result', action: 'finalize', outcome: 'skipped', reason: 'validated_action:finalize' }, 'ru'),
    'Внешний анализ не использовался: встроенные правила выбрали сохранение прогноза.',
  );
  assert.equal(
    eventReasonExplanation({ state: 'analyze_result', action: 'finalize', outcome: 'success', reason: 'validated_action:finalize' }, 'ru'),
    'Анализ завершён: выбрано сохранение прогноза.',
  );
  assert.equal(
    eventReasonExplanation({ state: 'analyze_result', action: 'use_baseline', outcome: 'skipped', reason: 'deterministic_fallback:quality_gate_failed' }, 'kk'),
    'Сыртқы талдау қолданылмады: кіріктірілген ережелер негізгі модельге ауысуды таңдады.',
  );
  assert.equal(
    eventReasonExplanation({ state: 'analyze_result', outcome: 'failed', reason: 'analyzer_unavailable:TimeoutError' }, 'kk'),
    'Талдау қызметі уақытында жауап бермеді; кіріктірілген ережелердің қосалқы шешімі қолданылды.',
  );
  assert.equal(
    eventReasonExplanation({ state: 'check_for_updates', outcome: 'success', reason: 'matching_forecast_reused' }, 'kk'),
    'Деректер өзгермеген; сақталған болжам қайта пайдаланылды.',
  );
  assert.equal(
    eventReasonExplanation({ state: 'quality_gate', outcome: 'failed', reason: 'operation_failed:RuntimeError' }, 'ru'),
    'Проверка прогноза не завершилась из-за ошибки выполнения; точная запись доступна в технических деталях.',
  );
});

test('unknown event reasons receive neutral localized copy', () => {
  assert.equal(eventReasonExplanation({ state: 'future_stage', outcome: 'skipped', reason: 'new_unknown_code' }, 'ru'), 'Для этого шага нет пояснения; исходная запись доступна в технических деталях.');
  assert.equal(eventReasonExplanation({ state: 'future_stage', outcome: 'skipped', reason: 'new_unknown_code' }, 'kk'), 'Бұл қадамға түсіндірме жоқ; бастапқы жазба техникалық деректерде қолжетімді.');
});

test('labels the raw reason and run ID as diagnostics in both interface languages', () => {
  assert.deepEqual(eventDiagnosticLabels('ru'), {
    disclosure: 'Для диагностики',
    reason: 'Исходная запись',
    runId: 'Идентификатор запуска',
  });
  assert.deepEqual(eventDiagnosticLabels('kk'), {
    disclosure: 'Диагностика үшін',
    reason: 'Бастапқы жазба',
    runId: 'Іске қосу ID',
  });
});
