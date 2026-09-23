# Wind Power Forecast Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Реализовать воспроизводимый прогноз нормализованной мощности двух турбин на 24/48 часов, проверяемый backtest, ограниченный агентный цикл и dashboard.

**Architecture:** Один Python application layer обслуживает CLI и Streamlit. Детерминированные компоненты владеют данными, прогнозами, метриками, состояниями и файлами; OpenAI выбирает только разрешённые действия и объясняет вычисленную диагностику. Независимые модули реализуют Luna xhigh в отдельных терминалах общего worktree; Astra проверяет контракты, код и интеграцию.

**Tech Stack:** Python 3.11+, pandas, numpy, CatBoost CPU, requests, python-dotenv, OpenAI Python SDK, Streamlit, pytest. CSV/JSON/JSONL и CatBoost native artifacts; без БД, GPU, Docker и фонового сервиса.

**Spec:** `docs/superpowers/specs/2026-09-23-wind-power-agent-design.md` — прочитать целиком перед исполнением. Этот план конкретизирует спецификацию; расхождения и проверяемые допущения перечислены ниже.

## Global Constraints

Следующие строки воспроизведены из спецификации дословно:

- Time available for implementation is four hours.
- NVIDIA access is inference-only and is excluded from the MVP.
- Secrets must be loaded from `.env`.
- Both `.env` and the existing `api_keys.txt` must be ignored by Git and must never be copied into logs or artifacts.
- Aggregate measurements to hourly resolution when at least four of the expected six 10-minute observations are present.
- Predict mean normalized active power for each hour.
- Clip persisted point and quantile predictions to `[0, 1]` after validation.
- No feature may use an observation whose timestamp is later than the forecast origin.
- Allow at most two LLM calls per forecast run.
- Allow at most three weather-request attempts.
- Use cached weather only when its `issued_at` is not later than the forecast origin.
- Record each action, reason, timestamp, duration, and outcome in `events.jsonl`.
- Use one application layer for the CLI and Streamlit dashboard.
- Preserve the dashboard, README, provenance, and audit trail at the four-hour scope cutline.

Дополнительные обязательства исполнения: общий worktree; не переключать ветки, не создавать worktree или коммиты, не откатывать чужие изменения. Перед каждой задачей читать `git status --short`, brief, board и свой report. Общие файлы изменяет только оркестратор. Не читать содержимое `.env`/`api_keys.txt` в терминал. Команды ниже предназначены для будущего исполнения; написание этого плана не устанавливает зависимости и не запускает API.

## Review Focus

1. Hourly timestamp обозначает начало интервала: в origin нельзя включить неполный текущий час или target ещё не завершившегося часа. Проверка: T2/T4, наблюдение ровно на origin и target_end > cutoff.
2. Архив, hindcast и время инициализации не доказывают доступность прогноза в прошлом. Проверка: T3, unknown/hindcast и initialized-before-but-published-after.
3. После января нет фактической мощности: лаги стареют, метрики февраля отсутствуют, прогноз не становится наблюдением. Проверка: T4/T5/T7, origin 28 февраля без новых наблюдений.
4. Два refresh и частично записанный run не должны создавать две версии или показывать успешный результат. Проверка: T6/T8, concurrent refresh и ошибка записи.
5. Нулевые мощности, NaN/inf и пересечение квантилей не должны превращаться в красивую, но неверную статистику. Проверка: T5/T6, нулевой sMAPE, nonfinite rejection, quantile correction count.

---

## 1. Проверенные исходные условия и решения

На момент планирования продуктового кода нет. `README.md` изменён пользователем; `.hackflow/`, scripts, spec, CSV, `.env`, `api_keys.txt` и другие файлы untracked. `.hackflow/BRIEF.md` и `BOARD.md` ещё шаблонные. Существующий `scripts/hackflow` запускает product/builder/qa, где только builder пишет код, и содержит старый default `gpt-5.6-luna`. Не запускать его вслепую для многопоточной реализации.

Роли исполнения: Astra (`gpt-6-astra`) — основная сессия оркестратора; код — `gpt-6-luna`, reasoning `xhigh`. Это отдельно от runtime LLM продукта: `OPENAI_MODEL=gpt-6-luna`, `OPENAI_REASONING_EFFORT=none`. Перед запуском терминалов проверить реальный локальный каталог и доступность выбранных имён; не заменять модель молча. План не утверждает, что модель текущей сессии уже переключена.

### Временные и числовые соглашения

- Исходные CSV: `Dataset HackAlemAI для участников 11.03.2023-28.02.2026 - turbine 1.csv` и аналогичный `turbine 2.csv`.
- Поля: `Статистическое время`, `Средняя скорость ветра(m/s)`, `Нормализованная активная мощность`, `Средняя температура окружающей среды(°C)`; `ID` не является ключом времени.
- Spec называет одновременно `Asia/Almaty` и UTC+05:00 для всей истории. IANA-зона для ранней части истории может дать другой offset. Рабочее допущение: трактовать **все naive CSV как фиксированный +05:00**, как требует числовое соглашение spec; явно записать `source_timezone_policy=fixed_UTC+05:00`. Не маскировать это под проверенную локальную хронологию. Источник должен подтвердить старые timestamps перед утверждением исторической точности.
- Внутри и на границах: timezone-aware UTC. Origin обязан быть zoned и приходиться на начало часа. `2026-02-01T00:00:00+05:00` = `2026-01-31T19:00:00Z`.
- `valid_time` — начало прогнозируемого часа; `lead_hours=0..H-1`. Первый прогноз покрывает `[origin, origin+1h)`. Это явная конвенция при отсутствии submission schema.
- Hourly row: `[hour_start, hour_start+1h)`, `available_at=hour_start+1h`; доступность наблюдений только `available_at <= origin`. Внутри часа учитывать уникальные валидные 10-minute slots, а не число строк до deduplication.
- Ровно `2*H` строк, ключ `(turbine_id, valid_time)`, turbine_id — строки `turbine_1`, `turbine_2`. Нельзя компенсировать недостающий час дубликатом.
- Сумма нормализованных мощностей двух турбин имеет диапазон `[0,2]`; это не MW. Dashboard показывает среднее двух турбин `[0,1]` как normalized farm proxy. Средние marginal p10/p90 — описательная полоса, не калиброванный интервал суммы.

### Погода: обязательный исследовательский gate T3

Официальная [Single Runs API документация](https://open-meteo.com/en/docs/single-runs-api) описывает `run` как initialization time, отличает его от availability и обозначает раннюю историю IFS как hindcasts. Поэтому наличие ответа за январь/февраль 2026 не доказывает выполнение условия «прогноз существовал в тот момент».

Официальная [Previous Runs API документация](https://open-meteo.com/en/docs/previous-runs-api) описывает offsets относительно valid time. Суффикс `previous_day1` нельзя без дополнительных доказательств превращать в точный `issued_at` общего forecast-origin. Этот fallback включать только после доказательства безопасной временной границы каждой строки и доступности требуемых переменных.

Политика реализации: `initialized_at`, `issued_at`, `available_at`, `retrieved_at` — разные поля. `issued_at` означает доказанное время выпуска, не время скачивания и не предположение. `provenance_status=verified|assumed|unknown|hindcast|synthetic`; только verified с `available_at <= origin` допускается в competition evidence. Документировать доказательство, URL и способ определения времени. Предполагаемая задержка публикации сама по себе не становится verified. Данные с неподтверждённой историчностью можно использовать только в явно обозначенном demo режиме с `competition_valid=false`.

Если доступный Open-Meteo архив не позволяет доказать историчность, продолжить offline demo, UI, baseline и агентный цикл, но зафиксировать **невыполненный критерий competition provenance**. Замена провайдера — отдельное решение оркестратора с документированным источником, а не скрытая подмена на reanalysis. Это внешний риск и для CatBoost training, и для февральской симуляции.

### Разделение train / calibration / backtest

- По умолчанию daily training origins: 2025-10-01..2025-12-14, calibration origins: 2025-12-15..2025-12-29; оценка rolling-origin: 2026-01-01..2026-01-30. Последний январский 48h target должен завершаться к 2026-02-01 00:00+05.
- Загружать только доступную и доказанную историю прогноза; фактическое покрытие выводить явно. Пропущенные origins не заполнять погодными наблюдениями.
- Для каждого backtest-origin заново выбирать training rows, у которых `target_end <= origin`. Оценивать все модели на одних и тех же target keys. Если CatBoost не покрывает fold, показать coverage/failed-fold, не сравнивать разные выборки молча.
- Baseline selection и residual calibration использовать только до текущего origin. Общий январский leaderboard нельзя использовать для выбора baseline в раннем январском fold.
- Для финального прогноза можно refit на всех доступных labels до origin, сохранив независимую декабрьскую calibration/selection policy. Если расширенная выборка меняет калибровку, считать её отдельно из out-of-fold residuals, не in-sample.
- Февраль: 28 origins, по 48h; последний покрывает 1 марта включительно. 2688 строк без учёта дополнительных версий. `metrics=null`, `metric_status=unavailable_no_labels` для февральских targets. Январский backtest — отдельный artifact и отдельная подпись в UI.
- Погода февраля обновляется, турбинные наблюдения остаются январскими. Добавить `observation_age_hours`; пропуски лагов не заполнять предсказанными значениями. Старые наблюдения явно отмечать `stale_observations` и `degraded` при возрасте >24h (рабочая threshold policy).

## 2. Владение файлами и граф исполнения

| Задача | Владелец | Файлы / ответственность | Зависимости |
|---|---|---|---|
| T1 | orchestrator | project/config/contracts, общий test support, launcher/brief/board | нет |
| T2 | data | `wind_forecast/data.py`, `tests/test_data.py` | T1 |
| T3 | weather | `wind_forecast/weather.py`, `tests/test_weather.py`, `tests/fixtures/weather/` | T1 |
| T4 | ml | `wind_forecast/features.py`, `tests/test_features.py` | T1; T2/T3 для integration |
| T5 | ml | `wind_forecast/models.py`, `evaluation.py`, `tests/test_models.py`, `test_evaluation.py` | T2–T4 |
| T6 | agent | `wind_forecast/agent.py`, `llm.py`, `artifacts.py`, соответствующие tests | T1; T3/T5 для integration |
| T7 | orchestrator + cli worker | `wind_forecast/service.py`, `cli.py`, `tests/test_cli.py`, `test_e2e.py` | T2–T6 |
| T8 | ui | `app.py`, `wind_forecast/ui/`, `.streamlit/config.toml`, `tests/test_dashboard.py`, `docs/DEMO.md` | контракты T1; T7 для integration |
| T9 | orchestrator + qa | README, lockfile, `tests/test_acceptance.py`, reports | T1–T8 |

`wind_forecast/__init__.py` и `tests/__init__.py` пустые; project root package упрощает требуемую CLI-команду. `config.py` читает только allowlisted настройки; `contracts.py` фиксирует schema и типы; `service.py` — единственная сборка зависимостей, API для CLI/UI. Не добавлять новые общие файлы без назначения владельца.

Волны: T1 → (T2 + T3 + T4) → (T5 + T6 + UI layout T8) → T7 → (UI integration T8 + QA T9). Агент/UI работают по контрактам на fake objects; не меняют контракты самостоятельно. Не более трёх активных coding workers; тяжёлый CatBoost training одновременно только один, `thread_count=4`.

Четырёхчасовой бюджет — целевой после принятия плана, не гарантия: 0–20 T1; 20–70 T2/T3/T4; 70–125 T5/T6 и UI; 125–165 T7; 165–205 T8; 205–240 T9. На 70-й минуте принять решение по weather gate, на 125-й — по quantile cutline. Если live archive недоступен, не тратить весь бюджет на retries: выпустить честно маркированный demo и записать незакрытый критерий.

## 3. Общие контракты для всех исполнителей

T1 создаёт типы в `contracts.py`; будущие методы реализуются в указанных задачах. DataFrames имеют обязательный schema validator; не полагаться только на аннотацию `pd.DataFrame`.

```python
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
import pandas as pd

Action = Literal['continue', 'retry_weather', 'use_cached_weather',
                 'use_baseline', 'abort', 'finalize']

@dataclass(frozen=True)
class RunRequest:
    origin: datetime
    horizon: int = 48
    mode: Literal['competition', 'demo'] = 'competition'

@dataclass
class WeatherSnapshot:
    rows: pd.DataFrame
    raw_responses: list[dict[str, Any]]
    fingerprint: str
    provenance: dict[str, Any]

@dataclass
class Prediction:
    rows: pd.DataFrame
    model_name: str
    diagnostics: dict[str, Any]

@dataclass(frozen=True)
class Decision:
    action: Action
    reason: str

@dataclass
class RunResult:
    run_id: str
    directory: Path
    status: Literal['success', 'degraded', 'failed']
    reused: bool
```

Data schema: `turbine_id, hour_start, available_at, power, wind_speed, temperature, observation_count, quality_flag`; count 0..6, flags `good|partial|invalid|missing`, power nullable only for invalid/missing. `partial` означает 4/5, `good` — 6, invalid — 1..3.

Weather schema: `turbine_id, valid_time, initialized_at, issued_at, available_at, lead_hours, weather_model, wind_speed_10m, wind_speed_100m, wind_direction_10m, wind_gusts_10m, temperature_2m, surface_pressure`; forecast-origin lead не путать с `model_lead_hours=(valid_time-initialized_at)/1h` в provenance. Nullable provenance допустима только для noncompetition demo.

Feature metadata: `turbine_id, forecast_origin, valid_time, lead_hours, max_observation_available_at, target_end`; training rows дополнительно `target`. Features: weather variables; sin/cos hour/month/weekday; last power/wind; power mean/std и count для последних 6/24/168 валидных наблюдений; day/week lag; lag missing indicators; observation age. Ни target, ни raw timestamps, ни provenance metadata не входят в model X.

Forecast CSV columns **строго в порядке spec**: `run_id,forecast_origin,turbine_id,valid_time,lead_hours,p10,p50,p90,weather_issued_at,weather_model,run_status`. Все timestamps ISO UTC, без локальных naive strings. `run_status` дублирует manifest status, `competition_valid` находится в manifest.

Manifest schema v1: `schema_version,run_id,forecast_origin,horizon,mode,status,competition_valid,created_at,parent_run_id,fingerprints,model,weather_provenance,data_quality,calibration,files`. Fingerprints: data contents, canonical weather, config, feature schema, model training rows. `model`: name, parameters, seed, training_cutoff, training row count, calibration cutoff, dependency versions. `files`: basename→SHA256 для остальных файлов, без hash самого manifest.

Metrics schema v1: `schema_version,metric_status,evaluation_period,models,coverage`; models — records `(model,turbine_id,lead_hours,n,mae,rmse,smape,bias,interval_coverage)`. Отсутствующие метрики — JSON null, не NaN и не 0.

Event: `schema_version,run_id,sequence,state,action,reason,timestamp,duration_ms,outcome`; `outcome=success|failed|skipped|rejected`, timestamp UTC, duration неотрицательная. Никаких API credentials, HTTP Authorization или произвольного environment dump.

## T1. Исполняемый project skeleton, контракты и безопасная координация

**Files:** Create `pyproject.toml`, `.gitignore`, `.env.example`, `wind_forecast/__init__.py`, `config.py`, `contracts.py`, `tests/__init__.py`, `tests/test_contracts.py`; modify `.hackflow/BRIEF.md`, `.hackflow/BOARD.md`, `scripts/hackflow`, `.hackflow/prompts/`.

**Interfaces:** Produces все dataclasses выше; `parse_request(origin: str, horizon: int, mode: str='competition') -> RunRequest`; `Settings.from_env() -> Settings`, settings paths `data_dir/cache_dir/model_dir/run_dir`, LLM settings и timeout. Каталоги по умолчанию `.cache/wind`, `artifacts/models`, `artifacts/runs`.

- [ ] Перенести цель, ограничения, judging weights и demo definition из spec в brief; указать spec как подробный источник и погодный риск. Доска: конкретные T1–T9, owner, dependency, acceptance command; сначала активна только T1. Поддерживать reports отдельно по ролям.
- [ ] Записать failing tests в `tests/test_contracts.py`:

```python
import pytest
from wind_forecast.contracts import parse_request

def test_zoned_origin_and_lead_contract():
    r = parse_request('2026-02-01T00:00:00+05:00', 48)
    assert r.origin.isoformat() == '2026-01-31T19:00:00+00:00'
    assert r.horizon == 48

@pytest.mark.parametrize('origin,horizon', [
    ('2026-02-01T00:00:00', 48),
    ('2026-02-01T00:30:00+05:00', 48),
    ('2026-02-01T00:00:00+05:00', 25),
])
def test_invalid_request(origin, horizon):
    with pytest.raises(ValueError):
        parse_request(origin, horizon)
```

- [ ] Создать `pyproject.toml`: setuptools build, name `wind-forecast`, Python>=3.11, dependencies pandas/numpy/requests/python-dotenv/openai/streamlit; optional `ml=[catboost]`, `dev=[pytest]`; pytest testpaths `tests`, marker `live`. Создать venv и установить: `python3 -m venv .venv`, `.venv/bin/python -m pip install -e '.[ml,dev]'`. При сетевой блокировке запросить предусмотренное средой разрешение; не обходить sandbox. Если CatBoost не устанавливается, core app обязано устанавливаться с `.[dev]` и фиксировать degraded.
- [ ] Выполнить `.venv/bin/python -m pytest tests/test_contracts.py -q`; ожидается import failure ещё не реализованных contracts, не ошибка отсутствия pytest. Затем реализовать dataclasses и parser:

```python
from datetime import datetime, timezone

def parse_request(origin: str, horizon: int, mode: str = 'competition') -> RunRequest:
    value = datetime.fromisoformat(origin.replace('Z', '+00:00'))
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('origin must include a timezone')
    value = value.astimezone(timezone.utc)
    if value.minute or value.second or value.microsecond:
        raise ValueError('origin must be an hour boundary')
    if horizon not in (24, 48) or mode not in ('competition', 'demo'):
        raise ValueError('invalid horizon or mode')
    return RunRequest(value, horizon, mode)
```

- [ ] `.gitignore` дополнить `.env`, `.env.*`, `!.env.example`, `api_keys.txt`, `.venv/`, `__pycache__/`, `.pytest_cache/`, `.cache/`, `artifacts/`, `*.egg-info/`; `.env.example` содержит пустой OPENAI_API_KEY, model/effort из spec, пути и HTTP_TIMEOUT_SECONDS=20. Settings загружает dotenv и конкретные ключи, не сериализует секреты.
- [ ] Подготовить launcher для `start data weather ml` и следующих назначенных ролей; по умолчанию Luna xhigh; `doctor` проверяет каталог, shell, prompts. Сохранить существующие product/builder/qa режимы. Worker prompt обязан перечислить разрешённые файлы из таблицы, прочитать spec+план+board, не писать общие файлы, записать report. Проверить `bash -n scripts/hackflow`. До проверки доступности модели терминалы не запускать; при её отсутствии сообщить точную ошибку, без замены slug.
- [ ] Повторить contract tests; `git check-ignore .env api_keys.txt artifacts/runs/example/forecast.csv` должен перечислить все три пути. Astra проверяет diff и контракты, отмечает T1 done, назначает T2/T3/T4. Commit не создавать.

## T2. CSV → валидная почасовая история

**Files:** Create `wind_forecast/data.py`, `tests/test_data.py`.

**Interfaces:** `DataPipeline.load(paths: dict[str, Path]) -> pd.DataFrame`; `DataPipeline.hourly(raw: pd.DataFrame) -> pd.DataFrame`; `DataPipeline.prepare(paths: dict[str, Path], output: Path) -> pd.DataFrame`. Raw schema: turbine_id, timestamp, power, wind_speed, temperature. Output — data schema §3; `output/hourly.csv`, `output/quality.json`.

- [ ] Написать тест минимального CSV через `tmp_path.write_text` в тесте; включить `0:00:00` и `00:10:00`, сохранить кириллические headers из §1. Проверить разбор одинакового формата `%Y-%m-%d %H:%M:%S` и UTC offset. Тест aggregation:

```python
import pandas as pd
from wind_forecast.data import DataPipeline

def test_partial_hour_and_unavailable_current_hour():
    raw = pd.DataFrame({
        'turbine_id': ['turbine_1'] * 4,
        'timestamp': pd.date_range('2026-01-31T18:00Z', periods=4, freq='10min'),
        'power': [0.2, 0.4, 0.6, 0.8],
        'wind_speed': [5.] * 4, 'temperature': [0.] * 4,
    })
    row = DataPipeline().hourly(raw).iloc[0]
    assert row.observation_count == 4
    assert row.quality_flag == 'partial'
    assert abs(row.power - 0.5) < 1e-9
    assert row.available_at == pd.Timestamp('2026-01-31T19:00Z')
```

- [ ] `.venv/bin/python -m pytest tests/test_data.py -q` → FAIL по отсутствующей реализации. Реализовать чтение, mapping, numeric coercion и explicit datetime parse; timezone через `datetime.timezone(timedelta(hours=5))`. Точные дубликаты схлопнуть и посчитать; противоречащие друг другу строки одной турбины/времени отклонить с безопасной диагностикой. Неверный timestamp/negative wind/nonfinite/out-of-range power — исключить из valid counts и отразить в quality report; отсутствующие headers — ValueError.
- [ ] Алгоритм aggregation: отсортировать по turbine/time; проверить minute%10==0, second==0; resample каждой турбины по `1h`, left closed/left labeled; reindex пропущенные часы; средние только по строкам, где все нужные numeric values валидны; при count<4 power=NaN и flag invalid/missing. Температуру не отбрасывать по наблюдавшимся extrema как по физическим пределам.

```python
counts = group.resample('1h')['power'].count()
hourly = group.resample('1h')[['power', 'wind_speed', 'temperature']].mean()
hourly['observation_count'] = counts
hourly.loc[counts < 4, 'power'] = float('nan')
hourly['available_at'] = hourly.index + pd.Timedelta(hours=1)
```

- [ ] Добавить случаи count=3/0/6, дубликат не увеличивает count, conflicting duplicates, shuffled input, неверное число, пустой файл и header mismatch. Каждый сначала должен обнаружить отсутствие нужного поведения, затем пройти.
- [ ] Повторить suite; локальная prepare обоих supplied CSV должна подтвердить фактический min/max и quality summary. Не требовать заранее заданного числа hourly rows. Report: dropped rows, timezone assumption, последняя доступность, checks. Astra проверяет сохранение пропусков и границы интервалов.

## T3. Погодный provider, provenance gate и cache

**Files:** Create `wind_forecast/weather.py`, `tests/test_weather.py`, `tests/fixtures/weather/synthetic_48h.json`, `tests/fixtures/weather/README.md`.

**Interfaces:** `WeatherProvider(cache_dir: Path, transport: requests.Session | None=None)`; `fetch(request: RunRequest, refresh: bool=False) -> WeatherSnapshot`; `validate_weather(snapshot: WeatherSnapshot, request: RunRequest) -> None`; `weather_fingerprint(rows: pd.DataFrame, provenance: dict) -> str`. Validation raises ValueError with stable reason code; no requests from tests.

- [ ] Провести ограниченный probe официального Single Runs за первый origin: explicit model, обе координаты из spec, UTC, m/s, variables из §3, `forecast_hours` достаточно для offset от model initialization плюс 48h. Параметр `run` передавать без секунд. Сначала документировать доступные model slug/variables, response coverage, происхождение hindcast и доказательство availability. Не утверждать доступность 100m/gusts до проверки. Сохранить очищенный raw response только в cache; fixture README указывает источник, capture time, provenance status. Synthetic fixture не выдавать за recorded real API response.
- [ ] Создать тестовую snapshot в самом тесте: два turbine_id × `pd.date_range(request.origin, periods=48, freq='1h')`, finite weather, initialized/issued/available до origin, `provenance_status=verified` только как явно синтетическое утверждение test double. Реальный synthetic fixture имеет `provenance_status=synthetic` и mode demo. Контрольный тест:

```python
import pandas as pd
import pytest
from wind_forecast.contracts import WeatherSnapshot, parse_request
from wind_forecast.weather import validate_weather

def test_future_availability_is_rejected():
    req = parse_request('2026-02-01T00:00:00+05:00', 24)
    snapshot = WeatherSnapshot(pd.DataFrame({
        'available_at': [pd.Timestamp(req.origin) + pd.Timedelta(hours=1)],
    }), [], 'test', {'provenance_status': 'verified'})
    with pytest.raises(ValueError):
        validate_weather(snapshot, req)
```

- [ ] Запустить tests → FAIL. Реализовать selection только proven-available cycle, сначала cache, потом network. Default transport timeout 20s, максимум три HTTP attempts всего на fetch, обе координаты в batch если поддерживается; exponential delays 1/2s только между retryable timeout/429/5xx, не retry 4xx validation errors. Если batch не поддерживается, бюджет общий, cached coordinate не запрашивать повторно. Сохранить request metadata отдельно от неизменённых response objects в `raw_responses`.
- [ ] При отсутствии verified dataset competition validation возвращает `unverified_weather_provenance`; demo допускает synthetic/assumed с `competition_valid=false`. Не включать Previous Runs без доказанной per-row доступности; наличие fallback в spec не разрешает фиктивный issued_at. Стабильный код missing variable/coverage не превращать в успешный прогноз.
- [ ] Cache key: provider/model/coordinates/run/variable set/units. Fingerprint: сортировка turbine/valid_time, значения и provenance; исключить retrieved_at, generationtime_ms, HTTP order. При hash изменении сохранять новую snapshot, не перезаписывать файл, используемый завершённым run.

```python
import hashlib
import json

def canonical_hash(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'),
                         allow_nan=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()
```

- [ ] Проверки: unknown/hindcast competition rejection; initialized<=origin, available>origin rejection; changed numeric value меняет hash; changed retrieval time не меняет; missing turbine/hour, duplicate hour, wrong units, null weather; cached future issue rejected; не более трёх transport calls. Тест future availability должен проверять конкретный reason code на полной валидной fixture, чтобы schema failure не дал ложный PASS.
- [ ] Повторить tests; записать weather report с исходом probe, URL evidence и точным статусом competition feasibility. Astra отдельно проверяет происхождение архива. Провал gate — известный blocker competition evidence, а не повод скрыть риск в degraded label.

## T4. Point-in-time features и обучающие строки

**Files:** Create `wind_forecast/features.py`, `tests/test_features.py`.

**Interfaces:** `FeaturePipeline.build(history: pd.DataFrame, weather: WeatherSnapshot, request: RunRequest) -> pd.DataFrame`; `training_rows(history: pd.DataFrame, snapshots: list[tuple[RunRequest, WeatherSnapshot]], cutoff: datetime) -> pd.DataFrame`; `FEATURE_COLUMNS: tuple[str,...]` экспортируется из features.py, содержит только predictor columns §3.

- [ ] Написать тест инвариантности: построить историю с завершёнными часами до origin и одной строкой с available_at > origin, вызвать build, заменить в будущей строке power на 0.99; frame должен остаться идентичным. Отдельно observation ровно на начале origin-часа недоступен до его конца. Тест training labels:

```python
def test_training_labels_available_by_cutoff(history, snapshots, cutoff):
    from wind_forecast.features import training_rows
    rows = training_rows(history, snapshots, cutoff)
    assert len(rows) > 0
    assert (rows['target_end'] <= cutoff).all()
    assert (rows['max_observation_available_at'] <= rows['forecast_origin']).all()
    assert 'target' not in __import__(
        'wind_forecast.features', fromlist=['FEATURE_COLUMNS']).FEATURE_COLUMNS
```

Fixtures `history,snapshots,cutoff` определить локально в `test_features.py`: hourly starts 2025-12-01..2026-01-03 UTC, две турбины с power=0.5, complete count=6; snapshots для zoned origins 2026-01-01 и 2026-01-02, полный 48h weather; cutoff=2026-01-03T00:00Z. Значения synthetic, сетевых вызовов нет.

- [ ] Запустить тесты → FAIL; реализовать фильтр `history.available_at <= origin` перед любыми rolling/lag операциями. Для каждого turbine вычислить state один раз на origin, join с погодой на `(turbine_id,valid_time)`, assert one-to-one и точный horizon grid.

```python
known = history.loc[(history.available_at <= request.origin) & history.power.notna()]
known = known.sort_values(['turbine_id', 'hour_start'])
# Внутри каждого turbine: последние k валидных наблюдений, без bfill.
last_24 = turbine_history.tail(24)
mean_24 = last_24.power.mean()
std_24 = last_24.power.std(ddof=0)
```

- [ ] Day/week lag определяется для каждого target как `valid_time-24h`/`valid_time-168h`, но только если available_at<=origin; иначе NaN+missing indicator. Seasonal baseline имеет отдельную repeat-last-day policy T5. Пустая история турбины — отказ `missing_turbine_history`; короткая история сохраняет counts, std=0 для одного значения. Для CatBoost numeric missing допустим, inf запрещён.
- [ ] `training_rows` строит features на каждом historical origin, затем join target по turbine/valid_time, отбрасывает target_end>cutoff и invalid targets. Weather validation обязана применяться до join. Производные календаря вычисляются в фиксированном +05 согласно общей policy.
- [ ] Тесты: 48-й горизонт не читает истинное t-24 из будущего; future target exclusion; short history; февральское старение и отсутствие backfill; изменение будущих наблюдений/labels не меняет X и fit fingerprint. Запустить tests, Astra проверяет отсутствие global preprocessing до split.

## T5. Baselines, CatBoost, калибровка и rolling backtest

**Files:** Create `wind_forecast/models.py`, `wind_forecast/evaluation.py`, `tests/test_models.py`, `tests/test_evaluation.py`.

**Interfaces:** `BaselineModel.fit(history: pd.DataFrame, cutoff: datetime) -> BaselineModel`; `predict(features: pd.DataFrame, strategy: str) -> Prediction`; `ForecastModel.fit(rows: pd.DataFrame, calibration: pd.DataFrame) -> ForecastModel`; `predict(features: pd.DataFrame) -> Prediction`; `save(path: Path) -> None`; `load(path: Path) -> ForecastModel`; `score(y, p50, p10=None, p90=None) -> dict`; `rolling_backtest(history, snapshots, origins: list[datetime], model_dir: Path) -> dict`. Strategies `persistence|seasonal|power_curve`; Snapshot list contract из T4.

- [ ] Записать численно точные metric tests, затем выполнить `.venv/bin/python -m pytest tests/test_models.py tests/test_evaluation.py -q` → FAIL:

```python
import pytest
from wind_forecast.evaluation import score

def test_metrics_and_zero_denominator():
    m = score([0., 1.], [0., 0.5], [0., 0.4], [0.1, 1.])
    assert m['mae'] == pytest.approx(0.25)
    assert m['rmse'] == pytest.approx((0.125) ** 0.5)
    assert m['bias'] == pytest.approx(-0.25)
    assert m['smape'] == pytest.approx(100 / 3)
    assert m['interval_coverage'] == 1.0
    assert score([0.], [0.])['smape'] == 0.0
```

- [ ] Реализовать persistence=last observed power; seasonal=последний доступный суточный профиль, для второго дня повторять тот же профиль, missing slot→persistence с диагностикой. Power curve: по turbine, wind bin шириной 1m/s, median power на observations available<=cutoff; линейная интерполяция между заполненными bins, outside→nearest bin; inference по forecast wind10m, отметить measurement-height mismatch. Curve не обучать на будущих данных и не вычислять по test targets.
- [ ] Metrics: MAE, RMSE, bias=mean(pred-truth), sMAPE=mean(200*abs(error)/(abs(truth)+abs(pred))) с нулём при обоих нулях; coverage inclusive bounds; pairwise missing targets excluded с n/coverage; nonfinite predictions — error, не silent drop. Score по turbine и каждому lead плюс overall.
- [ ] CatBoost импортировать лениво. Три `CatBoostRegressor` с `loss_function='Quantile:alpha=0.1'/'0.5'/'0.9'`, iterations=250, depth=6, learning_rate=0.05, random_seed=42, thread_count=4, allow_writing_files=False, verbose=False; turbine_id categorical string, остальные FEATURE_COLUMNS numeric. Parameters зафиксировать в manifest, не проводить дорогой search.

```python
from catboost import CatBoostRegressor

model = CatBoostRegressor(
    loss_function='Quantile:alpha=0.5', iterations=250, depth=6,
    learning_rate=0.05, random_seed=42, thread_count=4,
    allow_writing_files=False, verbose=False,
)
model.fit(rows[list(FEATURE_COLUMNS)], rows['target'], cat_features=['turbine_id'])
```

- [ ] Calibration: residuals `truth-pred` только out-of-sample pre-origin; baseline bounds = point + residual q10/q90 по turbine, minimum 30 residuals, иначе global pool при n>=30. Если residual evidence недостаточно, demo interval `[0,1]` с `calibration_status=unavailable`, degraded; не называть калиброванным. Cutline: один p50 CatBoost и такой же residual interval с `uncertainty_method=empirical_residual`. Не обучать три модели после принятия cutline.
- [ ] Selection best validated baseline по calibration MAE, tie priority persistence→seasonal→power_curve. Если нет evaluation history, persistence — unvalidated fallback с degraded. CatBoost failure поймать только на его boundary, не поглощать leakage/data errors.
- [ ] Training fingerprint включает canonical X/y, cutoff, feature schema, parameters, calibration rows и versions. Сохранять `.cbm` и JSON metadata; baseline curve/profile/residuals — JSON, не executable pickle. Load только собственные локальные artifacts с matching metadata. Каждый fold не может загрузить model с training_cutoff после origin.
- [ ] Rolling backtest: для каждого daily origin построить train cutoff, fit/load fold model, predict 48h, отдельно присоединить truth для metrics; no random split. Persist `artifacts/backtest/metrics.json` и predictions.csv; `training_rows` и weather subset использованы одинаково всеми моделями. Missing labels не превращать в нули.
- [ ] Tests: persistence ожидаемые константы; seasonal second day без будущего truth; power-curve cutoff; poisoned future labels не меняют ранний fold; baseline winner использует только calibration; CatBoost tiny CPU fit/load (30 iterations в тесте через configurable params); reproducible outputs в tolerance; calibration excludes validation; missing February labels → null metrics. Повторить suite. Astra проверяет split и selection, а не только средний MAE.

## T6. Ограниченный агент, LLM decisions и атомарные artifacts

**Files:** Create `wind_forecast/agent.py`, `llm.py`, `artifacts.py`, `tests/test_agent.py`, `test_llm.py`, `test_artifacts.py`.

**Interfaces:** `ForecastAgent(weather_provider, predictor, analyzer, store)`; `run(request: RunRequest, history: pd.DataFrame, refresh: bool=False) -> RunResult`; injected `predictor(history, snapshot, request) -> Prediction`; `analyzer(diagnostics: dict, allowed: set[str]) -> Decision`; `ArtifactStore(root: Path).persist(request, prediction, snapshot, metrics: dict, events: list[dict], report: str, manifest: dict) -> RunResult`; `quality_gate(prediction: Prediction, snapshot: WeatherSnapshot, request: RunRequest) -> Prediction`.

- [ ] Написать agent tests с fake provider/predictor/analyzer/store, записывающими call order; fake analyzer бросает TimeoutError, predictor возвращает полную 2H grid p10=.2/p50=.4/p90=.6. Ожидание: все deterministic стадии выполнены, run persisted, LLM outage отражён в events. Fake predictor failure требует baseline choice, а input provenance failure не имеет обхода через baseline.
- [ ] `.venv/bin/python -m pytest tests/test_agent.py tests/test_llm.py tests/test_artifacts.py -q` → FAIL; реализовать states в порядке spec. Перед выходом из каждого state записать start/duration/outcome. Лимиты HTTP/LLM принадлежат run context, а не обнуляются при переходе retry. SDK retries отключить `max_retries=0`.
- [ ] Transition table: fetch failure допускает retry_weather при remaining attempts, use_cached_weather только после validation, abort; model failure — use_baseline|abort; passed quality gate — continue|finalize|abort; failed gate — abort или повторная генерация валидным baseline при model-only failure, никогда direct finalize. analyze_result на успешном пути только после gate. Не более двух обращений analyzer независимо от ошибок/parsing/отказов.
- [ ] Responses function calling реализовать по [официальной документации OpenAI](https://developers.openai.com/api/docs/guides/function-calling). Сохранить заданный spec model, неподдерживаемый model/effort обрабатывать как unavailable; не подменять модель. Использовать один named tool, строгую schema и отключённый parallel tool calling:

```python
tool = {
    'type': 'function', 'name': 'choose_action',
    'description': 'Choose a permitted workflow action from computed diagnostics.',
    'strict': True,
    'parameters': {
        'type': 'object', 'additionalProperties': False,
        'properties': {
            'action': {'type': 'string', 'enum': sorted(allowed)},
            'reason': {'type': 'string'},
        },
        'required': ['action', 'reason'],
    },
}
response = client.responses.create(
    model=settings.openai_model,
    reasoning={'effort': settings.openai_reasoning_effort},
    input=diagnostics_json,
    tools=[tool], tool_choice={'type': 'function', 'name': 'choose_action'},
    parallel_tool_calls=False,
)
```

`diagnostics_json` строить allowlist из numeric metrics, coverage, status, model name; не из raw CSV или environment. Parse ровно один matching function_call, JSON action∈allowed, reason string с ограничением 2000 chars; иначе deterministic fallback. Fake response tests, никаких обязательных live OpenAI calls. `reason` объясняет вычисленное решение; report не использует числа, придуманные LLM.
- [ ] Quality gate: exact key grid, finite quantiles, valid weather provenance; до clipping отвергнуть NaN/inf. Quantile crossings исправить сортировкой трёх finite значений по строке, clip [0,1], записать correction/clipping counts и повторно проверить `0<=p10<=p50<=p90<=1`. При значительных corrections run degraded; policy любое исправление>0. Это явная postprocessing policy, не замалчивание проблем.
- [ ] Store: lock по origin/horizon/mode через exclusive file creation; stale lock не удалять автоматически без проверки процесса. Request fingerprint для reuse включает weather+data+config+model identity, а не только weather. Refresh с тем же weather и прочими inputs возвращает existing run_id, `reused=true`; изменённая погода создаёт новый run_id и parent_run_id. Другая origin/horizon никогда не reuse старый прогноз. Изменение training data тоже требует нового run.
- [ ] Создать staging directory под artifacts/runs, записать шесть файлов, schema validate, hashes, затем atomic rename в уникальный final directory; latest pointer обновить атомарно только после успеха. On failure отдельный failed manifest/events без успешного forecast. `weather.json` хранит raw responses в envelope без изменения response payload, provenance в manifest. Секреты не входят в serializable state и HTTP exceptions сохраняются как код/тип, без URL с credentials.

```python
import json

serialized = json.dumps(manifest, ensure_ascii=False, allow_nan=False, indent=2)
# Запись в заранее созданный staging каталог; rename только после всех checks.
(staging / 'manifest.json').write_text(serialized, encoding='utf-8')
staging.rename(final_directory)
```

- [ ] Tests: unknown tool/action, malformed JSON, third LLM call blocked, future cache rejected, unavailable API deterministic report, CatBoost fail→baseline, quality fail→no success, nan/inf, correction count, duplicate hour despite 2H length, path outside root rejected, simulated disk error no latest update, same refresh twice, changed snapshot version, two concurrent refresh one persisted result. Astra проверяет transition table и failure artifacts.

## T7. Сервис, CLI и февральская симуляция

**Files:** Create `wind_forecast/service.py`, `wind_forecast/cli.py`, `tests/test_cli.py`, `tests/test_e2e.py`.

**Interfaces:** `Application(settings)`; methods `prepare() -> Path`, `train() -> Path`, `backtest() -> Path`, `run(request: RunRequest, refresh: bool=False) -> RunResult`, `simulate(start: date, end: date) -> list[RunResult]`, `latest() -> RunResult | None`, `read_run(run_id: str) -> dict`. `read_run` возвращает forecast DataFrame, metrics/manifest dicts, events list, report string. UI и CLI не собирают модели/провайдеры самостоятельно.

- [ ] Записать CLI tests через subprocess с temporary fixture data/cache/run directories, env без OPENAI_API_KEY, fixture mode demo. Тест naive origin должен вернуть code 2 с понятной ошибкой. Определить argparse команды из spec, global `--mode competition|demo`, `--data-dir`, `--cache-dir`, `--run-dir`, `--weather-fixture`. Fixture разрешён только demo, это видно в stdout и manifest.
- [ ] `.venv/bin/python -m pytest tests/test_cli.py tests/test_e2e.py -q` → FAIL; implement thin dispatch:

```python
def execute_run(app, args):
    request = parse_request(args.origin, args.horizon, args.mode)
    result = app.run(request, refresh=args.refresh)
    print(result.directory)
    return 1 if result.status == 'failed' else 0
```

`main(argv: list[str] | None=None) -> int`; `if __name__ == '__main__': raise SystemExit(main())`. Exit 0 success/degraded с явным status, 1 execution failure, 2 invalid arguments. `train` использует доступные pre-origin snapshots, `backtest` конкретные defaults §1, `run` при отсутствии model сам делает train/load, чтобы CLI demo не зависел от скрытых ручных шагов.
- [ ] Application строит predictor, FeaturePipeline, модель и baselines; run-history ограничивается origin. Train/load fingerprint содержит фактические labels, calibration и model config. run.metrics ссылается на прошлый backtest только с его evaluation_period, не выдаёт его за точность текущего прогноза.
- [ ] `simulate`: origins inclusive от start до end, локальные midnights +05, horizon 48; cache и модели переиспользуются только по fingerprint. Записывать `artifacts/simulations/<id>/index.json` с каждой origin, result/status/error, не прерывать весь batch на одном неудачном дне, итоговый CLI exit=1 если есть failed origins. Нет February observations — сохраняется старая history и возраст, не teacher forcing и не synthetic truth.

```python
from datetime import datetime, time, timedelta, timezone

day = start
origins = []
while day <= end:
    origins.append(datetime.combine(day, time(), timezone(timedelta(hours=5))))
    day += timedelta(days=1)
```

- [ ] E2E: synthetic turbine CSV covering 8 days до first origin + 48h fixture; вызвать Application.run без OpenAI с принудительным CatBoost failure, проверить 96 unique rows, UTC, bounds, шесть artifacts, degraded+competition_valid=false, events order. Отдельно test fixture с реальной recorded погодой, если T3 gate прошёл; synthetic test не заменяет live provenance evidence.
- [ ] Simulate test fake service на все 28 дней: 28 различных origins, 2688 predictions, last valid hour соответствует 1 марта 23:00+05, нет февральских метрик/наблюдений, ошибки indexed. Verify CLI help и оба горизонта. Astra проверяет одинаковую логику CLI/UI и отсутствие скрытых prerequisites.

## T8. Dashboard с индивидуальным дизайном и сценарий демонстрации

**Files:** Create `app.py`, `wind_forecast/ui/__init__.py`, `wind_forecast/ui/theme.css`, `wind_forecast/ui/components.py`, `wind_forecast/ui/charts.py`, `.streamlit/config.toml`, `tests/test_dashboard.py`, `docs/DEMO.md`.

**Interfaces:** Только Application methods из T7. `render(app: Application) -> None`; tests inject fake Application. Видимые вкладки: Overview, Forecast, Backtest, Agent Trace, Data Quality.

- [ ] Перед работой прочитать доступный frontend/design skill и его обязательные references; правило выбора skills — §4. Пользователь явно требует красивый веб-интерфейс, стандартный вид Streamlit не принимается. Streamlit сохраняется как runtime согласно spec; отдельный React/API слой не добавляется без продуктовой необходимости.
- [ ] Зафиксировать visual direction: операторская ветропарка с тёмной боковой навигацией, светлой основной областью, выразительной типографикой и большим графиком прогноза. Основной фон `#F4F7F8`, панели `#FFFFFF`, текст `#142D35`, muted `#526871`, sidebar `#102B34`, акцент `#087F73`, вторая турбина `#2563EB`, warning `#9A5700`, error `#B42318`. Это исходные tokens, контраст проверить в браузере. System font stack без сетевой загрузки, tabular numerals для метрик; текст 14–16px, заголовки 24–32px, metric values 28–36px, spacing 8/16/24/32px, card radius 12px, тонкие borders и умеренные shadows.
- [ ] Компоновка desktop: боковая панель около 220px с названием продукта и пятью разделами; сверху заголовок выбранного раздела, origin/timezone и status badge; ниже четыре компактные metric cards; основной график занимает большую часть ширины, справа краткая сводка двух турбин/карта. Run — одна выраженная primary action, Refresh — secondary рядом с параметрами. Метаданные, хеши и полные traces вынести в соответствующие разделы, не перегружать первый экран. На узком экране карточки перестраиваются в одну/две колонки, sidebar сворачивается, графики не создают горизонтальный скролл всей страницы.
- [ ] Создать CSS/theme и небольшие presentation helpers. `app.py` отвечает за navigation/state, `components.py` — карточки и status blocks, `charts.py` — единый стиль axes/legend/tooltips/interval bands. Использовать поддерживаемые theme/layout API установленной версии Streamlit; CSS ограничивать собственными классами, не привязываться к случайным generated class names. Пользовательские строки перед HTML interpolation экранировать; кнопки/inputs оставить доступными нативными controls, не заменять их нефункциональными HTML-картинками.

```css
:root {
  --wf-bg: #f4f7f8;
  --wf-surface: #ffffff;
  --wf-text: #142d35;
  --wf-muted: #526871;
  --wf-accent: #087f73;
  --wf-border: #d9e3e6;
}
.wf-card {
  background: var(--wf-surface);
  color: var(--wf-text);
  border: 1px solid var(--wf-border);
  border-radius: 12px;
  padding: 24px;
}
.wf-metric { font-variant-numeric: tabular-nums; font-size: 2rem; }
@media (max-width: 640px) {
  .wf-card { padding: 16px; }
}
```

- [ ] Написать тест `streamlit.testing.v1.AppTest` с temporary fixture artifacts/Application fake: приложение запускается без exception, показывает обе turbine_id и degraded label. UI до первого run показывает понятное пустое состояние. Tests не должны вызывать сеть.
- [ ] Запустить `.venv/bin/python -m pytest tests/test_dashboard.py -q` → FAIL; реализовать origin input с timezone, select 24/48, Run и Refresh. Disable повторного действия пока оно выполняется; backend lock остаётся источником concurrency correctness. Чтение run только через Application.

```python
def render(app):
    import streamlit as st
    st.title('Wind Power Forecast')
    origin = st.text_input('Forecast origin', '2026-02-01T00:00:00+05:00')
    horizon = st.selectbox('Horizon, hours', [24, 48], index=1)
    run_clicked = st.button('Run')
    refresh_clicked = st.button('Refresh')
    if run_clicked or refresh_clicked:
        with st.spinner('Running forecast'):
            result = app.run(parse_request(origin, horizon), refresh=refresh_clicked)
            st.session_state['run_id'] = result.run_id
```

Mode берётся из settings: demo ясно обозначен, не спрятан. Errors ловить на UI boundary, показывать reason code и путь events, не raw exception с credentials. В fixture smoke mode RunRequest получает mode demo.

Пример `render` выше фиксирует только wiring контролов, а не готовый дизайн. Финальный экран обязан соответствовать visual direction и browser acceptance ниже.
- [ ] Overview: state, weather provenance, model, origin, latest observation age, отдельно labeled backtest MAE или «нет фактических данных». Map — две координаты из spec. Forecast — per-turbine curves и normalized farm proxy; подпись uncertainty limitation §1. Forecast total без MW/MWh и без выдуманных capacity weights.
- [ ] Backtest: модели и metrics by turbine/lead, common coverage, evaluation period, failed folds; missing metrics отображать как unavailable. Agent Trace: ordered sequence/actions/reasons/durations; Data Quality: missing hours, observation counts, weather availability evidence и leakage status. CSV download отдаёт существующий файл.
- [ ] Привести графики к единому оформлению: p50 — хорошо различимая линия, p10–p90 — прозрачная область, деления времени с понятной timezone, числовая шкала мощности и tooltip с turbine/time/value. Цвет не является единственным различием: подписи/legend и при необходимости line style. Timeline агента показывает шаг, статус и длительность; таблицы — выравнивание чисел и короткие заголовки. Не добавлять декоративные изображения, которые отнимают место у прогноза.
- [ ] Оформить состояния empty/loading/success/degraded/error/reused: первая загрузка объясняет следующий шаг, выполнение показывает текущую стадию и блокирует повторное действие, ошибка предлагает доступное действие, unchanged Refresh сообщает об использовании той же версии. Warning provenance и отсутствие метрик видны, но не доминируют над всем экраном. Обеспечить видимый keyboard focus, подписи controls и отсутствие обязательной анимации.
- [ ] Tests: empty/latest failed/degraded run, missing February metrics, invalid naive origin не вызывает service, Refresh unchanged сохраняет run_id, changed weather version видна, две turbine curves. Не писать unit tests, которые просто повторяют CSS tokens. Помимо AppTest провести визуальную проверку настоящего браузера при 1440×900, 768×1024 и 390×844: отсутствие обрезанных controls/текста и общего горизонтального скролла, читаемость axes, рабочая navigation, доступность основных действий с клавиатуры. Сохранить screenshots для review в `artifacts/ui-review/`, пути указать в report. AppTest без браузерного осмотра не доказывает качество дизайна; если браузер недоступен, это незакрытый visual check.
- [ ] DEMO.md: prepare → archived weather evidence → 48h run → uncertainty → январский leaderboard → refresh unchanged/changed → manifest+trace. Для changed historical weather использовать заранее маркированную demo fixture, не обещать изменение неизменного архива по клику. Astra проверяет, что ограничения provenance и метрик заметны судье.

## T9. Приёмка, воспроизводимость и handoff

**Files:** Modify `README.md` (сохранить существующий hackflow раздел); create `requirements.lock.txt`, `tests/test_acceptance.py`; update reports/board только по фактическим результатам.

**Interfaces:** Документированные CLI команды spec, `streamlit run app.py`, disk schemas §3.

- [ ] Acceptance test на E2E artifact directory: точный CSV header, две турбины, оба horizons parameterized, 2H keys, UTC, finite/order/bounds, manifest/events schemas, hashes, metrics null semantics. Использовать fixture из T7, не создавать второй параллельный application implementation.

```python
import numpy as np

def assert_forecast(frame, horizon):
    assert set(frame.turbine_id) == {'turbine_1', 'turbine_2'}
    assert len(frame) == horizon * 2
    assert not frame.duplicated(['turbine_id', 'valid_time']).any()
    assert frame.groupby('turbine_id').lead_hours.apply(set).map(
        lambda values: values == set(range(horizon))).all()
    q = frame[['p10', 'p50', 'p90']].to_numpy()
    assert np.isfinite(q).all()
    assert ((0 <= q[:, 0]) & (q[:, 0] <= q[:, 1]) &
            (q[:, 1] <= q[:, 2]) & (q[:, 2] <= 1)).all()
```

- [ ] Запустить весь offline suite: `.venv/bin/python -m pytest -m 'not live' -q`; отдельно meaningful tiny CatBoost test обязателен, если dependency installed. Failures сначала диагностировать; не расширять scope feature work. Проверить `git diff --check`, `bash -n scripts/hackflow`, `git check-ignore .env api_keys.txt artifacts/runs/example/forecast.csv`.
- [ ] Сформировать lock из проверенного окружения с точными resolved versions, исключить editable/local absolute paths; записать файл через apply_patch. Проверить install lock в отдельном временном venv, затем package `--no-deps -e .` и offline smoke. Если чистая установка не воспроизведена, прямо указать ограничение README/report.
- [ ] README: Python version, venv/install, `.env.example`, API optionality, пути supplied CSV, все CLI команды, один dashboard command, offline fixture demo vs competition mode, время/lead conventions, metrics formula, splits, hardware, provenance evidence, ограничения February/no labels, four-hour cutline, секреты/ignore. Не удалять прежние пользовательские инструкции coordination.
- [ ] Реальные команды при готовом weather gate: prepare, train, backtest, run first February origin, simulate весь февраль, dashboard. Для live проверок фиксировать elapsed time, rows, model, coverage и artifacts; не объявлять успешной симуляцию с failed days. Если gate не пройден, выполнить demo/fixture path и явно отметить unmet competition DoD.
- [ ] Astra читает changes каждого worker и воспроизводит critical acceptance, особенно leakage tests и fallback. QA report read-only: severity, file/line, reproduction, proposed next step. Исправления возвращаются владельцу файлов; после них повторить затронутые tests и integration при изменении контракта.
- [ ] Final handoff: changed files, команды и результаты, известные failures, оставшиеся риски, demo paths, рекомендованный следующий шаг. Никаких коммитов без отдельного указания оркестратора.

## 4. Protocol параллельных терминалов и review gates

### Skills перед каждой задачей

По прямому указанию пользователя каждый исполнитель **до кода** определяет относящиеся к его задаче skills, читает их `SKILL.md` целиком и нужные references, затем применяет инструкции. Нельзя ограничиваться названием skill или полагаться на пересказ другого worker. В prompt каждой роли добавить это требование; в report перечислять фактически прочитанные skills и их влияние на решение.

- UI T8: доступный frontend/design skill, а для браузерной проверки — подходящий browser/testing skill. Если таких skills нет в каталоге, проверить локальные skill locations; не выдумывать их наличие и не устанавливать новые без необходимости. При отсутствии использовать конкретные design/acceptance требования T8 и отметить fallback в report.
- Data/weather/ML/agent/service T2–T7: доступные backend/Python/API/data skills, соответствующие конкретной задаче; OpenAI Docs для Responses integration; TDD для существенной логики. Общая задача Python не требует чтения всех skills подряд.
- Ошибки и неожиданные failures: systematic-debugging; review feedback: receiving-code-review; сдача задачи: verification-before-completion и уместный code review workflow.
- Оркестратор перед запуском волн читает skill выбранного execution workflow и parallel dispatch; назначение models и shared-worktree ограничения пользователя сохраняют приоритет.

В текущем каталоге сессии нет отдельно объявленного frontend-design/backend skill; перед реализацией проверить доступность заново. Отсутствие такого skill не отменяет требования к качеству UI и проверке backend.

Перед запуском исполнителей T1 подготавливает отдельные prompts для data/weather/ml/agent/ui/cli. Пример обязательного задания каждому: «Ты worker data, gpt-6-luna xhigh. Реализуй только T2 из указанного плана. Разрешены wind_forecast/data.py и tests/test_data.py. Прочитай AGENTS.md, brief, board, spec и T2. Не меняй другие файлы/ветки/commits. Перед изменениями git status. Report: .hackflow/reports/data.md; перечисли изменённые файлы, failing→passing checks, blockers и handoff. После завершения жди следующего назначения».

Запуск через проверенный launcher в GUI терминалах требует стандартного разрешения среды на открытие GUI. Оно запрашивается инструментом только при исполнении. Не запускать одновременно старый builder и нового owner тех же файлов. Не выдавать будущие T5/T7 за готовые зависимости: worker использует contract fakes, реальную integration принимает оркестратор после dependency gate.

Каждая задача заканчивается двумя проверками Astra: (1) соответствие spec/контрактам; (2) корректность, тесты, безопасность данных и отсутствие перезаписи чужих файлов. Статусы board: ready→active→review→done; blocker требует evidence и минимального решения. Если нужен дополнительный reviewer, отдельная Luna xhigh read-only сессия; окончательная приёмка остаётся у Astra.

При трёх coding workers больше не запускать дополнительные CPU-heavy процессы. Общие зависимости устанавливаются один раз T1, lock делает T9. Общие contracts меняет только orchestrator после сообщения всем зависимым владельцам.

## 5. Self-review и покрытие спецификации

| Требование spec | Реализация / проверка |
|---|---|
| CSV parsing, coverage, timezone | T1/T2; mixed hour format, counts, UTC tests |
| Weather by coordinates, cache, provenance | T3; live gate + future availability tests |
| Safe weather training, lag features | T4; poisoned future data invariance |
| CatBoost + three baselines + intervals | T5; metrics, fit/load, calibration tests |
| Rolling daily 48h, per-lead metrics | T5/T7; cutoff/common coverage/28-day tests |
| Full agent cycle, decisions, retry limits | T6; fake transport/LLM transition tests |
| Input updates and versioning | T6/T8; hash, repeated/concurrent refresh |
| CLI and dashboard same services | T7/T8; Application integration |
| Six run artifacts, schemas, audit trail | T6/T9; atomic write + acceptance |
| Reproducibility, README, offline fallback | T9; clean install/smoke and live status |
| Secret exclusion | T1/T6/T9; ignore + allowlist serialization |

Согласованность: forecast lead=0..H-1 во всех задачах; target_end=valid_time+1h; observation filter available_at<=origin; naming weather rows valid_time → CSV valid_time; model timestamps не predictors; request origin всегда UTC-aware. Review Focus имеет владельцев и конкретные тесты.

Известные незакрытые вопросы не скрываются в completion: доказательство оперативного происхождения раннего погодного архива; source timezone до изменения зоны; фактический доступ к model slug/100m variables; отсутствие February truth. Первые три проверяются на ранних gates, последнее — известное ограничение данных, отражённое в artifact/UI policy. План позволяет реализовать систему, но не обещает отсутствующие внешние данные.

Переход к реализации после review этого плана пользователем, как требует writing-plans. Способ исполнения уже выбран: Astra orchestrator + Luna xhigh coding workers в параллельных терминалах; повторно выбирать способ не требуется.
