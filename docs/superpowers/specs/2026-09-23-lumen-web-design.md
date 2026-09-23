# Windline: real Lumen web interface

Status: written specification approved by user; implementation plan separately approved for parallel Luna xhigh execution.

## Intent and scope

Replace the rejected Streamlit visual surface with an actual Astro/Lumen product interface for the hackathon's two-turbine, hourly 24/48-hour forecasting workflow. Preserve the existing Python application and artifact contracts. Streamlit remains a working fallback, not the new frontend's rendering engine. User-selected implementation: Luna xhigh terminal workers; orchestrator reviews code and browser results. No commits, reference-file edits, destructive cleanup, public deployment, or secret exposure.

## Product design

Calm, high-contrast operations workspace: dark navigation rail, light content surface, restrained teal semantic brand tokens, readable typography and Lucide icons. Use the supplied windline-lumen-demo as visual reference only. No emoji navigation, fake statistics, oversized empty header, or native Streamlit controls in the new interface.

Overview prioritizes the hourly forecast chart and uncertainty band, compact operational facts, then a clearly labeled forecast form. Supporting provenance and data age stay visible without dominating the chart. Preserve five sections: overview, forecast/table/download, historical backtest, agent trace, data quality. Backtest is a view of existing historical evidence; expensive training/backtest execution remains CLI-only initially.

Use verified public Lumen components: application navigation, Stat, Card, Field/Input/NativeSelect, Button, Badge/Alert, Table, Skeleton/Empty, Icon, and Chart as appropriate. Read exact installed/MCP contracts before choosing props. Import Lumen styles once and mount one UIPrimitives runtime. Use semantic tokens and feature-oriented Astro modules, not a monolithic page.

Render loading, empty, validation, unavailable metrics, failed run, degraded run, successful run, and unchanged-refresh states. A failed newly requested run must remain selected; never silently display a previous success. Preserve user input after errors. Show returned run ID and reuse status. No fabricated February accuracy or power capacity conversion.

## Architecture and transport

New frontend/ owns Astro and its locked npm dependencies. New wind_forecast/presentation/api/ owns a thin FastAPI adapter and bounded local job execution. It calls service.Application and parse_request; it contains no training algorithms, weather policy, or alternate artifact implementation. The approved bootstrap refactor is independent and retains the facade. No other architecture migration batch is implicitly included.

API contract (all under /api):

- GET /health: readiness and configured mode/offline flags; no credentials or filesystem paths.
- GET /runs/latest: latest saved result and display payload, or an explicit empty result.
- GET /runs/{run_id}: selected persisted result: manifest, metrics, quality, events, forecast rows and report. Serialization uses explicit DTOs, UTC ISO timestamps, null instead of non-finite numbers, no local paths.
- POST /forecast-jobs: origin, horizon24/48, refresh boolean. Mode is server-configured and not client-overridable. Validate before acceptance. Return202 with job ID. One active job per local server; another submission returns409. Execute blocking Application.run off the event loop.
- GET /forecast-jobs/{job_id}: queued/running/completed/failed, safe error code, result/run ID and reused when available. Forecast run status is separate from transport-job status.
- GET /runs/{run_id}/forecast.csv: allowlisted forecast artifact only. Reject invalid identifiers/path traversal; missing forecast404. No arbitrary-file endpoint.

Local single-process jobs suffice; no Redis/Celery/database. On server restart jobs can be lost; completed runs remain persisted and retrievable. Bound retained completed-job metadata. Do not claim durable distributed execution. Disable duplicate UI submissions while a job is active; poll with bounded cadence and show recoverable connectivity errors.

Development frontend proxies /api to loopback backend; no wildcard CORS. Bind both servers to127.0.0.1. Check Origin/Host for mutation endpoints, accept JSON only, bound request fields, redact unexpected errors. No API key in frontend bundle or responses. No external deployment/authentication claim: a public multi-user deployment would require additional security design.

## Real-data and competition readiness

Normal operation uses actual input CSV and the configured weather provider; synthetic weather remains explicit opt-in demo only. The frontend accurately distinguishes operational execution from competition eligibility. Keep fail-closed provenance validation. Open-Meteo's early IFS archive is labeled hindcast and does not currently prove original release availability; changing RUN_MODE alone cannot resolve that requirement. Selecting a verified operational archive is a separate provider task after evidence review. No fabricated issue/availability timestamps.

## Verification and handoff

API tests cover validation, empty/latest/selected results, real serialization, failed run retention, reuse, job conflict/error handling, restart semantics, and path/origin restrictions. Use real application with tiny offline fixtures for integration, not only mocked transport.

Frontend build/typecheck must pass. Browser tests at1920/1440/768/390 cover all five sections, chart/table parity, keyboard focus, accessible labels, no horizontal overflow, loading/failure/empty states, actual forecast submission, and unchanged refresh. Orchestrator visually inspects screenshots; passing geometry alone is insufficient design acceptance. Full Python regression suite must pass after bootstrap and API integration. Leave working localhost links and record exact commands, limitations and reports.

## Self-review

The new interface/API and its implementation plan are explicitly approved. No unrelated backend rewrite, imagined metrics, public deployment, or silent competition bypass is included. One local worker and bounded in-memory job tracking match current deployment scope. User-provided Astro reference remains untouched.
