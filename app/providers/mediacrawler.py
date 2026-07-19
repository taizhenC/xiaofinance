"""MediaCrawler adapter — the only module that knows the vendor exists.

Owns: the pinned vendor commit, config/code/UA patching, cookie delivery,
CLI construction, subprocess lifecycle (timeout + kill-tree + cancel), and the
log heuristics that classify login failures. The patch contract is hard-fail:
if upstream renames an anchor we raise with a clear message instead of
crawling with silently-wrong config.

MediaCrawler is non-commercial/learning-licensed. It stays a user-fetched,
pinned checkout under vendor/ that this adapter talks to over its CLI — it is
never imported, bundled, or redistributed.
"""

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from .base import LoginOutcome, RunResult, SearchRequest, SessionState

log = logging.getLogger(__name__)

VENDOR_REPO = "https://github.com/NanmiCoder/MediaCrawler.git"
VENDOR_PIN = "3bde9e2015f912f2e19ee63b615a0f48b9a90315"

# The only MediaCrawler internals we touch. Patcher hard-fails if upstream renames them.
# CDP mode stays off: it only changes how login/rendering happen, while the API calls the
# platform actually judges go out over httpx — so it buys nothing and costs a Chrome launch.
PATCHES = [
    ("config/xhs_config.py", "SORT_TYPE", '"time_descending"'),
    ("config/base_config.py", "ENABLE_CDP_MODE", "False"),
    ("config/base_config.py", "CRAWLER_MAX_SLEEP_SEC", "3"),
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
]

LOGIN_HINTS = ["扫码", "二维码", "请扫码", "未登录", "登录已过期", "login expired", "login failed"]
# The platform can expire a session mid-crawl, so a run that fetched notes and *then*
# died still needs a re-login. These say so outright — trust them over the note count.
EXPIRED_MARKERS = ["登录已过期", "login expired"]
# The mainland-vs-RedNote backend mismatch (and platform-gated accounts) answer every
# search with this even though the login itself succeeded.
UNAUTHORIZED_MARKERS = ["没有权限访问"]


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


def patch_config(mc_dir: Path, settings=None) -> None:
    if settings is None:
        from ..config import settings
    patches = PATCHES + [
        ("config/base_config.py", "XHS_INTERNATIONAL", _bool(settings.XHS_INTERNATIONAL)),
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
        log.info("patched %s: extended page timeouts to 120s", rel)
    _patch_user_agent(mc_dir, settings.BROWSER_USER_AGENT)


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
        for rel in ("config/base_config.py", "config/xhs_config.py", "media_platform/xhs/core.py"):
            if not (self.mc_dir / rel).exists():
                problems.append(f"MediaCrawler file missing: {rel} — re-run `infinance setup`")
        return problems

    # ---- crawling ----------------------------------------------------------

    def _cookies(self) -> str:
        return (getattr(self.settings, "XHS_COOKIES", "") or "").strip()

    def _build_cmd(self, req: SearchRequest, login_type: str) -> list[str]:
        # No --cookies here, ever: cookies travel via set_cookie_config (TR-03).
        return [
            self.settings.UV_EXE, "run", "main.py",
            "--platform", "xhs", "--lt", login_type, "--type", "search",
            "--keywords", ",".join(req.keywords),
            "--save_data_option", "jsonl",
            "--save_data_path", str(req.run_dir),
            "--get_comment", "yes" if req.get_comments else "no",
            "--get_sub_comment", "yes" if req.include_sub_comments else "no",
            "--crawler_max_notes_count", str(req.max_notes_per_keyword),
            "--max_comments_count_singlenotes", str(req.max_comments_per_note),
            "--start", "1", "--headless", "no", "--max_concurrency_num", "1",
        ]

    def _spawn(self, cmd: list[str], log_path: Path, timeout_s: int) -> tuple[int | None, bool]:
        """Run cmd in the vendor dir, stdout+stderr to log_path.
        Returns (exit_code, timed_out)."""
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        timed_out = False
        with open(log_path, "wb") as logf:
            self._proc = subprocess.Popen(
                cmd, cwd=self.mc_dir, stdout=logf, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, env=env,
            )
            try:
                self._proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                timed_out = True
                log.warning("crawl timed out after %ds, killing process tree", timeout_s)
                self._kill_tree(self._proc)
        proc, self._proc = self._proc, None
        return proc.returncode, timed_out

    @staticmethod
    def _kill_tree(proc: subprocess.Popen) -> None:
        # plain kill would orphan Chromium on Windows
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pass

    def search(self, req: SearchRequest) -> RunResult:
        patch_config(self.mc_dir, self.settings)
        req.run_dir.mkdir(parents=True, exist_ok=True)
        log_path = req.run_dir / "crawler.log"
        self._cancelled = False

        cookies = self._cookies()
        login_type = "cookie" if cookies else "qrcode"
        if cookies:
            set_cookie_config(self.mc_dir, cookies)
        try:
            exit_code, timed_out = self._spawn(
                self._build_cmd(req, login_type), log_path, req.timeout_min * 60
            )
        finally:
            if cookies:
                clear_cookie_config(self.mc_dir)
        return RunResult(
            exit_code=exit_code, timed_out=timed_out,
            cancelled=self._cancelled, log_path=log_path,
        )

    def cancel(self) -> None:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            self._cancelled = True
            log.info("cancelling crawl (pid %s)", proc.pid)
            self._kill_tree(proc)

    # ---- login -------------------------------------------------------------

    def login(self, timeout_min: int = 6) -> LoginOutcome:
        """Interactive login: a tiny visible crawl (3 notes, one keyword, no
        comments). QR flow unless cookies are configured; the session is cached
        in the vendor's browser profile (SAVE_LOGIN_STATE), so this is needed
        only on first run or after expiry."""
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
            get_comments=False,
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

        log_text = ""
        try:
            log_text = Path(result.log_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
        state = self.classify_log(log_text)
        if state == SessionState.UNKNOWN and result.timed_out:
            detail = f"timed out after {timeout_min} min (QR not scanned?)"
        elif result.cancelled:
            detail = "cancelled"
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

    def login_looks_required(self, log_path: Path, notes_fresh: int) -> bool:
        """True if the log says the session expired, or the crawl got nothing
        and mentions login."""
        if not Path(log_path).exists():
            return False
        try:
            text = Path(log_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        if any(m in text for m in EXPIRED_MARKERS):
            return True
        if notes_fresh > 0:
            return False
        return any(h in text for h in LOGIN_HINTS)
