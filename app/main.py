import json
import logging
import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import analyze, crawler_runner, mentions, pipeline, prices, scoreboard, scoring
from .config import BASE_DIR, settings
from .db import connect
from .util import now_ms

log = logging.getLogger(__name__)

TICKER_RE = re.compile(r"^[A-Z]{1,5}$")

fetch_lock = threading.Lock()
quotes_lock = threading.Lock()
scheduler: BackgroundScheduler | None = None


def _refresh_quotes_bg(tickers: list[str], symbol_overrides: dict[str, str] | None = None) -> None:
    """Non-blocking: kick a daemon thread so ranking requests never wait on
    stooq.com; the UI picks up fresh quotes on its next poll."""
    if not quotes_lock.acquire(blocking=False):
        return

    def worker():
        try:
            conn = connect()
            try:
                prices.refresh_quotes(conn, tickers, symbol_overrides=symbol_overrides)
            finally:
                conn.close()
        except Exception:
            log.exception("background quote refresh failed")
        finally:
            quotes_lock.release()

    threading.Thread(target=worker, daemon=True).start()


def _run_cycle_locked(mode: str) -> bool:
    if not fetch_lock.acquire(blocking=False):
        return False
    try:
        pipeline.run_cycle(mode)
    except Exception:
        log.exception("cycle failed")
    finally:
        fetch_lock.release()
    return True


def _scheduled_cycle() -> None:
    conn = connect()
    try:
        until = pipeline.risk_cooldown_until(conn, settings)
    finally:
        conn.close()
    if until and now_ms() < until:
        log.warning(
            "scheduled fetch skipped: XHS risk-control cooldown for another %.1f h",
            (until - now_ms()) / 3_600_000,
        )
        return
    _run_cycle_locked("both")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    conn = connect()
    conn.execute(
        "UPDATE fetch_runs SET status='failed', error='stale: server restarted mid-run' WHERE status='running'"
    )
    conn.commit()
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


def _latest_analysis(conn, ticker: str):
    rows = conn.execute(
        "SELECT * FROM stock_analyses WHERE ticker=? ORDER BY generated_at_ms DESC LIMIT 5",
        (ticker,),
    ).fetchall()
    for r in rows:
        if r["status"] in ("ok", "no_api_key"):
            return r
    return rows[0] if rows else None


def _with_progress(row) -> dict:
    """A running crawl gets its live counts attached; a finished one already has them."""
    r = dict(row)
    r.pop("detail", None)  # multi-KB blob; served by /api/runs/{id}/detail on demand
    if r.get("status") == "running" and r.get("raw_dir"):
        r["progress"] = crawler_runner.crawl_progress(
            Path(r["raw_dir"]), [k for k in (r.get("keywords") or "").split(",") if k],
            settings.MAX_NOTES_PER_KEYWORD,
        )
    return r


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
        cooldown_ms = pipeline.risk_cooldown_until(conn, settings)
        if cooldown_ms and cooldown_ms <= now_ms():
            cooldown_ms = None
        return {
            "last_run": _with_progress(last) if last else None,
            "running": fetch_lock.locked(),
            "login_required": bool(last and last["error"] == "login_required"),
            "scheduler": {
                "enabled": settings.FETCH_INTERVAL_HOURS > 0,
                "interval_hours": settings.FETCH_INTERVAL_HOURS,
                "next_run_at_ms": next_run_ms,
                "cooldown_until_ms": cooldown_ms,
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
    if not fetch_lock.acquire(blocking=False):
        raise HTTPException(409, "a fetch cycle is already running")

    def worker():
        try:
            pipeline.run_cycle(mode)
        except Exception:
            log.exception("cycle failed")
        finally:
            fetch_lock.release()

    threading.Thread(target=worker, daemon=True).start()
    return JSONResponse(status_code=202, content={"started": True, "mode": mode})


@app.get("/api/ranking")
def api_ranking():
    conn = connect()
    try:
        now = now_ms()
        dict_data = mentions.load_stock_dict()
        names = {s["ticker"]: s.get("name_cn", "") for s in dict_data["stocks"]}
        sectors = {s["ticker"]: s.get("sector", "") for s in dict_data["stocks"]}
        entries = {s["ticker"]: s for s in dict_data["stocks"]}
        classes = mentions.asset_classes(dict_data)
        indexes = mentions.index_tickers(dict_data)
        investments = mentions.investment_tickers(dict_data)
        non_stocks = indexes | investments
        stats = scoring.compute_stats(conn, settings.fresh_window_ms, now, indexes=non_stocks)
        context = scoring.compute_stats(conn, settings.context_window_ms, now, indexes=non_stocks)
        tracked = _tracked_map(conn)
        trends = scoring.compute_trends(conn)
        ranking, _ = scoring.ranking_and_radar(stats, settings.MIN_MENTIONS_FOR_ANALYSIS, non_stocks)
        index_ranking = scoring.index_board(stats, indexes, settings.MIN_MENTIONS_FOR_ANALYSIS)
        investment_ranking = scoring.index_board(
            stats, investments, settings.MIN_MENTIONS_FOR_ANALYSIS
        )
        # Sectors and radar read the wider window: a sector that produces one post a day
        # is invisible in 24h, and calling that "no interest" would be a measurement
        # artifact rather than a finding.
        breakdown = scoring.sector_breakdown(context, sectors, exclude=non_stocks)
        for s in breakdown:
            if s["leader"]:
                s["leader"]["name_cn"] = names.get(s["leader"]["ticker"], "")

        shown = (
            {e["ticker"] for e in ranking}
            | {e["ticker"] for e in index_ranking}
            | {e["ticker"] for e in investment_ranking}
        )
        for t in sorted(tracked):
            if t not in shown:
                e = stats.get(t) or {
                    "ticker": t, "score": 0.0, "mentions": 0,
                    "note_count": 0, "comment_count": 0,
                    "note_count_raw": 0, "comment_count_raw": 0,
                    "focused_mentions": 0,
                    "latest_item_ms": 0, "top_quote": None,
                }
                # a tracked ticker always shows, but on the board it belongs to
                if t in investments:
                    investment_ranking.append(e)
                elif t in indexes:
                    index_ranking.append(e)
                else:
                    ranking.append(e)
                shown.add(t)

        boarded = (
            [e["ticker"] for e in ranking]
            + [e["ticker"] for e in investment_ranking]
            + [e["ticker"] for e in index_ranking]
        )
        history = scoring.score_history(conn, boarded)
        quotes = {}
        if settings.ENABLE_PRICE_QUOTES:
            quoteable = [
                t for t in boarded
                if classes.get(t) in ("stock", "index") or entries.get(t, {}).get("quote_symbol")
            ]
            symbol_overrides = {
                t: entries[t]["quote_symbol"] for t in quoteable
                if entries.get(t, {}).get("quote_symbol")
            }
            quotes = prices.get_quotes(conn, quoteable)
            if prices.quotes_need_refresh(
                conn, quoteable, settings.QUOTE_REFRESH_MIN * 60_000, now
            ):
                _refresh_quotes_bg(quoteable, symbol_overrides)

        def card(e: dict) -> dict:
            t = e["ticker"]
            a = _latest_analysis(conn, t)
            sc = json.loads(a["sentiment_counts"]) if a and a["sentiment_counts"] else None
            q = quotes.get(t)
            divergence = False
            if q and q.get("change_pct") is not None and sc:
                net = sc.get("bullish", 0) - sc.get("bearish", 0)
                divergence = (net >= 2 and q["change_pct"] <= -2) or (net <= -2 and q["change_pct"] >= 2)
            return {
                "ticker": t,
                "name_cn": names.get(t, ""),
                "sector": sectors.get(t, ""),
                "asset_class": classes.get(t, "stock"),
                "score": e.get("score", 0.0),
                "note_count": e["note_count"],
                "comment_count": e["comment_count"],
                "note_count_raw": e["note_count_raw"],
                "comment_count_raw": e["comment_count_raw"],
                "mentions": e.get("mentions", 0),
                "focused_mentions": e.get("focused_mentions", 0),
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
            }

        out = [card(e) for e in ranking]
        indexes_out = [card(e) for e in index_ranking]
        investments_out = [card(e) for e in investment_ranking]
        radar = scoring.radar_entries(context, exclude=shown | set(tracked) | non_stocks)
        radar_out = [
            {
                "ticker": e["ticker"],
                "name_cn": names.get(e["ticker"], ""),
                "sector": sectors.get(e["ticker"], ""),
                "mentions": e["mentions"],
                "top_quote": e["top_quote"],
                "trend": trends.get(e["ticker"]),
            }
            for e in radar
        ]
        return {
            "ranking": out,
            "indexes": indexes_out,
            "investments": investments_out,
            "topics": mentions.topic_breakdown(
                conn, dict_data, settings.fresh_window_ms, now
            ),
            "radar": radar_out,
            "sectors": breakdown,
            "windows": {
                "board_hours": settings.FRESH_WINDOW_HOURS,
                "context_hours": settings.CONTEXT_WINDOW_HOURS,
            },
            "now_ms": now,
        }
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
        classes = mentions.asset_classes(dict_data)
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
            "asset_class": classes.get(ticker, "stock"),
            "topics": sorted({
                tag for item in items
                for tag in mentions.extract_topic_tags(item["text"], dict_data)
            }),
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
    raw_kws = payload.get("custom_keywords", [])
    if raw_kws is None:
        kws = []
    elif isinstance(raw_kws, str):
        kws = [k.strip() for k in raw_kws.split(",") if k.strip()]
    elif isinstance(raw_kws, list) and all(isinstance(k, str) for k in raw_kws):
        kws = [k.strip() for k in raw_kws if k.strip()]
    else:
        raise HTTPException(422, "custom_keywords must be a string or list of strings")
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
def api_runs(limit: int = Query(default=20, ge=1)):
    conn = connect()
    try:
        return [
            _with_progress(r)
            for r in conn.execute(
                "SELECT * FROM fetch_runs ORDER BY id DESC LIMIT ?", (min(limit, 100),)
            )
        ]
    finally:
        conn.close()


@app.get("/api/runs/{run_id}/detail")
def api_run_detail(run_id: int):
    """Per-keyword timeline and failure anatomy. Live from the raw dir while it exists,
    else the snapshot stored when the run finished."""
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM fetch_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise HTTPException(404, "no such run")
        r = dict(row)
        kws = [k for k in (r.get("keywords") or "").split(",") if k]
        detail = None
        if r.get("raw_dir") and Path(r["raw_dir"]).exists():
            detail = crawler_runner.crawl_detail(Path(r["raw_dir"]), kws, r["status"])
        elif r.get("detail"):
            detail = json.loads(r["detail"])
        return {
            "id": r["id"], "mode": r["mode"], "status": r["status"], "error": r["error"],
            "started_at_ms": r["started_at_ms"], "finished_at_ms": r["finished_at_ms"],
            "notes_fresh": r["notes_fresh"], "comments_fresh": r["comments_fresh"],
            "detail": detail,
        }
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
