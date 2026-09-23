# Local forecast API

The FastAPI adapter exposes the existing `Application` facade on loopback. It
does not train models or implement a separate forecast pipeline. The Astro dev
server can proxy its `/api` requests to this service.

## Start

Install the project dependencies, then start the API from the repository root:

```bash
.venv/bin/python -m wind_forecast.presentation.api --host 127.0.0.1 --port 8000
```

Normal operation uses `RUN_MODE=competition` (the default), the configured
input CSV, and the configured weather provider. Synthetic weather is an
explicit demo option and requires both settings:

```bash
RUN_MODE=demo DEMO_OFFLINE=1 .venv/bin/python -m wind_forecast.presentation.api --host 127.0.0.1 --port 8000
```

`DEMO_OFFLINE` accepts only `0` or `1`. Offline mode requires `RUN_MODE=demo`.
The server reads settings and constructs one application when `create_app()` is
called; clients cannot change mode or offline behavior.

## Routes

All routes are under `/api`.

| Method and path | Result |
| --- | --- |
| `GET /health` | `{ "status": "ok", "mode": "demo" | "competition", "offline": boolean }` |
| `GET /runs/latest` | `{ "run": RunView | null }` for the latest persisted successful or degraded run |
| `GET /runs/{run_id}` | `{ "run": RunView }` for that persisted run |
| `POST /forecast-jobs` | `202 JobView` after strict validation and job admission |
| `GET /forecast-jobs/{job_id}` | Current in-process job state and result |
| `GET /runs/{run_id}/forecast.csv` | The run's forecast CSV attachment |

`RunView` contains `run_id`, `status`, `reused`, `manifest`, `metrics`,
`events`, `report`, and `forecast`. Its forecast rows use the existing artifact
column names and UTC ISO timestamps. Non-finite numeric values become JSON
`null`; local paths and secret-bearing fields are omitted.

Submit only the allowed request fields. The `origin` must be a zoned ISO-8601
hour boundary, `horizon` must be the integer `24` or `48`, and `refresh` must be
a boolean:

```bash
curl -sS -X POST http://127.0.0.1:8000/api/forecast-jobs \
  -H 'Content-Type: application/json' \
  --data '{"origin":"2026-02-01T00:00:00+05:00","horizon":24,"refresh":false}'
```

The returned `job_id` can be polled with `GET /api/forecast-jobs/{job_id}`.
Transport job states are `queued`, `running`, `completed`, and `failed`. A
completed job can contain a forecast run whose domain status is `failed`; the
job's `result` remains that exact run rather than falling back to a previous
latest result. Reused results are marked on the run view.

## Local execution limits

The process admits one active forecast at a time. A second submission returns
`409`; requests are validated and bounded before admission. Completed job
metadata is kept in memory up to 100 entries. Job state is local to one server
process and may be lost on restart; completed forecast artifacts remain
persisted and can be loaded again through the run routes.

Requests must use `127.0.0.1` or `localhost` with port `8000` or `4321` (or no
port in the `Host` header). Browser submissions must have an `http` Origin on
one of those hosts and ports. Non-browser clients may omit `Origin`. The server
does not enable wildcard CORS and the launcher binds to `127.0.0.1` by default.

## Errors

Errors use the same safe envelope and do not include exception text:

```json
{"error":{"code":"safe_code","message":"Safe user text."}}
```

Validation errors return `422`, oversized request bodies `413`, non-JSON
submissions `415`, disallowed origins `403`, invalid hosts `400`, missing runs
or jobs `404`, active-job conflicts `409`, and unexpected request failures
`500`. A forecast execution exception becomes a failed job with the safe code
`execution_error`.

The API is intended for local development. Keep it on loopback; public or
multi-user deployment needs additional security design.
