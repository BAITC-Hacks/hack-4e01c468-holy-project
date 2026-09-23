# Agentic AI for Wind Power Forecasting — Design Specification

**Status:** Complete; all five design sections were approved in chat and are ready for final written review.

**Date:** 2026-09-23

**Source:** `HackAlem AI_ Agentic AI для прогнозирования выработки ВЭС - Google Документы.pdf`

## Intent and Constraints

Build a competition-ready system that forecasts hourly normalized wind-farm output for the next 24–48 hours and autonomously repeats the entire workflow when inputs change.

The system must:

1. Train a forecasting model from the supplied turbine history.
2. Obtain weather forecasts from open sources by turbine coordinates.
3. Use only forecasts that were available at each historical forecast origin.
4. Produce an hourly 24–48 hour forecast.
5. Execute the agentic cycle: fetch, prepare, model, forecast, assess, and rerun.
6. Provide a reproducible CLI, dashboard, output artifacts, and README.

Time available for implementation is four hours. The repository currently contains no product implementation or installed Python ML dependencies. The local machine has 12 CPU cores, 15 GiB RAM, and no local GPU. NVIDIA access is inference-only and is excluded from the MVP.

Secrets must be loaded from `.env`. Both `.env` and the existing `api_keys.txt` must be ignored by Git and must never be copied into logs or artifacts.

## Challenge Evidence

- Turbine 1 coordinates: `43.645150, 78.535604`.
- Turbine 2 coordinates: `43.643198, 78.538828`.
- Training data ends at `2026-01-31 23:50:00` despite filenames mentioning February.
- The test-validity period is February 1–28, 2026. The initial run is formed after the final January 31 observation and represented as origin `2026-02-01T00:00:00+05:00`; subsequent origins advance daily.
- The challenge document defines judging criteria but no submission CSV schema or automatic accuracy metric.

Judging weights:

- task fit and working behavior: 25 points;
- technical implementation: 25 points;
- README and reproducibility: 25 points;
- value and applicability: 15 points;
- development potential and originality: 10 points.

## Selected Approach

Use a hybrid CatBoost forecasting pipeline with persistence and empirical power-curve baselines. A small OpenAI model orchestrates the bounded agent workflow and explains already-computed diagnostics; deterministic Python code owns data validation, training, forecasting, quality gates, and persistence.

Rejected alternatives:

- A power-curve-only model is explainable but cannot capture turbine-specific residual structure well enough.
- LSTM, TFT, or another deep sequence model is too risky under the four-hour deadline because there is no training GPU and the dataset is small enough for gradient boosting.
- NVIDIA NIM is excluded from the MVP because the available NVIDIA access currently supports inference but is unavailable through the required interface.

## Section 1 — Architecture

One Python project exposes the same application logic through a CLI and Streamlit dashboard:

```text
Turbine CSV ──> validation and hourly aggregation ──┐
                                                   ├──> features ──> CatBoost ──> 24–48 h forecast
Point-in-time weather forecast ──> local cache ────┘                         │
                                                                              v
OpenAI LLM <── agent orchestrator <── quality gate <── result assessment
                          │
                          └──> CSV + metrics + run manifest + dashboard
```

### Components

- `DataPipeline` loads both CSV files, validates missing values, duplicates, ranges, and temporal cadence, then aggregates 10-minute measurements to hourly rows.
- `WeatherProvider` retrieves Open-Meteo forecasts that existed at the requested forecast origin and persists raw responses with `issued_at`, `valid_at`, and `lead_hours` provenance.
- `FeaturePipeline` creates point-in-time-safe weather, calendar, turbine, and lag features.
- `ForecastModel` trains CatBoost models and evaluates persistence, seasonal, and empirical power-curve baselines.
- `ForecastAgent` executes `fetch -> validate -> prepare -> train/load -> predict -> assess -> persist` and starts a new run when the weather input fingerprint changes.
- The Streamlit dashboard displays turbine locations, hourly forecasts, model comparisons, backtest quality, uncertainty, and the agent event log.
- Each run persists `forecast.csv`, `metrics.json`, `manifest.json`, the weather snapshot, and a generated report.

### LLM Boundary

Use the OpenAI Responses API with:

```env
OPENAI_MODEL=gpt-6-luna
OPENAI_REASONING_EFFORT=none
```

`gpt-6-luna` is sufficient because the model receives compact, precomputed diagnostics and selects from a small set of defined actions. It may explain anomalies and recommend a permitted rerun, but it may not calculate forecasts, alter metrics, execute arbitrary code, or bypass quality gates. The deterministic workflow remains functional when the API is unavailable.

## Section 2 — Data and ML Design

### Observed Dataset Properties

| Property | Turbine 1 | Turbine 2 |
|---|---:|---:|
| Rows | 142,360 | 149,499 |
| Unique timestamps | 142,360 | 149,499 |
| Start | 2023-03-11 00:00 | 2023-03-11 00:00 |
| End | 2026-01-31 23:50 | 2026-01-31 23:50 |
| Duplicate timestamps | 0 | 0 |
| Empty values | 0 | 0 |
| Wind-speed range, m/s | 0.00–22.97 | 0.11–21.43 |
| Normalized-power range | 0.00–1.00 | 0.00–1.00 |
| Temperature range, °C | -19.26–43.48 | -19.08–43.97 |

The dominant cadence is 10 minutes, with irregular gaps from 20 minutes to several hours. Timestamps use non-zero-padded hours in some rows and must be parsed with an explicit format rather than strict ISO parsing.

### Preparation and Target

- Interpret naive source timestamps as `Asia/Almaty` (`UTC+05:00`) and persist UTC timestamps at all API and artifact boundaries.
- Aggregate measurements to hourly resolution when at least four of the expected six 10-minute observations are present.
- Persist the observation count and a quality flag for every hourly row.
- Predict mean normalized active power for each hour.
- Clip persisted point and quantile predictions to `[0, 1]` after validation.

### Weather Features

Retrieve forecast wind speed at 10 m and 100 m, wind direction, gusts, temperature, and pressure. Every weather record must include the forecast model and the `issued_at`, `valid_at`, and `lead_hours` fields.

Prefer Open-Meteo Single Runs data for exact historical forecast origins. Use Previous Runs only as an explicitly recorded fallback that cannot introduce future data. Never substitute reanalysis or observed weather for an archived forecast without marking the run invalid for competition evidence.

Train weather-aware CatBoost rows only where point-in-time archived forecasts are available. Older turbine observations remain valid for turbine-only baselines, the empirical power curve, seasonal diagnostics, and lag-state initialization; they must not be joined to later reanalysis and presented as historical forecasts.

### Point-in-Time-Safe Features

- forecast weather variables;
- forecast horizon in hours;
- hour, weekday, and month with cyclic encodings;
- last available turbine power and wind speed at the forecast origin;
- rolling mean and standard deviation over 6, 24, and 168 observed hours;
- same-hour values from one day and one week earlier;
- turbine identifier or turbine-specific model identity.

No feature may use an observation whose timestamp is later than the forecast origin.

### Models and Uncertainty

- Train CatBoost quantile models for `p10`, `p50`, and `p90`.
- Use `p50` as the primary hourly forecast.
- Train and score persistence, seasonal `t-24`, and empirical power-curve baselines.
- Keep the model strategy CPU-friendly and reproducible; deep-learning models are outside the MVP scope.

### Evaluation

Use rolling-origin backtesting that simulates daily 48-hour runs. Report MAE, RMSE, sMAPE, bias, and `p10–p90` interval coverage, separately by turbine and forecast horizon. The final simulation creates a new 48-hour run at `00:00 Asia/Almaty` for every evaluation day from February 1 through February 28, 2026; the first run uses observations through January 31 at 23:50.

## Section 3 — Agentic Workflow

The agent is bounded and auditable. It may invoke only named application tools and may choose only transitions allowed by the deterministic orchestrator.

### Run States

1. `fetch_weather` retrieves a point-in-time forecast or inspects the cache.
2. `validate_inputs` checks temporal provenance, coverage, and leakage constraints.
3. `prepare_features` builds features from information available at the forecast origin.
4. `train_or_load_model` retrains after a training-data fingerprint change or loads the matching artifact.
5. `generate_forecast` creates `p10`, `p50`, and `p90` predictions for 48 hours.
6. `quality_gate` validates coverage, numeric bounds, quantile order, and weather provenance.
7. `analyze_result` sends compact computed diagnostics to `gpt-6-luna`.
8. `persist_run` writes the forecast, manifest, diagnostics, and event log.
9. `check_for_updates` compares weather-snapshot hashes and creates a new forecast version only when inputs change.

### Permitted LLM Decisions

OpenAI function calling may return only one of these actions:

```text
continue | retry_weather | use_cached_weather | use_baseline | abort | finalize
```

The Python orchestrator validates every requested transition before execution. The model cannot execute arbitrary code, modify a prediction or metric, write outside the run directory, or bypass a failed quality gate.

### Limits and Degraded Modes

- Allow at most two LLM calls per forecast run.
- Allow at most three weather-request attempts.
- If OpenAI is unavailable, complete the workflow with a deterministic analysis report.
- If CatBoost is unavailable or prediction fails, use the best validated baseline and mark the run `degraded`.
- Use cached weather only when its `issued_at` is not later than the forecast origin.
- Record each action, reason, timestamp, duration, and outcome in `events.jsonl`.

### Recalculation

The historical simulation starts a fresh agent cycle for every daily forecast origin in February. In the dashboard, Refresh fetches weather, computes a content fingerprint, and starts a new forecast only when that fingerprint differs from the previous successful run.

## Section 4 — Interface, Artifacts, and Demo

Streamlit is a thin interface over the same application services invoked by the CLI. No database or background service is required for the MVP; inspectable files are the system of record.

### Dashboard

User refinement (2026-09-23): the web interface must have a polished, custom visual design; the default unstyled Streamlit appearance is not an acceptable final deliverable. Keep Streamlit as the application runtime, with a consistent theme, responsive composition, readable typography, styled charts, and complete loading, empty, error, and degraded states. Verify the rendered interface in a browser at desktop, tablet, and mobile widths. Read applicable frontend/backend skills before implementing each task, and record unavailable skills and fallbacks honestly.

- **Overview:** latest run state, weather source, model, MAE, and forecast total.
- **Forecast:** `p10`, `p50`, and `p90` curves for each turbine and their aggregate.
- **Backtest:** CatBoost versus persistence, seasonal, and power-curve baselines, including metrics by lead time.
- **Agent Trace:** ordered workflow steps, LLM decisions, retries, durations, and degraded modes.
- **Data Quality:** missing intervals, source coverage, weather coverage, and point-in-time leakage checks.
- **Controls:** forecast origin, 24/48-hour horizon, Run, and Refresh.

### CLI Contract

```bash
python -m wind_forecast.cli prepare
python -m wind_forecast.cli train
python -m wind_forecast.cli backtest
python -m wind_forecast.cli run --origin 2026-02-01T00:00:00+05:00 --horizon 48
python -m wind_forecast.cli simulate --start 2026-02-01 --end 2026-02-28
streamlit run app.py
```

### Forecast Schema

`forecast.csv` contains:

```text
run_id
forecast_origin
turbine_id
valid_time
lead_hours
p10
p50
p90
weather_issued_at
weather_model
run_status
```

Every run directory also contains:

- `metrics.json` with model and baseline metrics;
- `manifest.json` with data versions, model parameters, hashes, and provenance;
- `events.jsonl` with the agent trace;
- `weather.json` with the unmodified response snapshot;
- `report.md` with the concise LLM or deterministic explanation.

### Demo Story

1. Show the historical data and time-based split.
2. Start the initial agent run at `2026-02-01T00:00:00+05:00`, using observations through the end of January 31.
3. Show the archived weather forecast and its `issued_at` evidence.
4. Generate a 48-hour forecast with uncertainty bounds.
5. Compare CatBoost against three baselines.
6. Refresh the input and create a new version only when its fingerprint changes.
7. Open the trace and manifest to demonstrate autonomy and reproducibility.

## Section 5 — Verification, Completion, and Timebox

### Required Tests

- Parse both `0:00:00` and `00:00:00` source timestamp forms.
- Aggregate incomplete hours and assign the correct coverage flag.
- Reject any observation feature later than `forecast_origin`.
- Reject weather with `weather_issued_at > forecast_origin`.
- Require 24 or 48 hourly predictions for both turbines.
- Enforce `0 <= p10 <= p50 <= p90 <= 1`.
- Start a recalculation only when the weather fingerprint changes.
- Complete a run without OpenAI using deterministic analysis.
- Fall back to a validated baseline when CatBoost fails.
- Validate the forecast CSV, manifest, and agent event schemas.
- Exercise one end-to-end flow from turbine CSV and recorded weather fixture to run artifacts.

### Definition of Done

- One documented command starts the dashboard.
- The CLI reproduces a forecast for an explicitly zoned historical origin.
- The February simulation uses no observation or weather information from the future.
- CatBoost is compared with persistence, seasonal, and power-curve baselines.
- Both turbines and every requested lead hour appear in the output.
- The agent executes the full cycle and leaves an audit trail.
- Forecasting still works when the OpenAI API is unavailable.
- The README lets a judge reproduce the demo path.
- `.env`, `api_keys.txt`, trained models, caches, and generated run directories are ignored by Git.

### Four-Hour Implementation Timebox

1. **0:00–0:20:** protect secrets, define dependencies and project structure, and run a smoke test.
2. **0:20–1:10:** implement the CSV pipeline, Open-Meteo client, cache, and provenance.
3. **1:10–2:05:** implement features, baselines, CatBoost, and rolling backtest.
4. **2:05–2:45:** implement the bounded agent workflow, OpenAI analysis, and run artifacts.
5. **2:45–3:25:** implement the Streamlit dashboard.
6. **3:25–4:00:** run tests, finish the README, and rehearse the complete demo.

### Scope Cutline

If exact Single Runs retrieval threatens the critical path, use Previous Runs with explicit provenance and document the limitation. If three quantile models threaten the critical path, retain `p50` and derive a validation-calibrated empirical interval. Do not cut the dashboard, README, point-in-time checks, deterministic fallback, or audit artifacts because they directly support the judging criteria.

## Approved Decisions Log

- Build the competition solution, ML evaluation, agent workflow, and dashboard together as one vertical product.
- Use Open-Meteo archived forecasts and retain point-in-time provenance.
- Use CatBoost plus interpretable baselines instead of deep sequence models.
- Use OpenAI only for the MVP agent layer.
- Use `gpt-6-luna` with no reasoning effort as the minimum sufficient LLM.
- Keep all numeric decisions and quality gates deterministic.
- Restrict the LLM to validated workflow transitions and two calls per run.
- Rerun forecasts only when the point-in-time weather snapshot changes.
- Use one application layer for the CLI and Streamlit dashboard.
- Store forecasts, metrics, provenance, snapshots, and agent events as inspectable run artifacts.
- Treat source timestamps as Almaty time and expose timezone-aware origins in every public interface.
- Require at least four 10-minute measurements for a valid hourly training target.
- Preserve the dashboard, README, provenance, and audit trail at the four-hour scope cutline.
