import logging
import os
import re
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

# The only MediaCrawler internals we touch. Patcher hard-fails if upstream renames them.
# CDP mode stays off: it only changes how login/rendering happen, while the API calls the
# platform actually judges go out over httpx — so it buys nothing and costs a Chrome launch.
PATCHES = [
    ("config/xhs_config.py", "SORT_TYPE", '"time_descending"'),
    ("config/base_config.py", "ENABLE_CDP_MODE", "False"),
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
]

LOGIN_HINTS = ["扫码", "二维码", "请扫码", "未登录", "登录已过期", "login expired", "login failed"]
# The platform can expire a session mid-crawl, so a run that fetched notes and *then*
# died still needs a re-login. These say so outright — trust them over the note count.
EXPIRED_MARKERS = ["登录已过期", "login expired"]


def _bool(v) -> str:
    return "True" if v else "False"


def patch_config(mc_dir: Path, settings=None) -> None:
    if settings is None:
        from .config import settings
    patches = PATCHES + [
        ("config/base_config.py", "XHS_INTERNATIONAL", _bool(settings.XHS_INTERNATIONAL)),
        ("config/base_config.py", "CRAWLER_MAX_SLEEP_SEC", str(settings.CRAWL_SLEEP_SEC)),
    ]
    for rel, var, value in patches:
        path = mc_dir / rel
        if not path.exists():
            raise RuntimeError(f"MediaCrawler file missing: {path} — run scripts\\setup.ps1")
        text = path.read_text(encoding="utf-8")
        pattern = re.compile(rf"^({re.escape(var)}\s*=\s*).*$", re.MULTILINE)
        if not pattern.search(text):
            raise RuntimeError(
                f"MediaCrawler config variable {var} not found in {rel} — upstream layout changed; "
                f"update PATCHES in crawler_runner.py or re-pin the vendor commit"
            )
        new_text = pattern.sub(lambda m: m.group(1) + value, text, count=1)
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
            log.info("patched %s: %s = %s", rel, var, value)
    for rel, anchor, replacement in CODE_PATCHES:
        path = mc_dir / rel
        if not path.exists():
            raise RuntimeError(f"MediaCrawler file missing: {path} — run scripts\\setup.ps1")
        text = path.read_text(encoding="utf-8")
        if replacement in text:
            continue
        if anchor not in text:
            raise RuntimeError(
                f"MediaCrawler anchor line not found in {rel} — upstream layout changed; "
                f"update CODE_PATCHES in crawler_runner.py or re-pin the vendor commit"
            )
        path.write_text(text.replace(anchor, replacement, 1), encoding="utf-8")
        log.info("patched %s: %s", rel, replacement.strip().splitlines()[0])
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


class _CaptchaWatcher:
    """Counts CAPTCHA lines as the log grows, reading only what is new each poll."""

    def __init__(self, log_path: Path):
        self.path = Path(log_path)
        self.pos = 0
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


def _kill_tree(proc) -> None:
    # a plain kill would orphan Chromium on Windows
    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass


POLL_SEC = 5


def run_crawl(keywords: list[str], run_dir: Path, settings, get_comments: bool = True) -> dict:
    mc_dir = Path(settings.MEDIACRAWLER_DIR)
    patch_config(mc_dir, settings)
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "crawler.log"

    cookies = (getattr(settings, "XHS_COOKIES", "") or "").strip()
    login_args = ["--lt", "cookie", "--cookies", cookies] if cookies else ["--lt", "qrcode"]

    cmd = [
        settings.UV_EXE, "run", "main.py",
        "--platform", "xhs", *login_args, "--type", "search",
        "--keywords", ",".join(keywords),
        "--save_data_option", "jsonl",
        "--save_data_path", str(run_dir),
        "--get_comment", "yes" if get_comments else "no",
        "--get_sub_comment", "yes" if get_comments and settings.ENABLE_SUB_COMMENTS else "no",
        "--crawler_max_notes_count", str(settings.MAX_NOTES_PER_KEYWORD),
        "--max_comments_count_singlenotes", str(settings.MAX_COMMENTS_PER_NOTE),
        "--start", "1", "--headless", "no", "--max_concurrency_num", "1",
    ]
    # unbuffered, or the child's stdout arrives in 8KB blocks and both the CAPTCHA abort
    # and the progress readout lag tens of requests behind what the crawler is really doing
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}

    timed_out = risk_controlled = False
    watcher = _CaptchaWatcher(log_path)
    deadline = time.monotonic() + settings.CRAWL_TIMEOUT_MIN * 60
    with open(log_path, "wb") as logf:
        proc = subprocess.Popen(
            cmd, cwd=mc_dir, stdout=logf, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, env=env,
        )
        while True:
            try:
                proc.wait(timeout=POLL_SEC)
                break
            except subprocess.TimeoutExpired:
                pass
            if watcher.poll() >= settings.CAPTCHA_ABORT_COUNT:
                risk_controlled = True
                log.warning("XHS is serving CAPTCHAs (%d) — aborting rather than keep retrying",
                            watcher.count)
                _kill_tree(proc)
                break
            if time.monotonic() >= deadline:
                timed_out = True
                log.warning("crawl timed out after %d min, killing process tree",
                            settings.CRAWL_TIMEOUT_MIN)
                _kill_tree(proc)
                break

    return {"exit_code": proc.returncode, "timed_out": timed_out, "log_path": log_path,
            "risk_controlled": risk_controlled, "captchas": watcher.poll()}


CAPTCHA_MARKER = "CAPTCHA appeared"
NETWORK_MARKERS = ["ConnectError", "ConnectTimeout", "ReadTimeout", "ProxyError", "SSLError"]
KEYWORD_RE = re.compile(r"Current search keyword: (.+)")


def _log_text(log_path: Path, tail_bytes: int | None = None) -> str:
    """Reads the tail by default: a risk-controlled run writes an error line per retry and
    a full note dump per result, so these logs run to megabytes."""
    path = Path(log_path)
    if not path.exists():
        return ""
    try:
        with open(path, "rb") as f:
            if tail_bytes:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - tail_bytes))
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def failure_reason(log_path: Path, exit_code: int) -> str:
    """MediaCrawler exits 1 for every unhandled error alike, so the exit code on its own
    tells you nothing about whether to retry, re-login, or back off. Name the cause."""
    text = _log_text(log_path)
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


def crawl_progress(run_dir: Path, keywords: list[str]) -> dict:
    """Where a running crawl has got to. MediaCrawler reports nothing to us until it
    exits, but it writes JSONL rows and a per-keyword log line as it goes — so read those
    rather than leave a 30-minute crawl looking identical to a hung one."""
    jsonl = Path(run_dir) / "xhs" / "jsonl"
    text = _log_text(Path(run_dir) / "crawler.log", tail_bytes=1_000_000)
    seen = KEYWORD_RE.findall(text)
    current = seen[-1].strip() if seen else None
    return {
        "notes": _count_lines(jsonl.glob("search_contents_*.jsonl")),
        "comments": _count_lines(jsonl.glob("search_comments_*.jsonl")),
        "keyword": current,
        "keyword_index": keywords.index(current) + 1 if current in keywords else None,
        "keyword_total": len(keywords),
        "captchas": text.count(CAPTCHA_MARKER),
    }


def login_looks_required(log_path: Path, notes_fresh: int) -> bool:
    """True if the log says the session expired, or the crawl got nothing and mentions login."""
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
