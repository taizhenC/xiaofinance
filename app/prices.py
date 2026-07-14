"""Free daily quotes from Yahoo Finance's public chart API (no key; the same
endpoint yfinance wraps). Only closes are stored: the last two sessions drive
the "price reality check" badge, and the daily history feeds the hit-rate
scoreboard. All failures are swallowed per ticker so a network outage can
never break a pipeline cycle."""

import json
import logging
import urllib.request
from datetime import date, datetime, timedelta, timezone

from .util import now_ms

log = logging.getLogger(__name__)

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=2mo&interval=1d"
UA = {"User-Agent": "Mozilla/5.0 (infinance local dashboard)"}
TIMEOUT_S = 10
HISTORY_KEEP_DAYS = 60


# dictionary tickers that aren't valid Yahoo symbols as-is.
# SKHY: SK hynix ADR still trades under the when-issued symbol SKHYV
# (listed 2026-07-10); drop the override once SKHY serves quotes itself.
_SYMBOL_OVERRIDES = {"BRK": "BRK-B", "SKHY": "SKHYV"}


def yahoo_symbol(ticker: str) -> str:
    return _SYMBOL_OVERRIDES.get(ticker, ticker.replace(".", "-"))


def parse_chart_json(data: dict) -> list[tuple[str, float]]:
    """Yahoo v8 chart JSON -> [(iso_date, close), ...] oldest first.
    Dates use the exchange's own timezone (gmtoffset) — converting via the
    local clock would shift a US close onto the next day from UTC+8."""
    try:
        res = data["chart"]["result"][0]
        timestamps = res["timestamp"]
        closes = res["indicators"]["quote"][0]["close"]
        offset = res.get("meta", {}).get("gmtoffset", 0)
    except (KeyError, IndexError, TypeError):
        return []
    by_date: dict[str, float] = {}
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        day = datetime.fromtimestamp(ts + offset, tz=timezone.utc).date().isoformat()
        by_date[day] = round(float(close), 4)
    return sorted(by_date.items())


def _fetch_history(ticker: str, symbol: str | None = None) -> list[tuple[str, float]]:
    req = urllib.request.Request(CHART_URL.format(sym=symbol or yahoo_symbol(ticker)), headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        return parse_chart_json(json.loads(r.read().decode("utf-8", "replace")))


def store_quote(conn, ticker: str, closes: list[tuple[str, float]], now: int) -> bool:
    if not closes:
        return False
    conn.executemany(
        "INSERT INTO price_history(ticker, date, close) VALUES(?,?,?) "
        "ON CONFLICT(ticker, date) DO UPDATE SET close=excluded.close",
        [(ticker, d, c) for d, c in closes],
    )
    market_date, price = closes[-1]
    # fresh IPOs have a single session — show the price, skip the change badge
    prev = closes[-2][1] if len(closes) >= 2 else None
    change = round((price - prev) / prev * 100, 2) if prev else None
    conn.execute(
        """INSERT INTO quotes(ticker, price, prev_close, change_pct, market_date, quoted_at_ms)
           VALUES(?,?,?,?,?,?)
           ON CONFLICT(ticker) DO UPDATE SET price=excluded.price, prev_close=excluded.prev_close,
             change_pct=excluded.change_pct, market_date=excluded.market_date,
             quoted_at_ms=excluded.quoted_at_ms""",
        (ticker, price, prev, change, market_date, now),
    )
    return True


def refresh_quotes(conn, tickers, now: int | None = None,
                   symbol_overrides: dict[str, str] | None = None) -> int:
    now = now or now_ms()
    symbol_overrides = symbol_overrides or {}
    updated = 0
    for t in dict.fromkeys(tickers):
        try:
            if store_quote(conn, t, _fetch_history(t, symbol_overrides.get(t)), now):
                updated += 1
        except Exception as e:
            log.warning("quote refresh failed for %s: %s", t, e)
    conn.execute(
        "DELETE FROM price_history WHERE date < ?",
        ((date.today() - timedelta(days=HISTORY_KEEP_DAYS)).isoformat(),),
    )
    conn.commit()
    return updated


def get_quotes(conn, tickers) -> dict[str, dict]:
    tickers = list(tickers)
    if not tickers:
        return {}
    qmarks = ",".join("?" * len(tickers))
    return {
        r["ticker"]: dict(r)
        for r in conn.execute(f"SELECT * FROM quotes WHERE ticker IN ({qmarks})", tickers)
    }


def quotes_need_refresh(conn, tickers, max_age_ms: int, now: int | None = None) -> bool:
    tickers = list(tickers)
    if not tickers:
        return False
    now = now or now_ms()
    quotes = get_quotes(conn, tickers)
    if any(t not in quotes for t in tickers):
        return True
    return any(now - q["quoted_at_ms"] > max_age_ms for q in quotes.values())
