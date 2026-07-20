"""The interface the pipeline and API consume from a content source.

The pipeline's contract with a provider: hand it a SearchRequest, get back a
RunResult plus a run directory of JSONL files that ingest.py can read
(xhs/jsonl/search_contents_*.jsonl + search_comments_*.jsonl, one JSON object
per line). The provider also owns every reader of those artifacts — live
progress, per-keyword yield, run anatomy, failure classification — because
their grammar (log markers, file layout) is source-specific. Everything else
about the source (vendored code, config patching, CLI construction) stays
behind this boundary so upstream churn (and the vendor's non-commercial
license) never leaks into the pipeline or the public API surface.
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class SearchRequest:
    keywords: list[str]
    run_dir: Path
    max_notes_per_keyword: int
    max_comments_per_note: int
    include_sub_comments: bool
    timeout_min: int
    get_comments: bool = True
    # routine crawls run headless (per settings); the login flow opts into a
    # visible window because the user must scan a QR code in it
    visible_browser: bool = False


@dataclass(frozen=True)
class RunResult:
    exit_code: int | None
    timed_out: bool
    cancelled: bool
    log_path: Path
    # the CAPTCHA wall: the watcher killed the run (or counted 461s on the way out)
    risk_controlled: bool = False
    captchas: int = 0
    # byte offset where this run's slice of the cycle's shared log begins
    log_start: int = 0


class SessionState(StrEnum):
    VALID = "valid"
    EXPIRED = "expired"
    UNAUTHORIZED = "unauthorized"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class LoginOutcome:
    ok: bool
    state: SessionState
    detail: str = ""


class SourceProvider(Protocol):
    """One instance per cycle/job; search() and login() are blocking, cancel()
    may be called from another thread while search() runs."""

    name: str

    def preflight(self) -> list[str]:
        """Human-readable problems preventing a crawl (empty = ready)."""
        ...

    def search(self, req: SearchRequest) -> RunResult: ...

    def login(self, timeout_min: int = 6) -> LoginOutcome:
        """Interactive (visible browser) login; blocks until done or timeout."""
        ...

    def login_looks_required(self, log_path: Path, notes_fresh: int, start: int = 0) -> bool:
        """Did this run fail because the session is dead?"""
        ...

    def classify_log(self, log_text: str) -> SessionState:
        """Best-effort session diagnosis from a run's log output."""
        ...

    def cancel(self) -> None:
        """Abort the in-flight subprocess, if any. Idempotent."""
        ...

    # ---- run-artifact readers ----------------------------------------------

    def keyword_counts(self, run_dir: Path) -> tuple[dict[str, int], dict[str, int]]:
        """Per-keyword (notes, comments) counts from a run directory."""
        ...

    def crawl_progress(self, run_dir: Path, keywords: list[str],
                       target_per_keyword: int = 20) -> dict:
        """Where a running crawl has got to (phase, keyword i/n, live counts)."""
        ...

    def crawl_detail(self, run_dir: Path, keywords: list[str], status: str) -> dict:
        """The anatomy of one run: keyword timeline, yields, cause of death."""
        ...

    def failure_reason(self, log_path: Path, exit_code: int, start: int = 0) -> str:
        """Name the cause of a failed run (wall / network / retries / unknown)."""
        ...

    def append_log_line(self, log_path: Path, message: str) -> None:
        """Pipeline-side note written into the run's own log."""
        ...
