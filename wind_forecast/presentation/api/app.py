"""FastAPI transport adapter for the existing forecasting application."""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Receive, Scope, Send

from wind_forecast.config import Settings
from wind_forecast.contracts import parse_request
from wind_forecast.presentation.api.jobs import (
    JobConflict,
    JobNotFound,
    JobRunner,
    JobRunnerClosed,
)
from wind_forecast.presentation.api.schemas import ForecastJobInput
from wind_forecast.presentation.api.serialization import run_view, valid_run_id
from wind_forecast.service import Application

_ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
_ALLOWED_PORTS = {8000, 4321}
_MAX_REQUEST_BYTES = 2048
_ARTIFACT_NAMES = (
    "forecast.csv",
    "manifest.json",
    "metrics.json",
    "events.jsonl",
    "weather.json",
    "report.md",
)


def _error(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}}, status_code=status_code
    )


def _local_host_header(value: str) -> bool:
    if not value or any(character.isspace() for character in value):
        return False
    try:
        parsed = urlsplit(f"//{value}")
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.hostname in _ALLOWED_HOSTS
        and parsed.username is None
        and parsed.password is None
        and parsed.path == ""
        and parsed.query == ""
        and parsed.fragment == ""
        and (port is None or port in _ALLOWED_PORTS)
    )


def _local_origin(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "http"
        and parsed.hostname in _ALLOWED_HOSTS
        and port in _ALLOWED_PORTS
        and parsed.username is None
        and parsed.password is None
        and parsed.path in {"", "/"}
        and parsed.query == ""
        and parsed.fragment == ""
    )


def _check_run_tree(application: Any, run_id: str) -> Path | None:
    """Reject existing run files that are symlinks or escape the configured root."""
    settings = getattr(application, "settings", None)
    configured_root = getattr(settings, "run_dir", None)
    if configured_root is None:
        return None

    root = Path(configured_root).expanduser().resolve()
    directory = root / run_id
    if directory.is_symlink():
        raise FileNotFoundError("run artifacts do not exist")
    if not directory.exists():
        return directory
    resolved_directory = directory.resolve()
    if resolved_directory.parent != root or not resolved_directory.is_dir():
        raise FileNotFoundError("run artifacts do not exist")

    for name in _ARTIFACT_NAMES:
        artifact = directory / name
        if not artifact.exists() and not artifact.is_symlink():
            continue
        if artifact.is_symlink() or not artifact.is_file():
            raise FileNotFoundError("run artifacts do not exist")
        if artifact.resolve().parent != resolved_directory:
            raise FileNotFoundError("run artifacts do not exist")
    return directory


class LocalApiBoundaryMiddleware:
    """Apply loopback host, origin, and JSON checks without a threadpool hop."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        host = headers.get(b"host", b"").decode("latin-1")
        if not _local_host_header(host):
            await _error(
                "host_not_allowed", "Requests must use a local host.", 400
            )(scope, receive, send)
            return

        is_submission = (
            scope.get("method") == "POST"
            and scope.get("path") == "/api/forecast-jobs"
        )
        if is_submission:
            content_type = headers.get(b"content-type", b"").decode("latin-1")
            media_type = content_type.partition(";")[0].strip().lower()
            if media_type != "application/json":
                await _error(
                    "json_required", "The request must use application/json.", 415
                )(scope, receive, send)
                return
            origin = headers.get(b"origin")
            if origin is not None and not _local_origin(origin.decode("latin-1")):
                await _error(
                    "origin_not_allowed", "Requests must come from a local origin.", 403
                )(scope, receive, send)
                return

            content_length = headers.get(b"content-length")
            if content_length is not None:
                try:
                    declared_size = int(content_length)
                except ValueError:
                    await _error(
                        "invalid_request", "The request is invalid.", 400
                    )(scope, receive, send)
                    return
                if declared_size < 0 or declared_size > _MAX_REQUEST_BYTES:
                    await _error(
                        "request_too_large", "The request is too large.", 413
                    )(scope, receive, send)
                    return

            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                if message["type"] != "http.request":
                    continue
                body.extend(message.get("body", b""))
                if len(body) > _MAX_REQUEST_BYTES:
                    await _error(
                        "request_too_large", "The request is too large.", 413
                    )(scope, receive, send)
                    return
                if not message.get("more_body", False):
                    break

            body_bytes = bytes(body)
            delivered = False

            async def replay_body():
                nonlocal delivered
                if delivered:
                    return {"type": "http.request", "body": b"", "more_body": False}
                delivered = True
                return {"type": "http.request", "body": body_bytes, "more_body": False}

            await self.app(scope, replay_body, send)
            return

        await self.app(scope, receive, send)


def _load_run_view(
    application: Any, run_id: str, result: Any | None = None
) -> dict[str, Any]:
    _check_run_tree(application, run_id)
    data = application.read_run(run_id)
    # For persisted runs, the stored manifest is the source of identity. The
    # application already validates its content-addressed directory boundary.
    if result is None:
        manifest = data.get("manifest") if isinstance(data, Mapping) else None
        status = manifest.get("status") if isinstance(manifest, Mapping) else None
        result = SimpleNamespace(run_id=run_id, status=status, reused=False)
    return run_view(result, data)


def create_app(application: Any | None = None) -> FastAPI:
    """Create one API server around an Application-compatible facade.

    When no facade is injected, settings and the application are constructed
    once for this server. Synthetic weather requires ``DEMO_OFFLINE=1`` and
    ``RUN_MODE=demo``; requests cannot change either setting.
    """
    if application is None:
        settings = Settings.from_env()
        offline_value = os.environ.get("DEMO_OFFLINE", "0")
        if offline_value not in {"0", "1"}:
            raise ValueError("DEMO_OFFLINE must be 0 or 1")
        offline = offline_value == "1"
        if offline and settings.mode != "demo":
            raise ValueError("DEMO_OFFLINE=1 requires RUN_MODE=demo")
        application = Application(settings, offline=offline)

    job_runner = JobRunner()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        del app
        try:
            yield
        finally:
            job_runner.shutdown(wait=True)

    app = FastAPI(
        title="Wind Forecast API",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.application = application
    app.state.job_runner = job_runner

    app.add_middleware(LocalApiBoundaryMiddleware)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        del request, exc
        return _error("invalid_request", "The request is invalid.", 422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        del request
        if exc.status_code == 404:
            return _error("not_found", "The requested resource was not found.", 404)
        if exc.status_code == 405:
            return _error("method_not_allowed", "The request method is not allowed.", 405)
        return _error("request_rejected", "The request could not be accepted.", exc.status_code)

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
        del request, exc
        return _error("internal_error", "The request could not be completed.", 500)

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        settings = application.settings
        return {
            "status": "ok",
            "mode": settings.mode,
            "offline": bool(application.offline),
        }

    @app.get("/api/runs/latest")
    async def latest_run() -> dict[str, Any]:
        result = application.latest()
        if result is None:
            return {"run": None}
        if not valid_run_id(result.run_id):
            return {"run": None}
        try:
            return {"run": _load_run_view(application, result.run_id, result)}
        except (FileNotFoundError, KeyError, ValueError):
            return {"run": None}

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str) -> Any:
        if not valid_run_id(run_id):
            return _error("run_not_found", "The requested run was not found.", 404)
        try:
            return {"run": _load_run_view(application, run_id)}
        except (FileNotFoundError, KeyError):
            return _error("run_not_found", "The requested run was not found.", 404)

    @app.post("/api/forecast-jobs", status_code=202)
    async def submit_forecast(body: ForecastJobInput) -> Any:
        try:
            request = parse_request(body.origin, body.horizon, application.settings.mode)
        except ValueError:
            return _error("invalid_request", "The request is invalid.", 422)

        def execute() -> dict[str, Any]:
            result = application.run(request, refresh=body.refresh)
            stored = application.read_run(result.run_id)
            return run_view(result, stored)

        try:
            return job_runner.submit(execute)
        except JobConflict:
            return _error(
                "job_already_running", "A forecast is already running.", 409
            )
        except JobRunnerClosed:
            return _error("server_shutting_down", "The server is shutting down.", 503)

    @app.get("/api/forecast-jobs/{job_id}")
    async def get_forecast_job(job_id: str) -> Any:
        if not re_full_job_id(job_id):
            return _error("job_not_found", "The requested job was not found.", 404)
        try:
            return job_runner.get(job_id)
        except JobNotFound:
            return _error("job_not_found", "The requested job was not found.", 404)

    @app.get("/api/runs/{run_id}/forecast.csv")
    async def download_forecast(run_id: str) -> Any:
        if not valid_run_id(run_id):
            return _error("run_not_found", "The requested run was not found.", 404)
        try:
            directory = _check_run_tree(application, run_id)
        except (FileNotFoundError, OSError, ValueError):
            directory = None
        if directory is None:
            return _error("forecast_not_found", "The forecast file was not found.", 404)

        forecast_path = directory / "forecast.csv"
        manifest_path = directory / "manifest.json"
        if not forecast_path.is_file() or not manifest_path.is_file():
            return _error("forecast_not_found", "The forecast file was not found.", 404)
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _error("forecast_not_found", "The forecast file was not found.", 404)
        files = manifest.get("files") if isinstance(manifest, Mapping) else None
        if (
            not isinstance(manifest, Mapping)
            or manifest.get("run_id") != run_id
            or not isinstance(files, Mapping)
            or "forecast.csv" not in files
        ):
            return _error("forecast_not_found", "The forecast file was not found.", 404)
        try:
            contents = forecast_path.read_bytes()
        except OSError:
            return _error("forecast_not_found", "The forecast file was not found.", 404)
        return Response(
            content=contents,
            media_type="text/csv",
            headers={"content-disposition": 'attachment; filename="forecast.csv"'},
        )

    return app


def re_full_job_id(value: str) -> bool:
    """Validate the opaque UUID4 IDs used by the in-memory job registry."""
    return len(value) == 32 and all(character in "0123456789abcdef" for character in value)
