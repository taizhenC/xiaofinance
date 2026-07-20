"""In-process job orchestration for fetch cycles (DC-02).

One job at a time (same mutual exclusion as before), but the job is a record:
stage, per-stage counts reported by the pipeline, elapsed time, and a
cancellation flag that is checked between stages — plus provider.cancel()
to abort an in-flight crawl subprocess immediately.

Deliberately stdlib-only and in-memory: a queue/broker would be absurd for a
single-user local app. The durable record of a cycle stays in fetch_runs;
this is the live view /api/status serves.
"""

import logging
import threading
from dataclasses import dataclass, field

from . import pipeline
from .providers import get_provider
from .util import now_ms

log = logging.getLogger(__name__)


@dataclass
class JobState:
    id: int
    mode: str
    started_at_ms: int
    stage: str = "starting"
    detail: dict = field(default_factory=dict)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    done: bool = False
    cancelled: bool = False
    error: str | None = None
    finished_at_ms: int | None = None

    def report(self, stage: str | None = None, **detail) -> None:
        """Progress callback handed to the pipeline. A stage change resets the
        detail dict so counts from one stage never bleed into the next."""
        if stage is not None and stage != self.stage:
            self.stage = stage
            self.detail = {}
        self.detail.update(detail)

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "mode": self.mode,
            "stage": self.stage,
            "detail": dict(self.detail),
            "started_at_ms": self.started_at_ms,
            "finished_at_ms": self.finished_at_ms,
            "cancel_requested": self.cancel_event.is_set(),
            "cancelled": self.cancelled,
            "done": self.done,
            "error": self.error,
        }


class JobRunner:
    """Owns the fetch lock; both the API endpoint and the scheduler start
    cycles through here — one entry point instead of two duplicated wrappers."""

    def __init__(self, lock: threading.Lock):
        self.lock = lock
        self._seq = 0
        self._job: JobState | None = None
        self._provider = None

    @property
    def running(self) -> bool:
        return self.lock.locked()

    def start(self, mode: str, settings=None) -> JobState | None:
        """Begin a cycle in a worker thread; None when one is already running."""
        if not self.lock.acquire(blocking=False):
            return None
        self._seq += 1
        job = JobState(id=self._seq, mode=mode, started_at_ms=now_ms())
        provider = get_provider(settings)
        self._job, self._provider = job, provider

        def worker():
            try:
                result = pipeline.run_cycle(
                    mode, settings=settings, provider=provider,
                    progress=job.report, cancel_event=job.cancel_event,
                )
                if isinstance(result, dict):
                    job.cancelled = bool(result.get("cancelled"))
            except Exception as e:
                log.exception("cycle failed")
                job.error = str(e)[:300]
            finally:
                job.done = True
                job.finished_at_ms = now_ms()
                job.report(stage="cancelled" if job.cancelled else "done")
                self._provider = None
                self.lock.release()

        threading.Thread(target=worker, daemon=True).start()
        return job

    def cancel(self) -> bool:
        """Request cancellation of the current job. Kills the in-flight crawl
        subprocess; non-crawl stages stop at the next stage boundary."""
        job = self._job
        if job is None or job.done:
            return False
        job.cancel_event.set()
        job.report(stage="cancelling")
        provider = self._provider
        if provider is not None:
            try:
                provider.cancel()
            except Exception:
                log.exception("provider cancel failed")
        return True

    def status(self) -> dict | None:
        job = self._job
        return job.snapshot() if job else None
