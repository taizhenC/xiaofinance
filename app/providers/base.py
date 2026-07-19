"""The narrow interface the pipeline consumes from a content source.

The pipeline's contract with a provider is deliberately small: hand it a
SearchRequest, get back a RunResult plus a run directory of JSONL files that
ingest.py can read (xhs/jsonl/search_contents_*.jsonl + search_comments_*.jsonl,
one JSON object per line). Everything source-specific — vendored code, config
patching, CLI construction, log heuristics — lives behind this boundary so
upstream churn (and the vendor's non-commercial license) never leaks into the
pipeline or the public API surface.
"""

from dataclasses import dataclass
from enum import Enum
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


@dataclass(frozen=True)
class RunResult:
    exit_code: int | None
    timed_out: bool
    cancelled: bool
    log_path: Path


class SessionState(str, Enum):
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

    def login_looks_required(self, log_path: Path, notes_fresh: int) -> bool:
        """Did this run fail because the session is dead?"""
        ...

    def classify_log(self, log_text: str) -> SessionState:
        """Best-effort session diagnosis from a run's log output."""
        ...

    def cancel(self) -> None:
        """Abort the in-flight subprocess, if any. Idempotent."""
        ...
