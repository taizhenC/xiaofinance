"""MediaCrawler adapter — the only module that knows the vendor exists.

Owns: the pinned vendor commit, config/code/UA patching, cookie delivery,
CLI construction, subprocess lifecycle (CAPTCHA watcher + timeout + kill-tree
+ cancel), and every reader of the run directory's artifacts (per-keyword
counts, live progress, run anatomy, failure classification). The patch
contract is hard-fail: if upstream renames an anchor we raise with a clear
message instead of crawling with silently-wrong config.

MediaCrawler is non-commercial/learning-licensed. It stays a user-fetched,
pinned checkout that this adapter talks to over its CLI — it is never
imported, bundled, or redistributed.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import psutil

from .base import LoginOutcome, RunResult, SearchRequest, SessionState

log = logging.getLogger(__name__)

VENDOR_REPO = "https://github.com/NanmiCoder/MediaCrawler.git"
VENDOR_PIN = "3bde9e2015f912f2e19ee63b615a0f48b9a90315"

# The only MediaCrawler internals we touch. Patcher hard-fails if upstream renames them.
# CDP mode drives the user's installed Chrome with a persistent profile under
# browser_data/cdp_xhs_user_data_dir. Routine crawls stay headless; the login flow opts
# into a visible window when the session needs user interaction.
PATCHES = [
    ("config/xhs_config.py", "SORT_TYPE", '"time_descending"'),
    ("config/base_config.py", "ENABLE_CDP_MODE", "True"),
    # Vendor default is True: MediaCrawler then *attaches* to a Chrome the user is
    # expected to have already started with --remote-debugging-port=9222 and, finding
    # none, blocks the full BROWSER_LAUNCH_TIMEOUT (60s) polling a dead port before it
    # errors out — the "login window takes forever to appear" symptom. We drive the
    # browser ourselves, so it must launch its own Chrome on the isolated
    # cdp_xhs_user_data_dir profile instead of waiting to connect to one.
    ("config/base_config.py", "CDP_CONNECT_EXISTING", "False"),
]

# Line patches for things that aren't config variables. Same contract as PATCHES:
# hard-fail if the anchor line disappears upstream, no-op if already applied.
CODE_PATCHES = [
    # Playwright's default 30s page timeout kills the QR login flow on slow
    # connections before the code can even render — give it 120s instead.
    (
        "media_platform/xhs/core.py",
        "self.context_page = await self.browser_context.new_page()",
        "self.context_page = await self.browser_context.new_page()\n"
        "            self.context_page.set_default_timeout(120_000)\n"
        "            self.context_page.set_default_navigation_timeout(120_000)",
    ),
    # goto() defaults to waiting for `load` — every image, font and tracker on the XHS
    # home page. That page takes ~23s just to deliver its HTML from here, so `load` blows
    # past even a 120s timeout and the crawl dies before it searches anything. The crawler
    # only needs the page's cookies and JS context (the signed API calls go out over
    # httpx), and both exist at domcontentloaded.
    (
        "media_platform/xhs/core.py",
        "await self.context_page.goto(self.index_url)",
        'await self.context_page.goto(self.index_url, wait_until="domcontentloaded")',
    ),
    # XHS returns each root comment with its first few replies already nested inside the
    # same response (`sub_comments`), and MediaCrawler hands those straight to the store
    # callback — they cost nothing. What follows is the expensive half: one request per
    # comment to chase the replies XHS withheld behind `sub_comment_has_more`. On an
    # account XHS has already flagged, that request volume is the thing that walls us, so
    # take the free replies and skip the paid ones. Anything below here is now unreachable.
    (
        "media_platform/xhs/client.py",
        '                sub_comment_has_more = comment.get("sub_comment_has_more")\n'
        "                if not sub_comment_has_more:\n"
        "                    continue\n",
        "                continue  # xiaofinance: inline replies only, never page for more\n",
    ),
    # One flavour of the 461 risk-control wall carries no Verifytype header, and reading
    # it with [] crashed the CAPTCHA detector itself — the KeyError escaped before
    # "CAPTCHA appeared" was logged, so the abort counter saw nothing and the run died as
    # an anonymous RetryError (run 16).
    (
        "media_platform/xhs/client.py",
        '            verify_type = response.headers["Verifytype"]\n'
        '            verify_uuid = response.headers["Verifyuuid"]\n',
        '            verify_type = response.headers.get("Verifytype", "unknown")\n'
        '            verify_uuid = response.headers.get("Verifyuuid", "unknown")\n',
    ),
    # A comment fetch that fails after retries propagates through asyncio.gather and
    # kills the whole crawl — run 16 lost keywords 4-6 to a single walled response. Skip
    # the note instead; a real CAPTCHA storm still aborts via CAPTCHA_ABORT_COUNT.
    (
        "media_platform/xhs/core.py",
        "            await self.xhs_client.get_note_all_comments(\n"
        "                note_id=note_id,\n"
        "                xsec_token=xsec_token,\n"
        "                crawl_interval=crawl_interval,\n"
        "                callback=xhs_store.batch_update_xhs_note_comments,\n"
        "                max_count=config.CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES,\n"
        "            )\n",
        "            try:\n"
        "                await self.xhs_client.get_note_all_comments(\n"
        "                    note_id=note_id,\n"
        "                    xsec_token=xsec_token,\n"
        "                    crawl_interval=crawl_interval,\n"
        "                    callback=xhs_store.batch_update_xhs_note_comments,\n"
        "                    max_count=config.CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES,\n"
        "                )\n"
        "            except Exception as ex:\n"
        '                utils.logger.error(f"[XiaoHongShuCrawler.get_comments] '
        'comments failed for note {note_id}, skipping: {ex}")\n',
    ),
    # get_browser_info() shells out to `chrome.exe --version` with no --user-data-dir,
    # so it targets the user's own default Chrome profile, not our isolated
    # cdp_xhs_user_data_dir one. If the user's real Chrome is already running, Chrome's
    # singleton IPC forwards the invocation to that live instance instead of just
    # printing a version string — popping a blank window in the user's own browser on
    # every keyword's crawl process. The version string is log-only, so skip the
    # subprocess call entirely rather than fight the singleton behaviour.
    (
        "tools/browser_launcher.py",
        "            # Try to get version info\n"
        "            try:\n"
        '                result = subprocess.run([browser_path, "--version"],\n'
        "                                      capture_output=True, text=True, encoding='utf-8', errors='ignore', timeout=5)\n"
        "                version = result.stdout.strip() if result.stdout else \"Unknown Version\"\n"
        "            except:\n"
        '                version = "Unknown Version"\n',
        '            version = "Unknown Version"  # xiaofinance: --version pokes the '
        "user's real Chrome via singleton IPC, skip it\n",
    ),
    # A freshly launched Chrome always has its own default startup tab (the New Tab
    # Page) sitting in the context CDP reuses; MediaCrawler opens a second page for XHS
    # and never touches the first, so every crawl left an unrelated NTP tab open
    # alongside the working one. Close the leftover only *after* our own page exists —
    # closing a real Chrome window's last remaining tab first would close the window
    # (and likely the whole process) before new_page() got a chance to open one.
    (
        "media_platform/xhs/core.py",
        "self.context_page = await self.browser_context.new_page()\n"
        "            self.context_page.set_default_timeout(120_000)\n"
        "            self.context_page.set_default_navigation_timeout(120_000)",
        "self.context_page = await self.browser_context.new_page()\n"
        "            self.context_page.set_default_timeout(120_000)\n"
        "            self.context_page.set_default_navigation_timeout(120_000)\n"
        "            for leftover_page in self.browser_context.pages:\n"
        "                if leftover_page is not self.context_page:\n"
        "                    await leftover_page.close()",
    ),
]

LOGIN_HINTS = ["扫码", "二维码", "请扫码", "未登录", "登录已过期", "login expired", "login failed"]
# The platform can expire a session mid-crawl, so a run that fetched notes and *then*
# died still needs a re-login. These say so outright — trust them over the note count.
EXPIRED_MARKERS = ["登录已过期", "login expired"]
# The mainland-vs-RedNote backend mismatch (and platform-gated accounts) answer every
# search with this even though the login itself succeeded.
UNAUTHORIZED_MARKERS = ["没有权限访问"]

CAPTCHA_MARKER = "CAPTCHA appeared"
NETWORK_MARKERS = ["ConnectError", "ConnectTimeout", "ReadTimeout", "ProxyError", "SSLError"]
KEYWORD_RE = re.compile(r"Current search keyword: (.+)")
POLL_SEC = 5


def _bool(v) -> str:
    return "True" if v else "False"


def _patch_var(mc_dir: Path, rel: str, var: str, value: str, redact: bool = False) -> None:
    """Set `var = value` in a vendor config file. Hard-fails when the variable
    is gone (upstream layout changed). `redact` keeps secrets out of our logs."""
    path = mc_dir / rel
    if not path.exists():
        raise RuntimeError(f"MediaCrawler file missing: {path} — run `infinance setup`")
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(rf"^({re.escape(var)}\s*=\s*).*$", re.MULTILINE)
    if not pattern.search(text):
        raise RuntimeError(
            f"MediaCrawler config variable {var} not found in {rel} — upstream layout changed; "
            f"update PATCHES in providers/mediacrawler.py or re-pin the vendor commit"
        )
    new_text = pattern.sub(lambda m: m.group(1) + value, text, count=1)
    if new_text != text:
        path.write_text(new_text, encoding="utf-8")
        log.info("patched %s: %s = %s", rel, var, "<redacted>" if redact else value)


def patch_config(mc_dir: Path, settings=None, browser_headless: bool | None = None) -> None:
    if settings is None:
        from ..config import settings
    if browser_headless is None:
        browser_headless = settings.BROWSER_HEADLESS
    patches = PATCHES + [
        ("config/base_config.py", "CDP_HEADLESS", _bool(browser_headless)),
        ("config/base_config.py", "XHS_INTERNATIONAL", _bool(settings.XHS_INTERNATIONAL)),
        ("config/base_config.py", "CRAWLER_MAX_SLEEP_SEC", str(settings.CRAWL_SLEEP_SEC)),
    ]
    for rel, var, value in patches:
        _patch_var(mc_dir, rel, var, value)
    for rel, anchor, replacement in CODE_PATCHES:
        path = mc_dir / rel
        if not path.exists():
            raise RuntimeError(f"MediaCrawler file missing: {path} — run `infinance setup`")
        text = path.read_text(encoding="utf-8")
        if replacement in text:
            continue
        if anchor not in text:
            raise RuntimeError(
                f"MediaCrawler anchor line not found in {rel} — upstream layout changed; "
                f"update CODE_PATCHES in providers/mediacrawler.py or re-pin the vendor commit"
            )
        path.write_text(text.replace(anchor, replacement, 1), encoding="utf-8")
        log.info("patched %s: %s", rel, replacement.strip().splitlines()[0])
    _patch_user_agent(mc_dir, settings.BROWSER_USER_AGENT)
    _patch_detail_slice(mc_dir, settings.MAX_NOTES_PER_KEYWORD)
    _ensure_chrome_keeps_session_cookies(mc_dir)


# Chromium deletes non-persistent (session) cookies from its on-disk store at every
# fresh launch unless the profile is set to "continue where you left off" — and XHS's
# login cookie is session-scoped. Each keyword gets its own from-scratch Chrome process
# (one per keyword, always hard-killed when done), so without this the account was
# logging out before the next keyword's launch, forcing a fresh QR scan almost every
# cycle even though the profile directory itself persists.
def _ensure_chrome_keeps_session_cookies(mc_dir: Path) -> None:
    prefs_path = mc_dir / "browser_data" / "cdp_xhs_user_data_dir" / "Default" / "Preferences"
    prefs_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(prefs_path.read_text(encoding="utf-8")) if prefs_path.exists() else {}
    except (OSError, ValueError):
        data = {}
    session = data.setdefault("session", {})
    if session.get("restore_on_startup") == 1:
        return
    session["restore_on_startup"] = 1
    prefs_path.write_text(json.dumps(data), encoding="utf-8")
    log.info("patched Chrome profile Preferences: session.restore_on_startup = 1")


# Regex rather than an anchor swap so re-running with a different UA re-patches cleanly.
# Only matches the hardcoded string assignment, not the commented-out get_user_agent() line.
UA_RE = re.compile(r'^(\s*self\.user_agent\s*=\s*)".*"$', re.MULTILINE)


def _patch_user_agent(mc_dir: Path, user_agent: str) -> None:
    path = mc_dir / "media_platform/xhs/core.py"
    text = path.read_text(encoding="utf-8")
    if not UA_RE.search(text):
        raise RuntimeError(
            "MediaCrawler xhs core.py no longer assigns a literal self.user_agent — "
            "upstream layout changed; update _patch_user_agent or re-pin the vendor commit"
        )
    new_text = UA_RE.sub(lambda m: f'{m.group(1)}"{user_agent}"', text, count=1)
    if new_text != text:
        path.write_text(new_text, encoding="utf-8")
        log.info("patched xhs core.py: user_agent = %s", user_agent)


# The search page is 1 request for a fixed 20 notes; the details and comments behind it
# are ~97% of a keyword's cost (a request per note plus one per comment page). Slicing
# the page to its newest N — sort is time_descending — is what makes
# MAX_NOTES_PER_KEYWORD below 20 actually cut traffic; MediaCrawler itself rounds the
# count cap up to a full page. Sliced at the comprehension source, not at the gather,
# so no coroutine is created only to go un-awaited. Regex so a changed N re-patches
# cleanly (same contract as the UA patch).
DETAIL_SLICE_RE = re.compile(
    r'for post_item in notes_res\.get\("items", \{\}\)(?:\[:\d+\])?'
)


def _patch_detail_slice(mc_dir: Path, max_notes: int) -> None:
    path = mc_dir / "media_platform/xhs/core.py"
    text = path.read_text(encoding="utf-8")
    if not DETAIL_SLICE_RE.search(text):
        raise RuntimeError(
            "MediaCrawler xhs core.py no longer iterates search items where expected — "
            "upstream layout changed; update _patch_detail_slice or re-pin the vendor commit"
        )
    new_text = DETAIL_SLICE_RE.sub(
        f'for post_item in notes_res.get("items", {{}})[:{max_notes}]', text, count=1
    )
    if new_text != text:
        path.write_text(new_text, encoding="utf-8")
        log.info("patched xhs core.py: detail slice = newest %d notes per page", max_notes)


def set_cookie_config(mc_dir: Path, cookies: str) -> None:
    """Deliver the session cookie through the vendor's config file instead of
    argv, so a live session token never shows up in the OS process list.
    Written as a Python literal (repr) so quotes/backslashes can't break the
    file; newlines are stripped because a cookie header is single-line."""
    clean = (cookies or "").replace("\r", "").replace("\n", " ").strip()
    _patch_var(mc_dir, "config/base_config.py", "COOKIES", repr(clean), redact=True)


def clear_cookie_config(mc_dir: Path) -> None:
    """Best-effort restore after a run so the cookie doesn't linger in the
    vendor checkout longer than needed."""
    try:
        _patch_var(mc_dir, "config/base_config.py", "COOKIES", '""', redact=True)
    except (RuntimeError, OSError):
        log.warning("could not clear COOKIES from vendor config")


class _CaptchaWatcher:
    """Counts CAPTCHA lines as the log grows, reading only what is new each poll.

    Starts at the log's current end: the file accumulates one cycle's per-keyword
    crawler processes, and an earlier keyword's wall must not abort a later keyword
    that is doing fine."""

    def __init__(self, log_path: Path):
        self.path = Path(log_path)
        try:
            self.pos = self.path.stat().st_size
        except OSError:
            self.pos = 0
        self.start = self.pos  # where this invocation's slice of the shared log begins
        self.carry = ""
        self.count = 0

    def poll(self) -> int:
        try:
            with open(self.path, "rb") as f:
                f.seek(self.pos)
                chunk = f.read()
                self.pos = f.tell()
        except OSError:  # the crawler still holds it — try again next tick
            return self.count
        if chunk:
            text = self.carry + chunk.decode("utf-8", errors="replace")
            self.count += text.count(CAPTCHA_MARKER)
            # a marker split across two reads would otherwise be missed
            self.carry = text[-(len(CAPTCHA_MARKER) - 1):]
        return self.count


# ---- run-artifact readers (module-level: pure functions over the run dir) ----


def _log_text(log_path: Path, tail_bytes: int | None = None, start: int = 0) -> str:
    """Reads the tail by default: a risk-controlled run writes an error line per retry and
    a full note dump per result, so these logs run to megabytes. `start` scopes the read
    to one keyword's slice of the cycle's shared log."""
    path = Path(log_path)
    if not path.exists():
        return ""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            lo = max(start, f.tell() - tail_bytes) if tail_bytes else start
            f.seek(max(0, lo))
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def failure_reason(log_path: Path, exit_code: int, start: int = 0) -> str:
    """MediaCrawler exits 1 for every unhandled error alike, so the exit code on its own
    tells you nothing about whether to retry, re-login, or back off. Name the cause."""
    text = _log_text(log_path, start=start)
    captchas = text.count(CAPTCHA_MARKER)
    if captchas:
        return (
            f"XHS risk control: {captchas} requests answered with a CAPTCHA (461) — "
            "the account is being rate-limited, not logged out"
        )
    if "KeyError: 'Verifytype'" in text:
        return (
            "XHS risk control (461 without a Verifytype header) — MediaCrawler's CAPTCHA "
            f"detector crashed on it before it could be counted; crawler exit code {exit_code}"
        )
    for m in NETWORK_MARKERS:
        if m in text:
            return f"network error ({m}) — crawler exit code {exit_code}"
    if "RetryError" in text:
        return f"XHS API kept failing until retries ran out — crawler exit code {exit_code}"
    return f"crawler exit code {exit_code}"


def _count_lines(paths) -> int:
    total = 0
    for p in paths:
        try:
            with open(p, "rb") as f:
                total += sum(1 for _ in f)
        except OSError:
            pass
    return total


def _iter_jsonl(path: Path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


def keyword_counts(run_dir: Path) -> tuple[dict[str, int], dict[str, int]]:
    """Per-keyword note/comment counts. Notes carry source_keyword; comments only carry
    note_id, so they inherit their note's keyword."""
    jsonl = Path(run_dir) / "xhs" / "jsonl"
    note_kw: dict[str, str] = {}
    notes: dict[str, int] = {}
    for p in sorted(jsonl.glob("search_contents_*.jsonl")):
        for row in _iter_jsonl(p):
            kw = row.get("source_keyword") or "?"
            if row.get("note_id"):
                note_kw[row["note_id"]] = kw
            notes[kw] = notes.get(kw, 0) + 1
    comments: dict[str, int] = {}
    for p in sorted(jsonl.glob("search_comments_*.jsonl")):
        for row in _iter_jsonl(p):
            kw = note_kw.get(row.get("note_id"))
            if kw:
                comments[kw] = comments.get(kw, 0) + 1
    return notes, comments


PAUSE_MARKER = "xiaofinance INFO - pausing"


def append_log_line(log_path: Path, message: str) -> None:
    """Pipeline-side notes written into the crawler's own log, so the run dir stays the
    single artifact the progress/detail readers parse. Same timestamp shape as
    MediaCrawler's lines; 'INFO' keeps it out of the ERROR harvesters."""
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} xiaofinance INFO - {message}\n"
    try:
        with open(log_path, "ab") as f:
            f.write(line.encode("utf-8"))
    except OSError:
        pass


# Positionally last marker wins: comment-store lines always follow their keyword's
# "Begin get note id comments", so rfind order reflects what the crawler is doing now.
PHASE_MARKERS = [
    ("Begin get note id comments", "comments"),
    ("update_xhs_note]", "note_details"),
    ("Note details:", "note_details"),
    ("Current search keyword", "search"),
    (PAUSE_MARKER, "paused"),
]
ERROR_LINE_RE = re.compile(r"^.* ERROR \([^)]+\) - (.*)$", re.MULTILINE)


def _phase(text: str) -> str:
    best, pos = "starting", -1
    for marker, name in PHASE_MARKERS:
        p = text.rfind(marker)
        if p > pos:
            best, pos = name, p
    if pos < 0 and any(h in text for h in LOGIN_HINTS):
        return "login"
    return best


def _last_error(text: str) -> str | None:
    last = None
    for m in ERROR_LINE_RE.finditer(text):
        last = m.group(1).rstrip()
    return last[:200] if last else None


def crawl_progress(run_dir: Path, keywords: list[str], target_per_keyword: int = 20) -> dict:
    """Where a running crawl has got to. MediaCrawler reports nothing to us until it
    exits, but it writes JSONL rows and a per-keyword log line as it goes — so read those
    rather than leave a 30-minute crawl looking identical to a hung one."""
    jsonl = Path(run_dir) / "xhs" / "jsonl"
    log_path = Path(run_dir) / "crawler.log"
    text = _log_text(log_path, tail_bytes=1_000_000)
    seen = KEYWORD_RE.findall(text)
    current = seen[-1].strip() if seen else None
    kw_notes, kw_comments = keyword_counts(run_dir)
    planned = keywords + [k for k in kw_notes if k not in keywords]
    # comments have no per-keyword target, so within-keyword progress needs to know how
    # many of the current keyword's notes have entered their comment fetch
    last_kw_pos = text.rfind("Current search keyword")
    comment_notes_done = text[last_kw_pos:].count("Begin get note id comments") if last_kw_pos >= 0 else 0
    try:
        last_activity_ms = int(log_path.stat().st_mtime * 1000)
    except OSError:
        last_activity_ms = None
    return {
        "notes": _count_lines(jsonl.glob("search_contents_*.jsonl")),
        "comments": _count_lines(jsonl.glob("search_comments_*.jsonl")),
        "keyword": current,
        "keyword_index": keywords.index(current) + 1 if current in keywords else None,
        "keyword_total": len(keywords),
        "captchas": text.count(CAPTCHA_MARKER),
        "per_keyword": [
            {"keyword": k, "notes": kw_notes.get(k, 0), "comments": kw_comments.get(k, 0)}
            for k in planned
        ],
        "target_per_keyword": target_per_keyword,
        "phase": _phase(text),
        "kw_comment_notes_done": comment_notes_done,
        "last_activity_ms": last_activity_ms,
        "last_error": _last_error(text),
    }


KEYWORD_TS_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) .*Current search keyword: (.+)$", re.MULTILINE
)
# Exception summary lines sit at column 0 in a traceback; log lines start with a timestamp
# and frame lines with spaces, so the anchor alone separates them.
EXC_LINE_RE = re.compile(r"^[A-Za-z_][\w.]*(?:Error|Exception|Interrupt)\b.*$", re.MULTILINE)


def _last_distinct(matches: list[str], n: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in reversed(matches):
        if x not in seen:
            seen.add(x)
            out.append(x)
        if len(out) == n:
            break
    return out[::-1]


def crawl_detail(run_dir: Path, keywords: list[str], status: str) -> dict:
    """The anatomy of one run: which keywords ran and with what yield, where it died, and
    what the crawler actually said. Computed from the run dir's artifacts on demand, and
    stored on the run row at finish because raw dirs are cleaned up after a week."""
    text = _log_text(Path(run_dir) / "crawler.log")
    kw_notes, kw_comments = keyword_counts(run_dir)

    reached: dict[str, str] = {}
    for m in KEYWORD_TS_RE.finditer(text):
        reached.setdefault(m.group(2).strip(), m.group(1))
    order = [k for k in keywords if k in reached] + [k for k in reached if k not in keywords]
    last = order[-1] if order else None

    per = []
    for k in keywords + [k for k in reached if k not in keywords]:
        if k not in reached:
            state = "not_reached"
        elif k != last or status == "success":
            state = "done"
        elif status == "running":
            state = "current"
        else:
            state = "died_here"
        per.append({
            "keyword": k, "state": state, "started_at": reached.get(k),
            "notes": kw_notes.get(k, 0), "comments": kw_comments.get(k, 0),
        })

    return {
        "keywords": per,
        "captchas": text.count(CAPTCHA_MARKER),
        "errors": _last_distinct([m.group(1).rstrip()[:200] for m in ERROR_LINE_RE.finditer(text)], 5),
        "exceptions": _last_distinct([m.group(0).rstrip()[:200] for m in EXC_LINE_RE.finditer(text)], 4),
        "log_tail": text[-4000:],
    }


class MediaCrawlerProvider:
    name = "mediacrawler"

    def __init__(self, settings=None):
        if settings is None:
            from ..config import settings
        self.settings = settings
        self.mc_dir = Path(settings.MEDIACRAWLER_DIR)
        self._proc: subprocess.Popen | None = None
        self._cancelled = False

    # ---- readiness ---------------------------------------------------------

    def preflight(self) -> list[str]:
        problems = []
        if not (self.mc_dir / "main.py").exists():
            problems.append(
                f"MediaCrawler not found at {self.mc_dir} — run `infinance setup` to fetch it"
            )
            return problems
        for rel in ("config/base_config.py", "config/xhs_config.py",
                    "media_platform/xhs/core.py", "media_platform/xhs/client.py",
                    "tools/browser_launcher.py"):
            if not (self.mc_dir / rel).exists():
                problems.append(f"MediaCrawler file missing: {rel} — re-run `infinance setup`")
        return problems

    # ---- crawling ----------------------------------------------------------

    def _cookies(self) -> str:
        return (getattr(self.settings, "XHS_COOKIES", "") or "").strip()

    def _build_cmd(self, req: SearchRequest, login_type: str) -> list[str]:
        # No --cookies here, ever: cookies travel via set_cookie_config (TR-03).
        headless = self.settings.BROWSER_HEADLESS and not req.visible_browser
        return [
            self.settings.UV_EXE, "run", "main.py",
            "--platform", "xhs", "--lt", login_type, "--type", "search",
            "--keywords", ",".join(req.keywords),
            "--save_data_option", "jsonl",
            "--save_data_path", str(req.run_dir),
            "--get_comment", "yes" if req.get_comments else "no",
            "--get_sub_comment",
            "yes" if req.get_comments and req.include_sub_comments else "no",
            "--crawler_max_notes_count", str(req.max_notes_per_keyword),
            "--max_comments_count_singlenotes", str(req.max_comments_per_note),
            "--start", "1",
            # cmd_arg.py applies this to config.CDP_HEADLESS unconditionally, clobbering
            # the patch_config() CDP_HEADLESS write with whatever this says — leaving it
            # hardcoded "no" forced every routine crawl into a visible window.
            "--headless", "yes" if headless else "no",
            "--max_concurrency_num", "1",
        ]

    def _spawn(self, cmd: list[str], log_path: Path, timeout_s: int) -> dict:
        """Run cmd in the vendor dir, appending stdout+stderr to log_path (a
        cycle is several per-keyword processes sharing one log). Watches the
        log for the CAPTCHA wall and aborts rather than let tenacity grind
        hundreds of walled retries into the account's risk score."""
        # unbuffered, or the child's stdout arrives in 8KB blocks and both the CAPTCHA
        # abort and the progress readout lag tens of requests behind reality
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
               "PYTHONUNBUFFERED": "1"}
        timed_out = risk_controlled = False
        watcher = _CaptchaWatcher(log_path)
        abort_count = getattr(self.settings, "CAPTCHA_ABORT_COUNT", 10)
        deadline = time.monotonic() + timeout_s
        with open(log_path, "ab") as logf:
            self._proc = subprocess.Popen(
                cmd, cwd=self.mc_dir, stdout=logf, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, env=env,
            )
            while True:
                try:
                    self._proc.wait(timeout=POLL_SEC)
                    break
                except subprocess.TimeoutExpired:
                    pass
                if watcher.poll() >= abort_count:
                    risk_controlled = True
                    log.warning("XHS is serving CAPTCHAs (%d) — aborting rather than keep retrying",
                                watcher.count)
                    self._kill_tree(self._proc)
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    log.warning("crawl timed out after %ds, killing process tree", timeout_s)
                    self._kill_tree(self._proc)
                    break
        proc, self._proc = self._proc, None
        return {"exit_code": proc.returncode, "timed_out": timed_out,
                "risk_controlled": risk_controlled, "captchas": watcher.poll(),
                "log_start": watcher.start}

    @staticmethod
    def _kill_tree(proc: subprocess.Popen) -> None:
        # kill children first (uv → python → Chrome): a plain kill of the
        # parent would orphan the browser on every platform
        try:
            parent = psutil.Process(proc.pid)
            procs = [*parent.children(recursive=True), parent]
        except psutil.NoSuchProcess:
            return
        for p in procs:
            try:
                p.kill()
            except psutil.NoSuchProcess:
                pass
        psutil.wait_procs(procs, timeout=30)

    def search(self, req: SearchRequest) -> RunResult:
        patch_config(self.mc_dir, self.settings,
                     browser_headless=None if not req.visible_browser else False)
        req.run_dir.mkdir(parents=True, exist_ok=True)
        log_path = req.run_dir / "crawler.log"
        self._cancelled = False

        cookies = self._cookies()
        login_type = "cookie" if cookies else "qrcode"
        if cookies:
            set_cookie_config(self.mc_dir, cookies)
        try:
            out = self._spawn(
                self._build_cmd(req, login_type), log_path, req.timeout_min * 60
            )
        finally:
            if cookies:
                clear_cookie_config(self.mc_dir)
        return RunResult(
            exit_code=out["exit_code"], timed_out=out["timed_out"],
            cancelled=self._cancelled, log_path=log_path,
            risk_controlled=out["risk_controlled"], captchas=out["captchas"],
            log_start=out["log_start"],
        )

    def cancel(self) -> None:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            self._cancelled = True
            log.info("cancelling crawl (pid %s)", proc.pid)
            self._kill_tree(proc)

    # ---- run-artifact readers, exposed on the interface --------------------

    def keyword_counts(self, run_dir: Path) -> tuple[dict[str, int], dict[str, int]]:
        return keyword_counts(run_dir)

    def crawl_progress(self, run_dir: Path, keywords: list[str],
                       target_per_keyword: int = 20) -> dict:
        return crawl_progress(run_dir, keywords, target_per_keyword)

    def crawl_detail(self, run_dir: Path, keywords: list[str], status: str) -> dict:
        return crawl_detail(run_dir, keywords, status)

    def failure_reason(self, log_path: Path, exit_code: int, start: int = 0) -> str:
        return failure_reason(log_path, exit_code, start)

    def append_log_line(self, log_path: Path, message: str) -> None:
        append_log_line(log_path, message)

    # ---- login -------------------------------------------------------------

    def login(self, timeout_min: int = 6) -> LoginOutcome:
        """Interactive login: a tiny visible crawl (3 notes, one keyword, no
        comments). QR flow unless cookies are configured; the session is cached
        in the vendor's Chrome profile, so this is needed only on first run or
        after expiry."""
        problems = self.preflight()
        if problems:
            return LoginOutcome(False, SessionState.UNKNOWN, "; ".join(problems))
        login_dir = Path(self.settings.RAW_DIR) / "login_check"
        # stale rows from an earlier probe must not count as this run's success
        if login_dir.exists():
            shutil.rmtree(login_dir, ignore_errors=True)
        result = self.search(SearchRequest(
            keywords=["美股"], run_dir=login_dir,
            max_notes_per_keyword=3, max_comments_per_note=0,
            include_sub_comments=False, timeout_min=timeout_min,
            get_comments=False, visible_browser=True,
        ))

        rows = 0
        for f in login_dir.rglob("*.jsonl"):
            try:
                with open(f, encoding="utf-8") as fh:
                    rows += sum(1 for line in fh if line.strip())
            except OSError:
                pass
        if rows > 0:
            return LoginOutcome(True, SessionState.VALID, f"fetched {rows} rows")

        log_text = _log_text(result.log_path, start=result.log_start)
        state = self.classify_log(log_text)
        if state == SessionState.UNKNOWN and result.timed_out:
            detail = f"timed out after {timeout_min} min (QR not scanned?)"
        elif result.cancelled:
            detail = "cancelled"
        elif result.risk_controlled:
            detail = f"XHS risk control: {result.captchas} CAPTCHAs — back off before retrying"
        else:
            detail = f"no data fetched (exit code {result.exit_code})"
        if state == SessionState.UNKNOWN:
            state = SessionState.EXPIRED
        return LoginOutcome(False, state, detail)

    # ---- log classification ------------------------------------------------

    def classify_log(self, log_text: str) -> SessionState:
        if any(m in log_text for m in UNAUTHORIZED_MARKERS):
            return SessionState.UNAUTHORIZED
        if any(m in log_text for m in EXPIRED_MARKERS):
            return SessionState.EXPIRED
        return SessionState.UNKNOWN

    def login_looks_required(self, log_path: Path, notes_fresh: int, start: int = 0) -> bool:
        """True if the log says the session expired, or the crawl got nothing
        and mentions login."""
        text = _log_text(log_path, start=start)
        if not text:
            return False
        if any(m in text for m in EXPIRED_MARKERS):
            return True
        if notes_fresh > 0:
            return False
        return any(h in text for h in LOGIN_HINTS)
