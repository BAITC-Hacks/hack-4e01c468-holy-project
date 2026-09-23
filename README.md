# holy-project hackathon flow

The repository includes a small Codex swarm for hackathon work. The current/main session acts as
the orchestrator; three separate terminal tabs run focused workers:

| Worker | Responsibility | Product-code access |
|---|---|---|
| `product` | Problem framing, research, MVP backlog, risk reduction | Read-only by protocol |
| `builder` | One explicitly assigned vertical slice | May edit |
| `qa` | Acceptance checks, review, demo-path validation | Read-only by protocol |

All workers use `gpt-5.6-luna` with `xhigh` reasoning by default. They share the current worktree,
so only the builder edits product code; coordination happens through `.hackflow/BOARD.md` and role
reports under `.hackflow/reports/`.

## Start

1. Fill in `.hackflow/BRIEF.md` with the challenge, constraints, judging criteria, and demo goal.
2. Validate the environment:

   ```bash
   scripts/hackflow doctor
   ```

3. Start the worker tabs:

   ```bash
   scripts/hackflow start
   ```

4. Stay in the main Codex session as orchestrator. Assign one concrete task at a time by editing
   `.hackflow/BOARD.md`, then tell the relevant worker to pick it up.
5. Inspect the shared state at any time:

   ```bash
   scripts/hackflow status
   ```

## Orchestrator loop

Use this loop throughout the hackathon:

1. **Frame:** keep the brief and demo definition of done current.
2. **Assign:** give every active worker one bounded task with an acceptance check.
3. **Observe:** read reports and `git diff`; resolve blockers and ownership conflicts.
4. **Verify:** have QA reproduce the critical path before marking a task done.
5. **Integrate:** accept, revise, or reject the slice; update the board and choose the next bottleneck.

The launcher uses normal Codex approvals and the `workspace-write` sandbox. It intentionally does
not bypass safety prompts, commit code, switch branches, or kill terminal sessions.

To test another model or reasoning level temporarily:

```bash
HACKFLOW_MODEL=gpt-5.6-luna HACKFLOW_REASONING=xhigh scripts/hackflow start
```

## Wind forecast application

The forecast package requires Python 3.11 or later. The supplied turbine CSV files belong in
the repository root, or point `DATA_DIR` / `--data-dir` at the directory containing one file per
turbine. The source filenames may include the descriptive prefix, but must end with `turbine 1.csv`
and `turbine 2.csv` (or use `turbine_1.csv` and `turbine_2.csv`).

Create an isolated environment and install the pinned dependencies and local package:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
.venv/bin/python -m pip install --no-deps -e .
cp .env.example .env
```

`OPENAI_API_KEY` is optional. Without it, the agent uses a deterministic action selector. Offline
demo runs always use that selector even if a key is configured, so those runs never call OpenAI.
The checked in `.env.example` contains no credentials. Keep real keys in `.env` or the process
environment; `.env`, API key files, caches, models, and run artifacts are ignored by Git.

### Commands

Run commands from the repository root. Shared options such as `--mode`, `--offline`, and path
overrides work before or after a subcommand. `--mode` defaults to `RUN_MODE` from `.env`, which is
`competition` in the example configuration.

```bash
# Normalize the turbine CSVs into cached UTC hourly history.
.venv/bin/python -m wind_forecast.cli prepare

# Build a model from deterministic synthetic weather for a clearly marked demo.
.venv/bin/python -m wind_forecast.cli train --mode demo --offline

# Evaluate January daily 48-hour origins against the default 15-day calibration window.
.venv/bin/python -m wind_forecast.cli backtest --mode demo --offline

# Fast, one-fold demo backtest; production defaults are 250 CatBoost iterations.
.venv/bin/python -m wind_forecast.cli backtest --mode demo --offline \
  --start 2026-01-30 --end 2026-01-30 --iterations 30

# Create one 48-hour offline demo forecast. Use --horizon 24 for a 24-hour run.
.venv/bin/python -m wind_forecast.cli run --mode demo --offline \
  --origin 2026-02-01T00:00:00+05:00 --horizon 48

# Run all inclusive February origins, writing one indexed entry per day.
.venv/bin/python -m wind_forecast.cli simulate --mode demo --offline \
  --start 2026-02-01 --end 2026-02-28

# Run the checked-in, fixed-origin weather fixture (demo mode only).
.venv/bin/python -m wind_forecast.cli run --mode demo \
  --weather-fixture tests/fixtures/weather/synthetic_48h.json \
  --origin 2026-01-31T19:00:00Z --horizon 48

# The dashboard uses the same Application service as the CLI. This selects
# deterministic synthetic weather explicitly for a demo-only browser session.
RUN_MODE=demo DEMO_OFFLINE=1 .venv/bin/streamlit run app.py

# With DEMO_OFFLINE unset or 0, the dashboard uses the configured archive provider.
.venv/bin/streamlit run app.py
```

`RUN_MODE=demo` selects demo policy but does not select synthetic weather. The dashboard uses
synthetic weather only when `DEMO_OFFLINE=1` is set, and refuses that setting unless
`RUN_MODE=demo`. Leave `DEMO_OFFLINE` unset (or set it to `0`) to use the configured weather
provider. CLI offline runs continue to require the explicit `--offline --mode demo` options.

Add `--data-dir`, `--cache-dir`, `--model-dir`, or `--run-dir` after any subcommand to keep
prepared data and artifacts outside the repository defaults. `train` accepts `--train-start`,
`--train-end`, `--calibration-start`, `--calibration-end`, `--iterations`, and `--refresh`.
`backtest` accepts `--start`, `--end`, `--history-start`, `--iterations`, `--calibration-days`,
and `--refresh`. `run` and `simulate` accept `--refresh` to bypass the weather cache. The
simulation range is inclusive; each origin is midnight at fixed UTC+05:00 with a 48-hour horizon.

`--offline` generates deterministic synthetic weather for any origin and is allowed only with
`--mode demo`. `--weather-fixture` reads the fixture's recorded valid times and is also demo-only;
it cannot be shifted to another origin. Demo output has `competition_valid=false`. Competition
mode uses the Open-Meteo archive provider and rejects weather without verified point-in-time
release provenance. A download time or model initialization time does not prove when a forecast
was published. The current weather evidence does not establish verified historical release times,
so competition-valid forecasts and historical competition scores remain unavailable unless that
evidence is supplied. Backtests that use synthetic or unverified archive snapshots are explicitly
marked unavailable for accuracy comparison, even though demo predictions are still written for
workflow inspection.

### Time, evaluation, and limits

Naive timestamps in the source CSVs are interpreted using the working fixed UTC+05:00 policy,
then stored in UTC. This assumption is not independent confirmation of the source system's older
timezone history. Forecast origins must include a timezone and fall on an hour boundary. A
forecast row's `valid_time` is the start of its hour; `lead_hours` is zero-based, from `0` through
`horizon - 1`. Observations become available at the end of their hour and features only use rows
whose `available_at` is no later than the origin.

Default daily split: training origins 2025-10-01 through 2025-12-14; calibration origins
2025-12-15 through 2025-12-29; rolling evaluation origins 2026-01-01 through 2026-01-30. Training
labels must end by the first calibration origin. The rolling backtest selects and calibrates
baselines from data available before each fold and scores models on common observed target keys.
MAE is mean absolute error, RMSE is the square root of mean squared error, sMAPE is
`200 * abs(prediction - truth) / (abs(prediction) + abs(truth))` (zero when both values are zero),
bias is mean `prediction - truth`, and interval coverage is the share of observed values within
`p10` to `p90`. Missing labels remain null and do not count as zero.

The packaged dataset ends with January 2026 turbine observations. It contains no February truth,
so February forecasts and simulations do not produce February accuracy metrics or inferred
observations. Synthetic weather supports reproducible demos and plumbing checks only; it is not
weather evidence and its forecast scores must not be described as historical accuracy. CatBoost
runs on CPU with four threads by default. The four-hour hackathon scope prioritizes the point-in-
time gate, reproducible artifacts, and usable offline demo when weather provenance cannot pass.
