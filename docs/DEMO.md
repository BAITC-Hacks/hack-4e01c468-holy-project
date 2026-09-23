# Windline dashboard demo

This walkthrough shows the same `Application` workflow through the CLI and Streamlit. Use demo mode when the selected weather source is synthetic, a hindcast, or otherwise missing release-time evidence. Demo forecasts are useful for reviewing the workflow and chart, but they are not competition evidence.

## Prepare the local run

Create a virtual environment and install the project dependencies using the repository setup instructions. Keep credentials in `.env`; the deterministic demo does not require an OpenAI key. Start by preparing the supplied turbine data and, when the required point-in-time weather inputs are available, build the historical evaluation:

```bash
python -m wind_forecast.cli prepare
python -m wind_forecast.cli train
python -m wind_forecast.cli backtest
```

For a fixed-origin CLI preview, use the supplied `tests/fixtures/weather/synthetic_48h.json` fixture with demo mode. It was generated for origin `2026-02-01T00:00:00+05:00` and horizon 48:

```bash
python -m wind_forecast.cli --mode demo \
  --weather-fixture tests/fixtures/weather/synthetic_48h.json \
  run --origin 2026-02-01T00:00:00+05:00 --horizon 48
```

To launch the interactive dashboard with deterministic synthetic weather, opt in explicitly:

```bash
RUN_MODE=demo DEMO_OFFLINE=1 streamlit run app.py
```

`DEMO_OFFLINE=1` is accepted only when `RUN_MODE=demo`. Setting `RUN_MODE=demo` alone selects demo policy but does not switch the dashboard to synthetic weather; with `DEMO_OFFLINE` unset (or `0`), it uses the configured archive provider. Competition mode refuses the offline setting. The dashboard's default origin is `2026-02-01T00:00:00+05:00` with a 48-hour horizon. The checked-in fixed-origin fixture is for CLI use; it is not silently selected by the dashboard.

## Tell the evidence story

1. **Show data readiness.** Select `Data Quality` and point out source coverage, observation age, and the leakage check. The forecast uses UTC at its artifact boundary; the default origin is displayed in Almaty time (`UTC+05:00`).
2. **Show weather provenance.** In `Overview` or `Data Quality`, read `provenance_status`, `issued_at`, `available_at`, and `competition_valid` from the run. The checked-in weather fixture is `synthetic`; its issue and availability times are intentionally null, and `competition_valid` is false. A retrieved archive response is not proof that it had been released at the historical origin.
3. **Run 48 hours.** Use `2026-02-01T00:00:00+05:00`, select 48 hours, and choose `Run forecast`. The result shows a separate p50 curve and p10–p90 band for each turbine, plus an equal-mean normalized farm proxy. The proxy is not MW/MWh, and averaging each turbine's marginal quantiles does not make a calibrated interval for the farm.
4. **Show the January backtest only when supported.** `Backtest` reports metrics by model, turbine, and lead from the persisted evaluation artifact. Only present its leaderboard as point-in-time evidence when the artifact has common target coverage and the January weather provenance is verified. If those checks fail or metrics are absent, show the unavailable state; do not substitute February scores because February turbine labels were not supplied.
5. **Show the agent audit.** Open `Agent Trace` and review ordered actions, reasons, outcomes, and durations. Then show that the run directory contains the forecast, metrics, manifest, weather snapshot, report, and `events.jsonl` artifacts.

## Refresh behavior

Use `Refresh weather` with unchanged inputs to show the reuse state: the current `run_id` stays the same and the dashboard says the forecast version is unchanged. To demonstrate a changed input, prepare a second, explicitly synthetic demo fixture before the run and configure the demo weather provider to serve that fixture for the next refresh. The changed fingerprint should produce a new run version and retain the first as its parent. Keep both fixtures labeled synthetic and `competition_valid=false`.

The checked-in fixture is a fixed file; the Refresh button does not mutate historical weather. Do not describe refreshing a historical archive as if it changes the weather that was available in the past. If the demo provider cannot switch fixtures during the session, demonstrate the changed-fingerprint path with the service's offline fixture test and use the dashboard for the unchanged refresh.

## What the demo does not claim

- The Open-Meteo archive's initialization timestamp alone does not prove issue or public availability time.
- Synthetic and hindcast weather are demo-only unless per-run release provenance is independently verified.
- There are no February turbine labels, so February forecast accuracy is unavailable.
- A successful UI render or plausible-looking chart is not a substitute for provenance, coverage, or backtest checks.
