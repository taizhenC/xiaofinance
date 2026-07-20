"""infinance — unified, cross-platform CLI.

Replaces the Windows-only PowerShell scripts:
    infinance setup    one-time install: vendor clone+pin, deps, browser, .env
    infinance login    (re)do the XHS login in a visible browser
    infinance run      start the dashboard server
    infinance doctor   diagnose common breakage
    infinance smoke    smallest possible real crawl (3 notes) to test the session
    infinance cycle    run one pipeline cycle without the server

Everything here is plain Python + subprocess so the same happy path works on
Windows, macOS and Linux.
"""

import argparse
import shutil
import socket
import subprocess
import sys
from pathlib import Path

OK = "[ok]"
BAD = "[!!]"
INFO = "[--]"


def _print_step(i: int, n: int, msg: str) -> None:
    print(f"\n[{i}/{n}] {msg}", flush=True)


def _run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> int:
    """Run a subprocess with inherited stdio so the user sees real progress."""
    print(f"    $ {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, cwd=cwd)
    if check and proc.returncode != 0:
        raise SystemExit(f"{BAD} command failed with exit code {proc.returncode}: {' '.join(cmd)}")
    return proc.returncode


def _uv_ok() -> bool:
    return shutil.which("uv") is not None


def _require_uv() -> None:
    if _uv_ok():
        return
    print(f"{BAD} `uv` was not found on PATH — it manages the crawler's Python environment.")
    print("    Install it, then re-run this command:")
    if sys.platform == "win32":
        print('      powershell -c "irm https://astral.sh/uv/install.ps1 | iex"')
    else:
        print("      curl -LsSf https://astral.sh/uv/install.sh | sh")
    raise SystemExit(1)


# ---------------------------------------------------------------- setup ----


def cmd_setup(args) -> int:
    from .config import DATA_HOME, PACKAGE_DIR, settings
    from .providers.mediacrawler import VENDOR_PIN, VENDOR_REPO

    mc_dir = Path(settings.MEDIACRAWLER_DIR)
    steps = 5
    print("== infinance setup ==")
    print(f"   data home: {DATA_HOME}")

    _print_step(1, steps, "checking prerequisites (git, uv)")
    if shutil.which("git") is None:
        print(f"{BAD} `git` not found — install it from https://git-scm.com and re-run.")
        return 1
    _require_uv()
    print(f"{OK} git and uv available")

    _print_step(2, steps, f"fetching MediaCrawler (pinned {VENDOR_PIN[:12]})")
    print("    MediaCrawler is fetched to your machine under its own non-commercial")
    print("    license and is never bundled with infinance. Keep usage personal.")
    if not (mc_dir / ".git").exists():
        mc_dir.parent.mkdir(parents=True, exist_ok=True)
        _run(["git", "clone", VENDOR_REPO, str(mc_dir)])
    _run(["git", "-C", str(mc_dir), "fetch", "--quiet", "--depth", "200", "origin"])
    rc = _run(
        ["git", "-C", str(mc_dir), "-c", "advice.detachedHead=false",
         "checkout", "--quiet", VENDOR_PIN],
        check=False,
    )
    if rc != 0:
        _run(["git", "-C", str(mc_dir), "fetch", "--quiet", "--unshallow", "origin"], check=False)
        _run(["git", "-C", str(mc_dir), "-c", "advice.detachedHead=false",
              "checkout", "--quiet", VENDOR_PIN])
    print(f"{OK} MediaCrawler pinned at {VENDOR_PIN[:12]}")

    _print_step(3, steps, "installing crawler dependencies (uv sync)")
    _run(["uv", "sync"], cwd=mc_dir)

    _print_step(4, steps, "installing Playwright Chromium (login browser)")
    _run(["uv", "run", "playwright", "install", "chromium"], cwd=mc_dir)

    _print_step(5, steps, "scaffolding configuration")
    env_path = DATA_HOME / ".env"
    if not env_path.exists():
        template = PACKAGE_DIR / "data" / "env.example"
        env_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(template, env_path)
        print(f"{OK} created {env_path} — add your DEEPSEEK_API_KEY there (optional)")
    else:
        print(f"{OK} {env_path} already exists, left untouched")
    (DATA_HOME / "data").mkdir(parents=True, exist_ok=True)

    print("\nSetup complete. Next steps:")
    print("  1. infinance login    (scan the QR code with your XHS app, once)")
    print("  2. infinance run      (open the dashboard, click 立即抓取)")
    return 0


# ---------------------------------------------------------------- login ----


def cmd_login(args) -> int:
    from .providers import SessionState, get_provider

    provider = get_provider()
    problems = provider.preflight()
    if problems:
        for p in problems:
            print(f"{BAD} {p}")
        return 1
    print("A browser window will open with a QR code — scan it with the XHS app.")
    print(f"(waiting up to {args.timeout} minutes; the session is cached afterwards)")
    outcome = provider.login(timeout_min=args.timeout)
    if outcome.ok:
        print(f"{OK} login verified — {outcome.detail}. You can start the dashboard: infinance run")
        return 0
    print(f"{BAD} login not verified: {outcome.detail}")
    if outcome.state == SessionState.UNAUTHORIZED:
        print("    The platform refused the search API for this session (没有权限访问). In order:")
        print("    1. Account registered on rednote.com (international app)? Set XHS_INTERNATIONAL=true in .env —")
        print("       it is a separate backend from mainland xiaohongshu.com.")
        print("    2. Same account searches fine in your normal browser? Paste that browser's cookie")
        print("       string into XHS_COOKIES in .env (must include a1= and web_session=).")
        print("    3. Neither helps? The account itself is gated — verify the phone number, use the app")
        print("       normally for a few days, and stop retrying (failed logins raise the risk score).")
    elif outcome.state == SessionState.EXPIRED:
        print("    The QR was likely not scanned in time, or the session expired — run `infinance login` again.")
    return 1


# ------------------------------------------------------------------ run ----


def cmd_run(args) -> int:
    import uvicorn

    from .config import settings
    from .main import check_bind_security, is_local_bind

    host = args.host or settings.HOST
    port = args.port or settings.PORT
    try:
        check_bind_security(host, settings.AUTH_TOKEN)
    except RuntimeError as e:
        print(f"{BAD} {e}")
        return 1
    if not is_local_bind(host):
        # settings.HOST drives the in-app security gate; keep it consistent
        # with the actual bind when --host was passed on the command line
        settings.HOST = host
        print(f"{BAD} WARNING: dashboard exposed on {host} — mutating endpoints require "
              "the AUTH_TOKEN bearer header; anyone on the network can read the data.")
    print(f"infinance dashboard → http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}")
    # one worker, always: fetch mutual-exclusion and the scheduler are in-process
    uvicorn.run("infinance.main:app", host=host, port=port, workers=1, log_level="info")
    return 0


# ---------------------------------------------------------------- cycle ----


def cmd_cycle(args) -> int:
    import json
    import logging

    from . import pipeline

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    result = pipeline.run_cycle(args.mode, skip_crawl=args.skip_crawl)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


# ---------------------------------------------------------------- smoke ----


def cmd_smoke(args) -> int:
    from .config import settings
    from .providers import SearchRequest, SessionState, get_provider

    provider = get_provider()
    problems = provider.preflight()
    if problems:
        for p in problems:
            print(f"{BAD} {p}")
        return 1
    auth = "cookie session from .env" if settings.XHS_COOKIES.strip() else "QR login"
    print(f"--- smoke crawl: 美股 × 3 notes, auth = {auth} ---")
    out_dir = Path(settings.RAW_DIR) / "smoke"
    result = provider.search(SearchRequest(
        keywords=["美股"], run_dir=out_dir, max_notes_per_keyword=3,
        max_comments_per_note=0, include_sub_comments=False,
        timeout_min=settings.CRAWL_TIMEOUT_MIN, get_comments=False,
    ))
    print(f"exit code: {result.exit_code} | timed out: {result.timed_out}")
    files = list(out_dir.rglob("*.jsonl"))
    total = 0
    for f in files:
        with open(f, encoding="utf-8") as fh:
            total += sum(1 for line in fh if line.strip())
    if total:
        print(f"{OK} SUCCESS: {total} rows across {len(files)} jsonl file(s) in {out_dir}")
        return 0
    print(f"{BAD} NO DATA. Check the log: {result.log_path}")
    try:
        log_text = Path(result.log_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        log_text = ""
    state = provider.classify_log(log_text)
    if state == SessionState.UNAUTHORIZED:
        print("    >>> XHS refused the search API for this session (risk control).")
        print("    >>> If xiaohongshu.com search works in your browser, set XHS_COOKIES in .env;")
        print("    >>> if your account is on rednote.com, set XHS_INTERNATIONAL=true.")
    elif state == SessionState.EXPIRED:
        print("    >>> Session expired — run `infinance login`.")
    return 1


# --------------------------------------------------------------- doctor ----


def _check(ok: bool, label: str, hint: str = "") -> bool:
    print(f"{OK if ok else BAD} {label}")
    if not ok and hint:
        print(f"     fix: {hint}")
    return ok


def _playwright_cache_dir() -> Path:
    if sys.platform == "win32":
        import os

        return Path(os.environ.get("LOCALAPPDATA", "~")).expanduser() / "ms-playwright"
    if sys.platform == "darwin":
        return Path("~/Library/Caches/ms-playwright").expanduser()
    return Path("~/.cache/ms-playwright").expanduser()


def cmd_doctor(args) -> int:
    from .config import DATA_HOME, settings
    from .db import connect
    from .migrations import LATEST_VERSION, get_version
    from .providers.mediacrawler import VENDOR_PIN

    print("== infinance doctor ==")
    print(f"{INFO} platform: {sys.platform}, python {sys.version.split()[0]}")
    print(f"{INFO} data home: {DATA_HOME}")
    ok = True

    ok &= _check(sys.version_info >= (3, 11), "Python >= 3.11")
    ok &= _check(_uv_ok(), "uv on PATH", "install from https://docs.astral.sh/uv/")

    mc_dir = Path(settings.MEDIACRAWLER_DIR)
    vendor_ok = (mc_dir / "main.py").exists()
    ok &= _check(vendor_ok, f"MediaCrawler present at {mc_dir}", "run `infinance setup`")
    if vendor_ok:
        try:
            head = subprocess.run(
                ["git", "-C", str(mc_dir), "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=15,
            ).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            head = ""
        ok &= _check(
            head == VENDOR_PIN,
            f"MediaCrawler pinned at {VENDOR_PIN[:12]}",
            "run `infinance setup` to re-pin (upstream churn breaks the patch contract)",
        )
        ok &= _check((mc_dir / ".venv").exists(), "crawler dependencies installed",
                     "run `infinance setup`")

    pw = _playwright_cache_dir()
    has_chromium = pw.exists() and any(p.name.startswith("chromium") for p in pw.iterdir())
    ok &= _check(has_chromium, "Playwright Chromium installed", "run `infinance setup`")

    env_path = DATA_HOME / ".env"
    _check(env_path.exists(), f".env present at {env_path}",
           "run `infinance setup` to scaffold it")
    if settings.DEEPSEEK_API_KEY:
        print(f"{OK} LLM key configured ({settings.LLM_MODEL} @ {settings.LLM_BASE_URL})")
    else:
        print(f"{INFO} no LLM key — cards will show top quotes instead of AI summaries")
    cookies = settings.XHS_COOKIES.strip()
    if cookies:
        valid = "a1=" in cookies and "web_session=" in cookies
        # never print the value: it is a live session token
        _check(valid, "XHS_COOKIES format (a1= and web_session= present)",
               "copy the full `cookie:` header value from a logged-in browser")

    try:
        conn = connect()
        version = get_version(conn)
        last = conn.execute("SELECT * FROM fetch_runs ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        _check(version == LATEST_VERSION, f"database schema v{version} (latest v{LATEST_VERSION})")
        if last is not None:
            when = last["started_at_ms"]
            print(f"{INFO} last run: #{last['id']} {last['mode']} → {last['status']}"
                  f" ({last['error'] or 'no error'})")
            if last["error"] == "login_required":
                print("     fix: run `infinance login` (or paste fresh XHS_COOKIES)")
            _ = when
        else:
            print(f"{INFO} no fetch runs yet — run `infinance run` and click 立即抓取")
    except Exception as e:
        ok = _check(False, f"database opens ({settings.DB_PATH})", str(e))

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind((settings.HOST, settings.PORT))
        s.close()
        print(f"{OK} port {settings.PORT} free on {settings.HOST}")
    except OSError:
        print(f"{INFO} port {settings.PORT} busy on {settings.HOST} — dashboard already running,"
              f" or set PORT in .env")

    print()
    if ok:
        print(f"{OK} no blocking problems found")
        return 0
    print(f"{BAD} problems found — see fixes above")
    return 1


# ----------------------------------------------------------------- main ----


def main(argv: list[str] | None = None) -> int:
    # Windows pipes default to the legacy codepage, which mangles the Chinese
    # strings this tool necessarily prints (keywords, XHS error markers).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    parser = argparse.ArgumentParser(
        prog="infinance",
        description="XHS (小红书) US-stock sentiment dashboard — local, personal, your own account.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("setup", help="one-time install: vendor, dependencies, browser, .env")

    p_login = sub.add_parser("login", help="(re)do the XHS login in a visible browser")
    p_login.add_argument("--timeout", type=int, default=6, metavar="MIN",
                         help="minutes to wait for the QR scan (default 6)")

    p_run = sub.add_parser("run", help="start the dashboard server")
    p_run.add_argument("--host", default=None)
    p_run.add_argument("--port", type=int, default=None)

    p_cycle = sub.add_parser("cycle", help="run one crawl+analysis cycle without the server")
    p_cycle.add_argument("--mode", choices=["both", "discovery", "tracked"], default="both")
    p_cycle.add_argument("--skip-crawl", action="store_true",
                         help="re-run analysis on existing data")

    sub.add_parser("smoke", help="smallest possible real crawl (3 notes) to test the session")
    sub.add_parser("doctor", help="diagnose common breakage")

    args = parser.parse_args(argv)
    handlers = {
        "setup": cmd_setup, "login": cmd_login, "run": cmd_run,
        "cycle": cmd_cycle, "smoke": cmd_smoke, "doctor": cmd_doctor,
    }
    try:
        return handlers[args.command](args)
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
