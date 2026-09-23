from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import numpy as np
import pandas as pd
import pytest

from wind_forecast.contracts import RunRequest, RunResult
RUN_ID = "run-20260201T000000Z-h24-0123456789ab"
ORIGIN = "2026-02-01T00:00:00+05:00"


def _create_app(application: Any):
    from wind_forecast.presentation.api import create_app

    return create_app(application)


def _run_data(tmp_path: Path, *, status: str = "degraded") -> dict[str, Any]:
    return {
        "manifest": {
            "run_id": RUN_ID,
            "status": status,
            "forecast_origin": pd.Timestamp(ORIGIN),
            "local_path": str(tmp_path / "must-not-leak"),
            "openai_api_key": "do-not-leak-this-secret",
            "openai_key": "do-not-leak-this-alternate-key",
        },
        "metrics": {"mae": np.nan, "sample_count": 0},
        "events": [{"timestamp": pd.Timestamp(ORIGIN), "score": np.inf}],
        "report": "Forecast report /tmp/private-report sk-proj-test_abcdefghijklmnopqrstuvwxyz",
        "forecast": pd.DataFrame(
            {
                "run_id": [RUN_ID],
                "forecast_origin": [pd.Timestamp(ORIGIN)],
                "turbine_id": ["turbine_1"],
                "valid_time": [pd.Timestamp("2026-02-01T01:00:00+05:00")],
                "lead_hours": [1],
                "p10": [0.1],
                "p50": [np.nan],
                "p90": [0.9],
            }
        ),
    }


class StubApplication:
    def __init__(self, run_dir: Path, data: dict[str, Any] | None = None) -> None:
        self.settings = SimpleNamespace(mode="demo", run_dir=run_dir)
        self.offline = True
        self.data = data or _run_data(run_dir)
        self.results: dict[str, RunResult] = {}
        self.latest_result: RunResult | None = None
        self.calls: list[tuple[RunRequest, bool]] = []
        self.run_callback = None

    def run(self, request: RunRequest, refresh: bool = False) -> RunResult:
        self.calls.append((request, refresh))
        if self.run_callback:
            return self.run_callback(request, refresh)
        result = RunResult(RUN_ID, self.settings.run_dir / RUN_ID, "degraded", refresh)
        self.results[RUN_ID] = result
        self.latest_result = result
        return result

    def latest(self) -> RunResult | None:
        return self.latest_result

    def read_run(self, run_id: str) -> dict[str, Any]:
        if run_id not in self.results and run_id != RUN_ID:
            raise FileNotFoundError("missing")
        return self.data


class ApiTestClient:
    """Synchronous test facade over httpx's in-process ASGI transport."""

    def __init__(self, app: Any) -> None:
        self.app = app

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback
        self.app.state.job_runner.shutdown(wait=True)

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        async def send():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://127.0.0.1:8000"
            ) as client:
                return await client.request(method, url, **kwargs)

        return asyncio.run(send())

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)


def _client(app: Any) -> ApiTestClient:
    return ApiTestClient(app)


def _submit(client: ApiTestClient, **overrides: Any):
    body = {"origin": ORIGIN, "horizon": 24, "refresh": False}
    body.update(overrides)
    return client.post("/api/forecast-jobs", json=body)


def test_api_factory_is_importable() -> None:
    from wind_forecast.presentation.api import create_app

    assert callable(create_app)


def test_health_and_empty_latest_expose_only_public_runtime_state(tmp_path: Path) -> None:
    client = _client(_create_app(StubApplication(tmp_path)))

    assert client.get("/api/health").json() == {
        "status": "ok",
        "mode": "demo",
        "offline": True,
    }
    assert client.get("/api/runs/latest").json() == {"run": None}


@pytest.mark.parametrize(
    "body",
    [
        {"origin": ORIGIN, "horizon": 72, "refresh": False},
        {"origin": ORIGIN, "horizon": True, "refresh": False},
        {"origin": ORIGIN, "horizon": "24", "refresh": False},
        {"origin": "2026-02-01T00:00:00", "horizon": 24, "refresh": False},
        {"origin": "2026-02-01T00:30:00+05:00", "horizon": 24, "refresh": False},
        {"origin": "not-a-date", "horizon": 24, "refresh": False},
        {"origin": ORIGIN, "horizon": 24, "refresh": False, "mode": "demo"},
        {"origin": ORIGIN, "horizon": 24, "refresh": False, "unexpected": "value"},
    ],
)
def test_invalid_job_input_is_rejected_before_application_execution(
    tmp_path: Path, body: dict[str, Any]
) -> None:
    application = StubApplication(tmp_path)

    response = _client(_create_app(application)).post("/api/forecast-jobs", json=body)

    assert response.status_code == 422
    assert response.json() == {
        "error": {"code": "invalid_request", "message": "The request is invalid."}
    }
    assert application.calls == []


def test_valid_job_uses_server_mode_and_serializes_allowlisted_run_view(tmp_path: Path) -> None:
    application = StubApplication(tmp_path)
    app = _create_app(application)
    client = _client(app)

    with client:
        accepted = _submit(client)
        assert accepted.status_code == 202
        job_id = accepted.json()["job_id"]
        app.state.job_runner.shutdown(wait=True)
        result = client.get(f"/api/forecast-jobs/{job_id}").json()
        latest = client.get("/api/runs/latest").json()

    run = result["result"]
    assert application.calls == [(RunRequest(pd.Timestamp("2026-01-31T19:00:00Z").to_pydatetime(), 24, "demo"), False)]
    assert set(run) == {"run_id", "status", "reused", "manifest", "metrics", "events", "report", "forecast"}
    assert run["status"] == "degraded"
    assert run["manifest"]["status"] == "degraded"
    assert run["manifest"]["forecast_origin"] == "2026-01-31T19:00:00Z"
    assert run["metrics"]["mae"] is None
    assert run["events"][0]["score"] is None
    assert run["forecast"][0]["valid_time"] == "2026-01-31T20:00:00Z"
    assert run["forecast"][0]["p50"] is None
    assert "must-not-leak" not in accepted.text + str(latest) + str(run)
    assert "do-not-leak-this-secret" not in accepted.text + str(latest) + str(run)
    assert "do-not-leak-this-alternate-key" not in str(run)
    assert "sk-proj-test_abcdefghijklmnopqrstuvwxyz" not in str(run)
    assert latest["run"]["run_id"] == RUN_ID


def test_failed_domain_run_stays_selected_instead_of_falling_back_to_latest(
    tmp_path: Path,
) -> None:
    application = StubApplication(tmp_path)
    old_id = "run-20260131T000000Z-h24-abcdef012345"
    application.results[old_id] = RunResult(old_id, tmp_path / old_id, "success", False)
    application.latest_result = application.results[old_id]
    failed_data = _run_data(tmp_path, status="failed")
    failed_id = "run-20260201T000000Z-h24-abcdef012345"
    application.data = {**failed_data, "manifest": {**failed_data["manifest"], "run_id": failed_id}}

    def failed_run(request: RunRequest, refresh: bool) -> RunResult:
        del request, refresh
        failed = RunResult(failed_id, tmp_path / failed_id, "failed", False)
        application.results[failed_id] = failed
        return failed

    application.run_callback = failed_run
    app = _create_app(application)
    client = _client(app)

    with client:
        job_id = _submit(client).json()["job_id"]
        app.state.job_runner.shutdown(wait=True)
        job = client.get(f"/api/forecast-jobs/{job_id}").json()
        selected = client.get(f"/api/runs/{failed_id}").json()["run"]

    assert job["state"] == "completed"
    assert job["result"]["run_id"] == failed_id
    assert job["result"]["status"] == "failed"
    assert selected["run_id"] == failed_id
    assert selected["status"] == "failed"
    assert selected["manifest"]["status"] == "failed"


def test_only_one_job_runs_and_parallel_submission_gets_conflict(tmp_path: Path) -> None:
    application = StubApplication(tmp_path)
    started = threading.Event()
    release = threading.Event()

    def blocked_run(request: RunRequest, refresh: bool) -> RunResult:
        started.set()
        assert release.wait(3), "test did not release the blocked forecast"
        return RunResult(RUN_ID, tmp_path / RUN_ID, "degraded", refresh)

    application.run_callback = blocked_run
    app = _create_app(application)
    client = _client(app)

    with client:
        first = _submit(client)
        assert first.status_code == 202
        assert started.wait(3), "first job never began"
        assert client.get(f"/api/forecast-jobs/{first.json()['job_id']}").json()["state"] == "running"
        second = _submit(client)
        assert second.status_code == 409
        assert second.json() == {
            "error": {"code": "job_already_running", "message": "A forecast is already running."}
        }
        release.set()

    completed = client.get(f"/api/forecast-jobs/{first.json()['job_id']}").json()
    assert completed["state"] == "completed"
    assert len(application.calls) == 1


def test_job_exception_is_redacted_and_missing_job_is_not_found(tmp_path: Path) -> None:
    application = StubApplication(tmp_path)

    def explode(request: RunRequest, refresh: bool) -> RunResult:
        del request, refresh
        raise RuntimeError("secret=/home/private/key.txt")

    application.run_callback = explode
    app = _create_app(application)
    client = _client(app)

    with client:
        job_id = _submit(client).json()["job_id"]
        app.state.job_runner.shutdown(wait=True)
        failed = client.get(f"/api/forecast-jobs/{job_id}")

    assert failed.status_code == 200
    assert failed.json()["state"] == "failed"
    assert failed.json()["error"] == {
        "code": "execution_error",
        "message": "The forecast could not be completed.",
    }
    assert "/home/private" not in failed.text and "key.txt" not in failed.text
    assert client.get("/api/forecast-jobs/not-a-job").status_code == 404


def test_host_origin_and_content_type_are_restricted(tmp_path: Path) -> None:
    application = StubApplication(tmp_path)
    client = _client(_create_app(application))

    with client:
        accepted = client.post(
            "/api/forecast-jobs",
            json={"origin": ORIGIN, "horizon": 24, "refresh": False},
            headers={"origin": "http://localhost:4321"},
        )
        hostile_origin = client.post(
            "/api/forecast-jobs",
            json={"origin": ORIGIN, "horizon": 24, "refresh": False},
            headers={"origin": "https://example.com"},
        )
        wrong_content_type = client.post(
            "/api/forecast-jobs",
            content='{"origin":"2026-02-01T00:00:00+05:00","horizon":24,"refresh":false}',
            headers={"content-type": "text/plain"},
        )
        invalid_host = client.get("/api/health", headers={"host": "attacker.example"})

    assert accepted.status_code == 202
    assert hostile_origin.status_code == 403
    assert wrong_content_type.status_code == 415
    assert invalid_host.status_code == 400
    assert len(application.calls) <= 1


def test_oversized_forecast_request_is_rejected_before_application_execution(
    tmp_path: Path,
) -> None:
    application = StubApplication(tmp_path)
    client = _client(_create_app(application))
    body = '{"origin":"' + ("x" * 3000) + '","horizon":24,"refresh":false}'

    response = client.post(
        "/api/forecast-jobs",
        content=body,
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json() == {
        "error": {"code": "request_too_large", "message": "The request is too large."}
    }
    assert application.calls == []


@pytest.mark.parametrize("run_id", ["../outside", "%2e%2e%2foutside", "run-abc/secret"])
def test_invalid_run_identifier_is_rejected(tmp_path: Path, run_id: str) -> None:
    application = StubApplication(tmp_path)

    response = _client(_create_app(application)).get(f"/api/runs/{run_id}")

    assert response.status_code == 404
    assert application.calls == []


def test_missing_run_is_not_found(tmp_path: Path) -> None:
    client = _client(_create_app(StubApplication(tmp_path)))

    response = client.get("/api/runs/run-20260201T000000Z-h24-abcdef012345")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "run_not_found"


def test_forecast_download_serves_only_contained_regular_artifact(tmp_path: Path) -> None:
    application = StubApplication(tmp_path)
    run_dir = tmp_path / RUN_ID
    run_dir.mkdir()
    forecast_path = run_dir / "forecast.csv"
    forecast_path.write_text("run_id,turbine_id\nrun-1,turbine_1\n", encoding="utf-8")
    (run_dir / "manifest.json").write_text(
        '{"run_id":"run-20260201T000000Z-h24-0123456789ab","files":{"forecast.csv":"sha256"}}',
        encoding="utf-8",
    )
    application.results[RUN_ID] = RunResult(RUN_ID, run_dir, "degraded", False)
    client = _client(_create_app(application))

    response = client.get(f"/api/runs/{RUN_ID}/forecast.csv")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.content == forecast_path.read_bytes()


def test_forecast_download_rejects_missing_file_and_symlink_escape(tmp_path: Path) -> None:
    application = StubApplication(tmp_path)
    empty_run = tmp_path / RUN_ID
    empty_run.mkdir()
    application.results[RUN_ID] = RunResult(RUN_ID, empty_run, "failed", False)
    client = _client(_create_app(application))

    missing = client.get(f"/api/runs/{RUN_ID}/forecast.csv")
    assert missing.status_code == 404

    outside = tmp_path / "outside.csv"
    outside.write_text("secret,content\n", encoding="utf-8")
    (empty_run / "forecast.csv").symlink_to(outside)
    escaped = client.get(f"/api/runs/{RUN_ID}/forecast.csv")

    assert escaped.status_code == 404
    assert "secret,content" not in escaped.text


def _tiny_history(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    local_times = pd.date_range(
        "2026-01-24 00:00", "2026-02-01 00:00", freq="10min", inclusive="left"
    )
    for index, turbine in enumerate(("turbine_1", "turbine_2"), start=1):
        pd.DataFrame(
            {
                "Статистическое время": local_times.strftime("%Y-%m-%d %H:%M:%S"),
                "Средняя скорость ветра(m/s)": [5.0 + index] * len(local_times),
                "Нормализованная активная мощность": [0.35 + index * 0.05] * len(local_times),
                "Средняя температура окружающей среды(°C)": [-3.0] * len(local_times),
            }
        ).to_csv(data_dir / f"{turbine}.csv", index=False)


def test_real_offline_application_persists_run_refreshes_reuse_and_csv_roundtrip(
    tmp_path: Path,
) -> None:
    from wind_forecast.config import Settings
    from wind_forecast.service import Application

    data_dir = tmp_path / "data"
    _tiny_history(data_dir)
    settings = Settings(
        root_dir=tmp_path,
        data_dir=data_dir,
        cache_dir=tmp_path / "cache",
        model_dir=tmp_path / "models",
        run_dir=tmp_path / "runs",
        fixture_dir=tmp_path / "fixtures",
        mode="demo",
    )
    application = Application(settings, offline=True, model_parameters={"iterations": 1})
    first_app = _create_app(application)
    first_client = _client(first_app)

    with first_client:
        first_job_id = _submit(first_client).json()["job_id"]
    first_job = first_client.get(f"/api/forecast-jobs/{first_job_id}").json()
    assert first_job["state"] == "completed"
    assert first_job["result"]["status"] in {"success", "degraded"}
    run_id = first_job["result"]["run_id"]
    saved = application.read_run(run_id)
    assert len(saved["forecast"]) == 48
    assert first_client.get(f"/api/runs/{run_id}").json()["run"]["run_id"] == run_id
    download = first_client.get(f"/api/runs/{run_id}/forecast.csv")
    assert download.status_code == 200
    assert download.content == (settings.run_dir / run_id / "forecast.csv").read_bytes()

    restarted_app = _create_app(application)
    restarted_client = _client(restarted_app)
    with restarted_client:
        refresh_job_id = _submit(restarted_client, refresh=True).json()["job_id"]
    refresh_job = restarted_client.get(f"/api/forecast-jobs/{refresh_job_id}").json()

    assert refresh_job["state"] == "completed"
    assert refresh_job["result"]["run_id"] == run_id
    assert refresh_job["result"]["reused"] is True
