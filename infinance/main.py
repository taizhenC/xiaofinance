import json
import logging
import re
import secrets
import threading
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import analyze, guardrails, jobs, mentions, prices, scoreboard, scoring, session
from .config import BASE_DIR, settings
from .db import connect
from .util import now_ms

# the .env-sourced cookie value, before any UI override is applied
ENV_COOKIES = settings.XHS_COOKIES

log = logging.getLogger(__name__)

TICKER_RE = re.compile(r"^[A-Z]{1,5}$")

fetch_lock = threading.Lock()
quotes_lock = threading.Lock()
runner = jobs.JobRunner(fetch_lock)
scheduler: BackgroundScheduler | None = None


def _refresh_quotes_bg(tickers: list[str]) -> None:
    """Non-blocking: kick a daemon thread so ranking requests never wait on
    stooq.com; the UI picks up fresh quotes on its next poll."""
    if not quotes_lock.acquire(blocking=False):
        return

    def worker():
        try:
            conn = connect()
            try:
                prices.refresh_quotes(conn, tickers)
            finally:
                conn.close()
        except Exception:
            log.exception("background quote refresh failed")
        finally:
            quotes_lock.release()

    threading.Thread(target=worker, daemon=True).start()


def _scheduled_cycle() -> None:
    """Scheduler entry: guardrails first — a timer must never out-vote them."""
    conn = connect()
    try:
        block = guardrails.check_fetch_allowed(conn, settings, manual=False)
    finally:
        conn.close()
    if block:
        log.info("scheduled cycle skipped by guardrail: %s", block["reason"])
        return
    runner.start("both")


LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def is_local_bind(host: str) -> bool:
    return host in LOOPBACK_HOSTS


def check_bind_security(host: str, auth_token: str) -> None:
    """Refuse the footgun: a non-local bind exposes endpoints that crawl with
    the user's XHS account and write files. TR-02."""
    if not is_local_bind(host) and not auth_token:
        raise RuntimeError(
            f"refusing to bind {host}: set AUTH_TOKEN in .env before exposing the "
            "dashboard beyond this machine — without it, anyone on the network can "
            "trigger crawls with YOUR XHS account. Mutating requests must then send "
            "the header 'Authorization: Bearer <token>'."
        )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    check_bind_security(settings.HOST, settings.AUTH_TOKEN)
    if not is_local_bind(settings.HOST):
        log.warning(
            "dashboard exposed on %s — mutating endpoints require the AUTH_TOKEN "
            "bearer header; reads are open to the network", settings.HOST,
        )
    conn = connect()
    conn.execute(
        "UPDATE fetch_runs SET status='failed', error='stale: server restarted mid-run' WHERE status='running'"
    )
    conn.commit()
    session.apply_overrides(conn, settings)
    conn.close()
    global scheduler
    if settings.FETCH_INTERVAL_HOURS > 0:
        scheduler = BackgroundScheduler()
        scheduler.add_job(
            _scheduled_cycle,
            IntervalTrigger(hours=settings.FETCH_INTERVAL_HOURS),
            id="fetch_cycle",
        )
        scheduler.start()
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


app = FastAPI(title="infinance", lifespan=lifespan)

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def security_gate(request: Request, call_next):
    """Active only for non-local binds (TR-02): bearer token on every mutating
    API call plus a same-origin check against cross-site browser requests.
    Reads stay open by design — the token protects actions, not the view."""
    if not is_local_bind(settings.HOST) and request.method in MUTATING_METHODS \
            and request.url.path.startswith("/api/"):
        origin = request.headers.get("origin")
        if origin:
            host = request.headers.get("host", "")
            if urlsplit(origin).netloc.lower() != host.lower():
                return JSONResponse(status_code=403, content={"detail": "cross-origin request rejected"})
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not settings.AUTH_TOKEN or not secrets.compare_digest(supplied, settings.AUTH_TOKEN):
            return JSONResponse(
                status_code=401,
                content={"detail": "AUTH_TOKEN required: send 'Authorization: Bearer <token>'"},
            )
    return await call_next(request)


def _latest_analysis(conn, ticker: str):
    rows = conn.execute(
        "SELECT * FROM stock_analyses WHERE ticker=? ORDER BY generated_at_ms DESC LIMIT 5",
        (ticker,),
    ).fetchall()
    for r in rows:
        if r["status"] in ("ok", "no_api_key"):
            return r
    return rows[0] if rows else None


def _tracked_map(conn) -> dict[str, list[str]]:
    return {
        r["ticker"]: json.loads(r["custom_keywords"] or "[]")
        for r in conn.execute("SELECT ticker, custom_keywords FROM tracked_stocks")
    }


@app.get("/")
def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/api/status")
def api_status():
    conn = connect()
    try:
        last = conn.execute("SELECT * FROM fetch_runs ORDER BY id DESC LIMIT 1").fetchone()
        next_run_ms = None
        if scheduler:
            job = scheduler.get_job("fetch_cycle")
            if job and job.next_run_time:
                next_run_ms = int(job.next_run_time.timestamp() * 1000)
        return {
            "last_run": dict(last) if last else None,
            "running": runner.running,
            "job": runner.status(),
            "login_required": bool(last and last["error"] == "login_required"),
            "guardrails": guardrails.state(conn, settings),
            "scheduler": {
                "enabled": settings.FETCH_INTERVAL_HOURS > 0,
                "interval_hours": settings.FETCH_INTERVAL_HOURS,
                "next_run_at_ms": next_run_ms,
            },
            "now_ms": now_ms(),
            "window_hours": settings.FRESH_WINDOW_HOURS,
            "has_api_key": bool(settings.DEEPSEEK_API_KEY),
        }
    finally:
        conn.close()


@app.post("/api/fetch")
def api_fetch(payload: dict = Body(default={})):
    mode = payload.get("mode", "both")
    if mode not in ("both", "discovery", "tracked"):
        raise HTTPException(422, "mode must be both|discovery|tracked")
    force = bool(payload.get("force", False))
    conn = connect()
    try:
        block = guardrails.check_fetch_allowed(conn, settings, manual=True, force=force)
    finally:
        conn.close()
    if block:
        raise HTTPException(429, detail=block)
    job = runner.start(mode)
    if job is None:
        raise HTTPException(409, "a fetch cycle is already running")
    return JSONResponse(status_code=202, content={"started": True, "mode": mode, "job_id": job.id})


@app.post("/api/fetch/cancel")
def api_fetch_cancel():
    if not runner.cancel():
        raise HTTPException(404, "no fetch cycle is running")
    return {"cancelling": True}


@app.get("/api/session")
def api_session():
    conn = connect()
    try:
        return session.health(conn, settings)
    finally:
        conn.close()


@app.post("/api/session/login")
def api_session_login(payload: dict = Body(default={})):
    if runner.running:
        raise HTTPException(409, "a fetch cycle is running — wait for it or cancel it first")
    timeout_min = int(payload.get("timeout_min", 6))
    if not session.start_login(settings, timeout_min=min(max(timeout_min, 1), 30)):
        raise HTTPException(409, "a login attempt is already in progress")
    return JSONResponse(status_code=202, content={"started": True})


@app.post("/api/session/cookies")
def api_session_cookies(payload: dict = Body(...)):
    cookies = str(payload.get("cookies", "")).strip()
    if not session.cookie_format_ok(cookies):
        raise HTTPException(
            422, "cookie string must contain a1= and web_session= — copy the whole "
                 "`cookie:` header value from a logged-in browser",
        )
    conn = connect()
    try:
        session.set_cookies(conn, settings, cookies)
    finally:
        conn.close()
    return {"configured": True}


@app.delete("/api/session/cookies")
def api_session_cookies_delete():
    conn = connect()
    try:
        session.clear_cookies(conn, settings, ENV_COOKIES)
    finally:
        conn.close()
    return {"configured": bool(settings.XHS_COOKIES)}


@app.post("/api/session/config")
def api_session_config(payload: dict = Body(...)):
    if "xhs_international" not in payload:
        raise HTTPException(422, "xhs_international (bool) is required")
    value = bool(payload["xhs_international"])
    conn = connect()
    try:
        session.set_international(conn, settings, value)
    finally:
        conn.close()
    return {"xhs_international": value}


@app.get("/api/ranking")
def api_ranking():
    conn = connect()
    try:
        now = now_ms()
        dict_data = mentions.load_stock_dict()
        names = {s["ticker"]: s.get("name_cn", "") for s in dict_data["stocks"]}
        stats = scoring.compute_stats(conn, settings.fresh_window_ms, now)
        tracked = _tracked_map(conn)
        trends = scoring.compute_trends(conn)
        ranking, radar = scoring.ranking_and_radar(stats, settings.MIN_MENTIONS_FOR_ANALYSIS)

        shown = {e["ticker"] for e in ranking}
        for t in sorted(tracked):
            if t not in shown:
                e = stats.get(t) or {
                    "ticker": t, "score": 0.0, "mentions": 0,
                    "note_count": 0, "comment_count": 0,
                    "note_count_raw": 0, "comment_count_raw": 0,
                    "latest_item_ms": 0, "top_quote": None,
                }
                ranking.append(e)
                shown.add(t)

        history = scoring.score_history(conn, [e["ticker"] for e in ranking])
        quotes = {}
        if settings.ENABLE_PRICE_QUOTES:
            shown_list = [e["ticker"] for e in ranking]
            quotes = prices.get_quotes(conn, shown_list)
            if prices.quotes_need_refresh(conn, shown_list, settings.QUOTE_REFRESH_MIN * 60_000, now):
                _refresh_quotes_bg(shown_list)

        out = []
        for e in ranking:
            t = e["ticker"]
            a = _latest_analysis(conn, t)
            sc = json.loads(a["sentiment_counts"]) if a and a["sentiment_counts"] else None
            q = quotes.get(t)
            divergence = False
            if q and q.get("change_pct") is not None and sc:
                net = sc.get("bullish", 0) - sc.get("bearish", 0)
                divergence = (net >= 2 and q["change_pct"] <= -2) or (net <= -2 and q["change_pct"] >= 2)
            out.append({
                "ticker": t,
                "name_cn": names.get(t, ""),
                "score": e.get("score", 0.0),
                "note_count": e["note_count"],
                "comment_count": e["comment_count"],
                "note_count_raw": e["note_count_raw"],
                "comment_count_raw": e["comment_count_raw"],
                "mentions": e.get("mentions", 0),
                "tracked": t in tracked,
                "sentiment_counts": sc,
                "quote": {
                    "price": q["price"], "change_pct": q["change_pct"],
                    "market_date": q["market_date"], "quoted_at_ms": q["quoted_at_ms"],
                } if q else None,
                "divergence": divergence,
                "analysis_status": a["status"] if a else None,
                "analysis_age_ms": (now - a["generated_at_ms"]) if a else None,
                "latest_item_age_ms": (now - e["latest_item_ms"]) if e.get("latest_item_ms") else None,
                "trend": trends.get(t),
                "history": history.get(t, []),
            })
        radar_out = [
            {
                "ticker": e["ticker"],
                "name_cn": names.get(e["ticker"], ""),
                "mentions": e["mentions"],
                "top_quote": e["top_quote"],
                "trend": trends.get(e["ticker"]),
            }
            for e in radar if e["ticker"] not in tracked
        ]
        return {"ranking": out, "radar": radar_out, "now_ms": now}
    finally:
        conn.close()


@app.get("/api/stocks/{ticker}")
def api_stock(ticker: str):
    ticker = ticker.upper()
    if not TICKER_RE.match(ticker):
        raise HTTPException(422, "invalid ticker")
    conn = connect()
    try:
        now = now_ms()
        dict_data = mentions.load_stock_dict()
        names = {s["ticker"]: s.get("name_cn", "") for s in dict_data["stocks"]}
        a = _latest_analysis(conn, ticker)
        items = analyze.gather_items(conn, ticker, settings.fresh_window_ms, now)[:30]
        analysis = None
        if a:
            analysis = dict(a)
            for f in ("sentiment_counts", "bull_points", "bear_points", "notable_quotes"):
                analysis[f] = json.loads(analysis[f]) if analysis[f] else None
            analysis["age_ms"] = now - a["generated_at_ms"]
        return {
            "ticker": ticker,
            "name_cn": names.get(ticker, ""),
            "analysis": analysis,
            "items": [
                {
                    "type": i["type"], "text": i["text"], "likes": i["likes"],
                    "age_ms": now - i["ts"], "url": i["url"], "cluster_size": i["cluster_size"],
                }
                for i in items
            ],
            "now_ms": now,
        }
    finally:
        conn.close()


@app.get("/api/tracked")
def api_tracked_list():
    conn = connect()
    try:
        return [
            {"ticker": r["ticker"], "added_at_ms": r["added_at_ms"],
             "custom_keywords": json.loads(r["custom_keywords"] or "[]")}
            for r in conn.execute("SELECT * FROM tracked_stocks ORDER BY ticker")
        ]
    finally:
        conn.close()


@app.post("/api/tracked")
def api_tracked_add(payload: dict = Body(...)):
    ticker = str(payload.get("ticker", "")).strip().upper()
    if not TICKER_RE.match(ticker):
        raise HTTPException(422, "ticker must match ^[A-Z]{1,5}$")
    kws = payload.get("custom_keywords") or []
    if isinstance(kws, str):
        kws = [k.strip() for k in kws.split(",") if k.strip()]
    conn = connect()
    try:
        conn.execute(
            """INSERT INTO tracked_stocks(ticker, added_at_ms, custom_keywords) VALUES(?,?,?)
               ON CONFLICT(ticker) DO UPDATE SET custom_keywords=excluded.custom_keywords""",
            (ticker, now_ms(), json.dumps(kws, ensure_ascii=False)),
        )
        conn.commit()
        return {"ticker": ticker, "custom_keywords": kws}
    finally:
        conn.close()


@app.delete("/api/tracked/{ticker}")
def api_tracked_delete(ticker: str):
    ticker = ticker.upper()
    conn = connect()
    try:
        cur = conn.execute("DELETE FROM tracked_stocks WHERE ticker=?", (ticker,))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "not tracked")
        return {"deleted": ticker}
    finally:
        conn.close()


@app.get("/api/scoreboard")
def api_scoreboard():
    conn = connect()
    try:
        return scoreboard.compute_scoreboard(conn)
    finally:
        conn.close()


@app.get("/api/runs")
def api_runs(limit: int = 20):
    conn = connect()
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM fetch_runs ORDER BY id DESC LIMIT ?", (min(limit, 100),)
            )
        ]
    finally:
        conn.close()


@app.get("/api/alias_suggestions")
def api_suggestions(status: str = "pending"):
    conn = connect()
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM alias_suggestions WHERE status=? ORDER BY suggested_at_ms DESC",
                (status,),
            )
        ]
    finally:
        conn.close()


@app.post("/api/alias_suggestions/{suggestion_id}")
def api_suggestion_action(suggestion_id: int, payload: dict = Body(...)):
    action = payload.get("action")
    if action not in ("accept", "reject"):
        raise HTTPException(422, "action must be accept|reject")
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM alias_suggestions WHERE id=?", (suggestion_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "no such suggestion")
        if action == "accept":
            mentions.add_alias_to_overlay(row["term"], row["guessed_ticker"])
        conn.execute(
            "UPDATE alias_suggestions SET status=? WHERE id=?",
            ("accepted" if action == "accept" else "rejected", suggestion_id),
        )
        conn.commit()
        return {"id": suggestion_id, "status": "accepted" if action == "accept" else "rejected"}
    finally:
        conn.close()


app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
