import json
import logging
import re
from pathlib import Path

from .config import settings
from .util import norm_text, now_ms

log = logging.getLogger(__name__)

_STRENGTH = {"safe_alias": 4, "ticker_symbol": 3, "alias+context": 2, "targeted_search": 1}
_STRENGTH_CASE = (
    "CASE {} WHEN 'safe_alias' THEN 4 WHEN 'ticker_symbol' THEN 3 "
    "WHEN 'alias+context' THEN 2 ELSE 1 END"
)
_ASCII_ALIAS_RE = re.compile(r"^[0-9a-z&.\- ]+$")


def load_stock_dict() -> dict:
    with open(settings.STOCK_DICT_PATH, encoding="utf-8") as f:
        base = json.load(f)
    local = Path(settings.STOCK_DICT_LOCAL_PATH)
    if local.exists():
        try:
            with open(local, encoding="utf-8") as f:
                overlay = json.load(f)
        except (json.JSONDecodeError, OSError):
            log.warning("overlay dict %s unreadable, ignoring", local)
            return base
        base["context_words"] = list(
            dict.fromkeys(base.get("context_words", []) + overlay.get("context_words", []))
        )
        base["collision_tickers"] = list(
            dict.fromkeys(base.get("collision_tickers", []) + overlay.get("collision_tickers", []))
        )
        by_ticker = {s["ticker"]: s for s in base["stocks"]}
        for s in overlay.get("stocks", []):
            cur = by_ticker.get(s["ticker"])
            if cur:
                cur["aliases"] = list(dict.fromkeys(cur.get("aliases", []) + s.get("aliases", [])))
                cur["ambiguous"] = list(
                    dict.fromkeys(cur.get("ambiguous", []) + s.get("ambiguous", []))
                )
            else:
                base["stocks"].append(s)
                by_ticker[s["ticker"]] = s
    return base


def add_alias_to_overlay(term: str, ticker: str) -> None:
    """Merge an accepted alias into the user overlay dict — ambiguous by default,
    so the context gate still applies to it."""
    path = Path(settings.STOCK_DICT_LOCAL_PATH)
    data: dict = {"stocks": []}
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            log.warning("overlay dict %s unreadable, recreating", path)
            data = {"stocks": []}
    data.setdefault("stocks", [])
    for s in data["stocks"]:
        if s.get("ticker") == ticker:
            amb = s.setdefault("ambiguous", [])
            if term not in amb:
                amb.append(term)
            break
    else:
        data["stocks"].append({"ticker": ticker, "name_cn": "", "aliases": [], "ambiguous": [term]})
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _compile_alias(alias: str):
    a = alias.lower()
    if _ASCII_ALIAS_RE.match(a):
        # Latin aliases need word boundaries ("arm" must not hit "farmer");
        # CJK has no word boundaries, so plain substring there.
        rx = re.compile(r"(?<![0-9a-z])" + re.escape(a) + r"(?![0-9a-z])")
        return lambda lower: rx.search(lower) is not None
    return lambda lower: a in lower


def alias_hits(text: str, alias: str) -> tuple[int, int]:
    """(position of the first mention, how many times it is named) — by the same boundary
    rules the matcher used, so callers agree with it about what counts as a mention.

    Both halves answer "is this post about the ticker, or does it just contain it": where
    the name first shows up, and whether it comes back."""
    if not alias:
        return -1, 0
    a = alias.lower()
    lower = norm_text(text or "").lower()
    if _ASCII_ALIAS_RE.match(a):
        rx = re.compile(r"(?<![0-9a-z])" + re.escape(a) + r"(?![0-9a-z])")
        found = list(rx.finditer(lower))
        return (found[0].start(), len(found)) if found else (-1, 0)
    return lower.find(a), lower.count(a)


class Matcher:
    def __init__(self, dict_data: dict, tracked: dict[str, list[str]] | None = None):
        tracked = tracked or {}
        # Same boundary rule as aliases: a latin context word like "pe" or "put" must not
        # match inside "people"/"input", or any English text would satisfy the context gate.
        self._context_matchers = [
            _compile_alias(w.lower()) for w in dict_data.get("context_words", [])
        ]
        self.collision = set(dict_data.get("collision_tickers", []))
        self.stocks: dict[str, dict] = {}
        for s in dict_data.get("stocks", []):
            self.stocks[s["ticker"]] = {
                "name_cn": s.get("name_cn", ""),
                "safe": list(s.get("aliases", [])),
                "ambiguous": list(s.get("ambiguous", [])),
            }
        for t, kws in tracked.items():
            entry = self.stocks.setdefault(t, {"name_cn": "", "safe": [], "ambiguous": []})
            # user-supplied keywords are unvetted → treated as ambiguous
            entry["ambiguous"] = list(dict.fromkeys(entry["ambiguous"] + [k for k in (kws or []) if k]))

        symbols = sorted((t for t in self.stocks if len(t) >= 2), key=len, reverse=True)
        self.symbol_re = (
            re.compile(r"(?<![A-Za-z0-9])(" + "|".join(re.escape(s) for s in symbols) + r")(?![A-Za-z0-9])")
            if symbols
            else None
        )
        self._alias_matchers = []
        for t, e in self.stocks.items():
            for alias in e["safe"]:
                self._alias_matchers.append((t, alias, True, _compile_alias(alias)))
            for alias in e["ambiguous"]:
                self._alias_matchers.append((t, alias, False, _compile_alias(alias)))

    def has_context(self, lower_text: str) -> bool:
        return any(fn(lower_text) for fn in self._context_matchers)

    def extract(self, text: str, targeted_ticker: str | None = None) -> dict[str, tuple[str, str]]:
        """text unit -> {ticker: (matched_alias, match_basis)}, strongest basis per ticker."""
        raw = norm_text(text or "")
        if not raw.strip():
            return {}
        lower = raw.lower()
        ctx = self.has_context(lower)
        out: dict[str, tuple[str, str]] = {}

        def add(ticker: str, alias: str, basis: str):
            if ticker not in out or _STRENGTH[basis] > _STRENGTH[out[ticker][1]]:
                out[ticker] = (alias, basis)

        if self.symbol_re:
            for m in self.symbol_re.finditer(raw):
                t = m.group(1)
                if t in self.collision:
                    if ctx:
                        add(t, t, "ticker_symbol")
                    elif targeted_ticker == t:
                        add(t, t, "targeted_search")
                else:
                    add(t, t, "ticker_symbol")

        for t, alias, is_safe, fn in self._alias_matchers:
            if not fn(lower):
                continue
            if is_safe:
                add(t, alias, "safe_alias")
            elif ctx:
                add(t, alias, "alias+context")
            elif targeted_ticker == t:
                add(t, alias, "targeted_search")
        return out


def build_tracked_keywords(dict_data: dict, tracked_rows) -> tuple[list[str], dict[str, str]]:
    """XHS queries for tracked tickers + query→ticker map (source_keyword provenance).
    Ambiguous/unvetted Chinese keywords get finance-qualified ('苹果 美股')."""
    stocks = {s["ticker"]: s for s in dict_data.get("stocks", [])}
    context_words = [w.lower() for w in dict_data.get("context_words", [])]

    def qualified(kw: str) -> str:
        return kw if any(c in kw.lower() for c in context_words) else f"{kw} 美股"

    queries: list[str] = []
    mapping: dict[str, str] = {}
    for row in tracked_rows:
        t = row["ticker"]
        custom = json.loads(row["custom_keywords"] or "[]")
        qs = [t]
        entry = stocks.get(t)
        if entry:
            if entry.get("aliases"):
                qs.append(entry["aliases"][0])
            elif entry.get("ambiguous"):
                qs.append(qualified(entry["ambiguous"][0]))
        qs.extend(qualified(k) for k in custom if k)
        for q in qs:
            if q.lower() not in mapping:
                mapping[q.lower()] = t
                queries.append(q)
    return queries, mapping


def extract_mentions(conn, dict_data: dict, tracked_rows, fresh_window_ms: int,
                     run_id: int | None = None, now: int | None = None) -> int:
    now = now or now_ms()
    cutoff = now - fresh_window_ms
    tracked_map = {r["ticker"]: json.loads(r["custom_keywords"] or "[]") for r in tracked_rows}
    matcher = Matcher(dict_data, tracked_map)
    _, query_map = build_tracked_keywords(dict_data, tracked_rows)

    strength_new = _STRENGTH_CASE.format("excluded.match_basis")
    strength_cur = _STRENGTH_CASE.format("stock_mentions.match_basis")
    upsert = f"""
        INSERT INTO stock_mentions(ticker, source_type, source_id, note_id, matched_alias,
                                   match_basis, content_time_ms, run_id)
        VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(ticker, source_type, source_id) DO UPDATE SET
          matched_alias = CASE WHEN {strength_new} > {strength_cur}
                               THEN excluded.matched_alias ELSE matched_alias END,
          match_basis   = CASE WHEN {strength_new} > {strength_cur}
                               THEN excluded.match_basis ELSE match_basis END
    """

    count = 0
    for n in conn.execute(
        "SELECT note_id, title, note_desc, source_keyword, publish_time_ms FROM notes WHERE publish_time_ms >= ?",
        (cutoff,),
    ).fetchall():
        targeted = query_map.get((n["source_keyword"] or "").strip().lower())
        found = matcher.extract(f"{n['title'] or ''}\n{n['note_desc'] or ''}", targeted)
        for t, (alias, basis) in found.items():
            conn.execute(upsert, (t, "note", n["note_id"], n["note_id"], alias, basis,
                                  n["publish_time_ms"], run_id))
            count += 1

    for c in conn.execute(
        """SELECT c.comment_id, c.content, c.note_id, c.create_time_ms, n.source_keyword
           FROM comments c JOIN notes n ON n.note_id = c.note_id
           WHERE c.create_time_ms >= ?""",
        (cutoff,),
    ).fetchall():
        targeted = query_map.get((c["source_keyword"] or "").strip().lower())
        found = matcher.extract(c["content"] or "", targeted)
        for t, (alias, basis) in found.items():
            conn.execute(upsert, (t, "comment", c["comment_id"], c["note_id"], alias, basis,
                                  c["create_time_ms"], run_id))
            count += 1

    conn.commit()
    log.info("mentions: %d matches", count)
    return count
