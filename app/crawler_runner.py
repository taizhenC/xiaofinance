import logging
import os
import re
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

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


def run_crawl(keywords: list[str], run_dir: Path, settings) -> dict:
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
        "--get_comment", "yes",
        "--get_sub_comment", "yes" if settings.ENABLE_SUB_COMMENTS else "no",
        "--crawler_max_notes_count", str(settings.MAX_NOTES_PER_KEYWORD),
        "--max_comments_count_singlenotes", str(settings.MAX_COMMENTS_PER_NOTE),
        "--start", "1", "--headless", "no", "--max_concurrency_num", "1",
    ]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}

    timed_out = False
    with open(log_path, "wb") as logf:
        proc = subprocess.Popen(
            cmd, cwd=mc_dir, stdout=logf, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, env=env,
        )
        try:
            proc.wait(timeout=settings.CRAWL_TIMEOUT_MIN * 60)
        except subprocess.TimeoutExpired:
            timed_out = True
            log.warning("crawl timed out after %d min, killing process tree", settings.CRAWL_TIMEOUT_MIN)
            # plain kill would orphan Chromium on Windows
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                pass

    return {"exit_code": proc.returncode, "timed_out": timed_out, "log_path": log_path}


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
