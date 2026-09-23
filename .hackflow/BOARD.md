# Orchestrator board

Only the orchestrator should change task ownership and priority here. Workers may update their
role report with progress and blockers.

| Priority | Task | Owner | Status | Acceptance check |
|---|---|---|---|---|
| P0 | T1 contracts/config/project | foundation | done | 8 tests passed, root reviewed contracts/config, ignores checked |
| P0 | T2 CSV/hourly pipeline | data | done | Validation fixes accepted;50 combined core tests;real CSV prepared |
| P0 | T3 weather/provenance/cache | weather | done | Validation fixes accepted;live96rows;competition provenance unverified |
| P0 | T4 features, T5 models/backtest | ml | done | ML fix reviewed;66 combined module tests;actual default demo train completed |
| P0 | T6 agent/LLM/artifacts | agent | done | Integrity+reuseaudit fixes reviewed;root34focusedtests;immutable artifacts and complete invocation trace |
| P0 | T7 service/CLI/API | orchestrator | done | Local API reviewed, NOAA competition baseline96rows and refresh/CSV passed |
| P0 | T8 real Astro/Lumen frontend | orchestrator | done | Reference composition, RU/KK, light/dark; browser8cases + actual NOAA E2E passed |
| P0 | T9 acceptance/docs | orchestrator | active | Russian README + validationflow done;246Python tests; full real GFS ML/backtest/month remains |
| P0 | T11 NOAA weather/source wiring | orchestrator | done | Full48h realweather verified + applicationbaseline; sourceisolation reviewed |
| P0 | T12 GFS ML calibration/backtest/month | orchestrator | active | User requested CatBoost; bounded17-origin NOAA download/training session7625, Jan12–25 train/Jan26–28 calibration; review delegated. Backtest/month remain separate |
| P0 | T10 incremental architecture redesign | orchestrator | done | Batch1 bootstrap/ports and stable identity fix accepted by Astra; further stages retained for gradual work |

Allowed statuses: `ready`, `active`, `blocked`, `review`, `done`.
