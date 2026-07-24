"""The cross-process guard on the single Chrome profile.

Chrome hands a second invocation for an already-owned profile to the live instance
and exits, so without this lock the loser waits out BROWSER_LAUNCH_TIMEOUT (60s)
for a debug port that never opens — the "login window never appears" bug.
"""

import json
from types import SimpleNamespace

import pytest

from infinance.browser_lock import BrowserBusy, browser_lock, lock_path


def settings(tmp_path):
    return SimpleNamespace(MEDIACRAWLER_DIR=tmp_path / "vendor")


def test_the_lock_lives_beside_the_profile_it_guards(tmp_path):
    s = settings(tmp_path)
    assert lock_path(s).parent.name == "browser_data"


def test_a_second_holder_is_refused_while_the_first_is_inside(tmp_path):
    s = settings(tmp_path)
    with browser_lock(s, "crawl"):
        with pytest.raises(BrowserBusy) as excinfo:
            with browser_lock(s, "login"):
                pass
    # the message has to name what to stop, or the user cannot act on it
    assert "crawl" in str(excinfo.value)


def test_the_lock_is_released_when_the_block_ends(tmp_path):
    s = settings(tmp_path)
    with browser_lock(s, "crawl"):
        pass
    assert not lock_path(s).exists()
    with browser_lock(s, "login"):  # must not raise
        pass


def test_the_lock_is_released_even_when_the_body_raises(tmp_path):
    s = settings(tmp_path)
    with pytest.raises(ValueError):
        with browser_lock(s, "crawl"):
            raise ValueError("crawl blew up")
    with browser_lock(s, "login"):  # a crash must not wedge the browser shut
        pass


def test_a_lock_from_a_dead_process_is_reclaimed(tmp_path):
    """A crawl killed with the window still up must not lock login out forever."""
    s = settings(tmp_path)
    path = lock_path(s)
    path.parent.mkdir(parents=True, exist_ok=True)
    # pid 1 exists on POSIX but is never this process; use an unassigned high pid
    path.write_text(json.dumps({"pid": 0x7FFFFFFF, "owner": "crawl"}), encoding="utf-8")
    with browser_lock(s, "login"):
        assert json.loads(path.read_text(encoding="utf-8"))["owner"] == "login"


def test_a_corrupt_lock_file_does_not_wedge_the_browser(tmp_path):
    s = settings(tmp_path)
    path = lock_path(s)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{half-written", encoding="utf-8")
    with browser_lock(s, "login"):  # unreadable == unheld, not "blocked forever"
        pass
