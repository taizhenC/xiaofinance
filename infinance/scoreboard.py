"""Crowd hit-rate scoreboard: was the XHS crowd's lean actually right?

A "call" is the last clearly-leaning ok-analysis per (ticker, UTC day):
net = bullish - bearish, |net| >= LEAN_MIN. Each call is scored against the
subsequent price moves from price_history — the first close after the call's
baseline session (1d) and the first close >= 7 calendar days later (7d).
Calls too recent to have an outcome stay pending and don't count."""

import json
from datetime import UTC, datetime, timedelta

from .util import now_ms

LEAN_MIN = 2
WINDOW_DAYS = 30
MAX_CALLS_SHOWN = 50


def _utc_date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).date().isoformat()


def _collect_calls(conn, since_ms: int) -> list[dict]:
    calls: dict[tuple[str, str], dict] = {}
    for r in conn.execute(
        """SELECT ticker, generated_at_ms, sentiment_counts FROM stock_analyses
           WHERE status='ok' AND generated_at_ms >= ? ORDER BY generated_at_ms""",
        (since_ms,),
    ):
        try:
            sc = json.loads(r["sentiment_counts"] or "{}")
        except json.JSONDecodeError:
            continue
        net = sc.get("bullish", 0) - sc.get("bearish", 0)
        if abs(net) < LEAN_MIN:
            continue
        day = _utc_date(r["generated_at_ms"])
        calls[(r["ticker"], day)] = {
            "ticker": r["ticker"], "date": day,
            "dir": "up" if net > 0 else "down", "net": net,
        }
    return list(calls.values())


def _score_call(call: dict, closes: list[tuple[str, float]]) -> dict:
    """closes: [(date, close)] ascending for the call's ticker."""
    out = {**call, "move_1d_pct": None, "correct_1d": None,
           "move_7d_pct": None, "correct_7d": None}
    baseline = None
    for d, c in closes:
        if d <= call["date"]:
            baseline = (d, c)
        else:
            break
    if baseline is None or baseline[1] == 0:
        return out
    base_date, base_close = baseline
    want_up = call["dir"] == "up"

    after = [(d, c) for d, c in closes if d > base_date]
    if after:
        move = round((after[0][1] - base_close) / base_close * 100, 2)
        out["move_1d_pct"] = move
        out["correct_1d"] = (move > 0) == want_up if move != 0 else False
    target_7d = (datetime.fromisoformat(base_date) + timedelta(days=7)).date().isoformat()
    later = [(d, c) for d, c in closes if d >= target_7d]
    if later:
        move = round((later[0][1] - base_close) / base_close * 100, 2)
        out["move_7d_pct"] = move
        out["correct_7d"] = (move > 0) == want_up if move != 0 else False
    return out


def _aggregate(scored: list[dict]) -> dict:
    agg = {}
    for horizon in ("1d", "7d"):
        done = [s for s in scored if s[f"correct_{horizon}"] is not None]
        correct = sum(1 for s in done if s[f"correct_{horizon}"])
        agg[f"evaluated_{horizon}"] = len(done)
        agg[f"correct_{horizon}"] = correct
        agg[f"hit_rate_{horizon}"] = round(correct / len(done) * 100, 1) if done else None
    return agg


def compute_scoreboard(conn, now: int | None = None) -> dict:
    now = now or now_ms()
    calls = _collect_calls(conn, now - WINDOW_DAYS * 86_400_000)
    closes_by_ticker: dict[str, list[tuple[str, float]]] = {}
    for t in {c["ticker"] for c in calls}:
        closes_by_ticker[t] = [
            (r["date"], r["close"])
            for r in conn.execute(
                "SELECT date, close FROM price_history WHERE ticker=? ORDER BY date", (t,)
            )
        ]
    scored = [_score_call(c, closes_by_ticker.get(c["ticker"], [])) for c in calls]
    scored.sort(key=lambda s: (s["date"], s["ticker"]), reverse=True)

    by_ticker: dict[str, list[dict]] = {}
    for s in scored:
        by_ticker.setdefault(s["ticker"], []).append(s)

    return {
        "window_days": WINDOW_DAYS,
        "overall": _aggregate(scored),
        "by_ticker": {
            t: {**_aggregate(rows), "calls": len(rows)} for t, rows in sorted(by_ticker.items())
        },
        "calls": scored[:MAX_CALLS_SHOWN],
    }
