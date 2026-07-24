"""Cross-process guard around the one Chrome profile MediaCrawler drives.

MediaCrawler always launches Chrome on a single hardcoded profile directory
(`browser_data/cdp_xhs_user_data_dir`). Chrome's singleton hands a second
invocation for an already-owned profile straight to the live instance and then
exits, so the loser's `--remote-debugging-port` never opens: BrowserLauncher
waits out its whole BROWSER_LAUNCH_TIMEOUT (60s) for a port that will never
answer. That is the "the login window takes forever to appear" symptom, and the
same collision is why a crawl can sit for a minute and then fetch nothing.

The server already refuses a login while a cycle runs (`main.api_session_login`)
and a second cycle while one runs (`jobs.Runner.start`) — but both guards are
in-process, and `infinance login` in a terminal is a *different* process that
sees neither. This lock is the cross-process version of those guards.

It lives beside the profile it protects rather than in the data dir, so it is
scoped to exactly the resource being contended: one vendor checkout, one Chrome
profile, one lock.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from pathlib import Path

import psutil

log = logging.getLogger(__name__)

LOCK_NAME = "browser.lock"


class BrowserBusy(RuntimeError):
    """Another process is already driving the Chrome profile."""

    def __init__(self, owner: str, pid: int):
        self.owner = owner
        self.pid = pid
        super().__init__(f"the login browser is already in use by a {owner} (pid {pid})")


def lock_path(settings) -> Path:
    return Path(settings.MEDIACRAWLER_DIR) / "browser_data" / LOCK_NAME


def _holder(path: Path) -> tuple[str, int] | None:
    """`(owner, pid)` when a live process holds the lock, else None.

    A lock whose pid is gone is stale and must not count: a crawl killed with the
    window still up would otherwise wedge login out of the browser forever, which
    is a worse failure than the one this module exists to fix.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        pid, owner = int(data["pid"]), str(data.get("owner", "unknown"))
    except (OSError, ValueError, TypeError, KeyError):
        # missing, unreadable, or caught half-written — treat as unheld
        return None
    if not psutil.pid_exists(pid):
        log.info("clearing stale browser lock from dead pid %d (%s)", pid, owner)
        return None
    return owner, pid


@contextmanager
def browser_lock(settings, owner: str):
    """Hold the Chrome profile for the duration of the block.

    Raises `BrowserBusy` immediately instead of queueing. Waiting would only move
    the doomed launch later; the caller needs to be told *now* that something else
    holds the browser, and which thing to stop.
    """
    path = lock_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd = None
    for _ in range(2):  # one retry, to clear a single stale file
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            held = _holder(path)
            if held is not None:
                raise BrowserBusy(*held) from None
            path.unlink(missing_ok=True)
    if fd is None:
        # lost the race to whoever cleared the stale file first
        raise BrowserBusy("crawl or login", -1)

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"pid": os.getpid(), "owner": owner}, fh)
        yield
    finally:
        path.unlink(missing_ok=True)
