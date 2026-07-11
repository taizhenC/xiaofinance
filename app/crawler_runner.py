import logging
import os
import re
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

# The only MediaCrawler internals we touch. Patcher hard-fails if upstream renames them.
PATCHES = [
    ("config/xhs_config.py", "SORT_TYPE", '"time_descending"'),
    ("config/base_config.py", "ENABLE_CDP_MODE", "False"),
    ("config/base_config.py", "CRAWLER_MAX_SLEEP_SEC", "3"),
]

LOGIN_HINTS = ["扫码", "二维码", "请扫码", "未登录", "登录已过期", "login expired", "login failed"]


def patch_config(mc_dir: Path) -> None:
    for rel, var, value in PATCHES:
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


def run_crawl(keywords: list[str], run_dir: Path, settings) -> dict:
    mc_dir = Path(settings.MEDIACRAWLER_DIR)
    patch_config(mc_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "crawler.log"

    cmd = [
        settings.UV_EXE, "run", "main.py",
        "--platform", "xhs", "--lt", "qrcode", "--type", "search",
        "--keywords", ",".join(keywords),
        "--save_data_option", "jsonl",
        "--save_data_path", str(run_dir),
        "--get_comment", "yes", "--get_sub_comment", "no",
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
    """Heuristic: crawl produced zero fresh notes AND the log mentions QR/login."""
    if notes_fresh > 0 or not Path(log_path).exists():
        return False
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return any(h in text for h in LOGIN_HINTS)
