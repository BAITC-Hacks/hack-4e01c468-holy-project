"""Thread-safe admission and bounded in-memory tracking for local forecast jobs."""

from __future__ import annotations

import copy
import threading
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable


class JobConflict(Exception):
    """Raised when a forecast is already active."""


class JobRunnerClosed(Exception):
    """Raised when the application is shutting down."""


class JobNotFound(Exception):
    """Raised when a job ID is not retained by this server process."""


@dataclass
class _Job:
    job_id: str
    state: str = "queued"
    result: dict[str, Any] | None = None
    error: dict[str, str] | None = None
    terminal: threading.Event = field(default_factory=threading.Event)

    def view(self) -> dict[str, Any]:
        return copy.deepcopy(
            {
                "job_id": self.job_id,
                "state": self.state,
                "result": self.result,
                "error": self.error,
            }
        )


class JobRunner:
    """Run one forecast at a time and keep only a bounded terminal history."""

    _MAX_COMPLETED = 100

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="forecast-job")
        self._jobs: OrderedDict[str, _Job] = OrderedDict()
        self._active_job: str | None = None
        self._closed = False

    def submit(self, execute: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        """Admit one callable and immediately return its job record."""
        with self._lock:
            if self._closed:
                raise JobRunnerClosed
            if self._active_job is not None:
                raise JobConflict
            job = _Job(job_id=uuid.uuid4().hex)
            self._active_job = job.job_id
            self._jobs[job.job_id] = job
            try:
                self._executor.submit(self._execute, job.job_id, execute)
            except RuntimeError as exc:
                self._active_job = None
                self._jobs.pop(job.job_id, None)
                raise JobRunnerClosed from exc
            return job.view()

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise JobNotFound
            return job.view()

    def wait_for_terminal(self, job_id: str, timeout: float | None = None) -> bool:
        """Wait for an existing job to reach a terminal state."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise JobNotFound
            event = job.terminal
        return event.wait(timeout)

    def shutdown(self, *, wait: bool = True) -> None:
        """Stop admission and join the worker during server shutdown."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=wait, cancel_futures=False)

    def _execute(self, job_id: str, execute: Callable[[], dict[str, Any]]) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.state = "running"

        try:
            result = execute()
        except Exception:
            self._finish(
                job_id,
                state="failed",
                result=None,
                error={
                    "code": "execution_error",
                    "message": "The forecast could not be completed.",
                },
            )
        else:
            self._finish(job_id, state="completed", result=result, error=None)

    def _finish(
        self,
        job_id: str,
        *,
        state: str,
        result: dict[str, Any] | None,
        error: dict[str, str] | None,
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.state = state
            job.result = result
            job.error = error
            self._active_job = None
            job.terminal.set()
            self._trim_completed()

    def _trim_completed(self) -> None:
        completed = [
            job_id
            for job_id, job in self._jobs.items()
            if job.state in {"completed", "failed"}
        ]
        for expired_id in completed[: max(0, len(completed) - self._MAX_COMPLETED)]:
            self._jobs.pop(expired_id, None)
