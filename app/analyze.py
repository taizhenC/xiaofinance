import argparse
import json
import logging
import time

from pydantic import BaseModel, Field, ValidationError

from .config import settings as default_settings
from .db import connect
from .dedup import comment_cluster_sizes, note_cluster_sizes
from .mentions import alias_hits, index_tickers, is_aside
from .scoring import is_rankable, source_fanout
from .util import (
    MIN_COMMENT_SUBSTANCE,
    MIN_NOTE_SUBSTANCE,
    QUOTE_MIN_SUBSTANCE,
    clean_tags,
    is_bot_prompt,
    note_text,
    now_ms,
    sha256_hex,
    substance,
)

log = logging.getLogger(__name__)

MAX_ITEMS = 60
NOTE_TRUNC = 300
COMMENT_TRUNC = 150
# The opening of a post is its topic sentence — keep it even when the ticker turns up much
# later, or a windowed excerpt reads as if it came from nowhere.
HEAD_KEEP = 60
# A mention this close to the end of the window would arrive with no context after it.
MENTION_TAIL = 40
# thread comments are only pulled from notes focused enough that "a comment
# here" plausibly reacts to this ticker, not one of a dozen listed names
THREAD_FANOUT_MAX = 2
THREAD_PER_NOTE = 5
FANOUT_ROUNDUP = 4
# previous-cycle summary is offered as compare-only context; older than this it's
# no longer "上一周期" and gets dropped rather than mislead
PREV_SUMMARY_MAX_AGE_MS = 48 * 3_600_000
PREV_SUMMARY_TRUNC = 300
# deepseek-chat list price, USD per 1M tokens
COST_IN_PER_M = 0.27
COST_OUT_PER_M = 1.10


class SentimentCounts(BaseModel):
    bullish: int = 0
    bearish: int = 0
    neutral: int = 0


class AnalysisResult(BaseModel):
    summary: str
    sentiment_counts: SentimentCounts = Field(default_factory=SentimentCounts)
    bull_points: list[str] = Field(default_factory=list)
    bear_points: list[str] = Field(default_factory=list)
    # Item numbers, not text: the model picking a quote cannot then misquote it, and it
    # stops paying output tokens to copy Chinese it was already shown.
    notable_quote_ids: list[int] = Field(default_factory=list)
    irrelevant_item_count: int = 0


def excerpt(text: str, pos: int, width: int) -> str:
    """Window a long post around where the ticker is actually named.

    Truncating from the start assumes the mention is up front, and in 23% of note-mentions
    it is not — the 高盛 one sits at character 1176 of 1243. Those posts reached the model
    as text that never names the ticker it was being asked to judge, and reached the keyless
    card as a quote that does not mention the stock it is filed under."""
    if pos < 0 or pos + MENTION_TAIL <= width:
        return text[:width]
    start = max(HEAD_KEEP, pos - width // 4)
    body = text[start : start + width - HEAD_KEEP]
    return f"{text[:HEAD_KEEP].rstrip()}…{body}".rstrip() + ("…" if start + width - HEAD_KEEP < len(text) else "")


def gather_items(conn, ticker: str, fresh_window_ms: int, now: int | None = None) -> list[dict]:
    """Canonical fresh items mentioning ticker, most-liked first, capped at MAX_ITEMS.
    Also pulls thread comments from focused mentioning notes (fanout ≤ 2): most
    comments never name the ticker, but under a dedicated note they are reactions
    to it — without this, nearly all crawled comments are invisible to analysis.

    Items with no readable prose are dropped rather than left for the model to sort out:
    an image post tagged #美光 costs input tokens and says nothing, and it surfaces as a
    quote in the keyless path where no model is there to sort anything out. The mention
    still counts — someone did post about the ticker — it just carries no evidence."""
    now = now or now_ms()
    cutoff = now - fresh_window_ms
    note_sizes = note_cluster_sizes(conn, fresh_window_ms, now)
    comment_sizes = comment_cluster_sizes(conn, fresh_window_ms, now)
    note_fanout = source_fanout(conn, "note", cutoff)
    comment_fanout = source_fanout(conn, "comment", cutoff)
    items = []
    for r in conn.execute(
        """SELECT n.note_id AS id, n.title, n.note_desc, n.liked_count AS likes,
                  n.publish_time_ms AS ts, n.note_url, m.matched_alias
           FROM stock_mentions m JOIN notes n ON n.note_id = m.source_id
           WHERE m.ticker=? AND m.source_type='note' AND m.content_time_ms>=?
             AND n.dup_group_id IS NULL""",
        (ticker, cutoff),
    ):
        full = clean_tags(note_text(r["title"], r["note_desc"]))
        pos, hits = alias_hits(full, r["matched_alias"] or "")
        text = excerpt(full, pos, NOTE_TRUNC)
        subs = substance(text)
        if subs < MIN_NOTE_SUBSTANCE:
            continue
        items.append({"type": "note", "id": r["id"], "text": text, "prompt_text": text,
                      "likes": r["likes"], "ts": r["ts"], "url": r["note_url"],
                      "substance": subs, "aside": is_aside(full, hits),
                      "cluster_size": note_sizes.get(r["id"], 1),
                      "fanout": note_fanout.get(r["id"], 1),
                      "unit": r["id"], "unit_likes": r["likes"], "depth": 0})
    picked: dict[str, dict] = {}
    for r in conn.execute(
        """SELECT c.comment_id AS id, c.content, c.like_count AS likes, c.create_time_ms AS ts,
                  c.note_id, c.parent_comment_id
           FROM stock_mentions m JOIN comments c ON c.comment_id = m.source_id
           WHERE m.ticker=? AND m.source_type='comment' AND m.content_time_ms>=?
             AND c.dup_group_id IS NULL""",
        (ticker, cutoff),
    ):
        picked[r["id"]] = dict(r, head=None)

    by_note: dict[str, list] = {}
    for r in conn.execute(
        """SELECT c.comment_id AS id, c.content, c.like_count AS likes, c.create_time_ms AS ts,
                  c.note_id, c.parent_comment_id, n.title, n.note_desc
           FROM stock_mentions m JOIN notes n ON n.note_id = m.source_id
           JOIN comments c ON c.note_id = n.note_id
           WHERE m.ticker=? AND m.source_type='note' AND m.content_time_ms>=?
             AND c.create_time_ms>=? AND c.dup_group_id IS NULL""",
        (ticker, cutoff, cutoff),
    ):
        if r["id"] in picked or note_fanout.get(r["note_id"], 1) > THREAD_FANOUT_MAX:
            continue
        by_note.setdefault(r["note_id"], []).append(r)
    for rows in by_note.values():
        rows.sort(key=lambda r: r["likes"], reverse=True)
        for r in rows[:THREAD_PER_NOTE]:
            # The reaction still counts when the post it reacts to is an unreadable image
            # ("Is That True？"); quoting that title back as context does not.
            body = clean_tags(note_text(r["title"], r["note_desc"]))
            title = clean_tags(" ".join((r["title"] or "").split()))[:20]
            head = f"主帖「{title}」下的评论" if substance(body) >= MIN_NOTE_SUBSTANCE else "主帖下的评论"
            picked[r["id"]] = dict(r, head=head)

    items += thread_items(conn, picked, comment_sizes, comment_fanout)
    # Units (a note, or a whole comment thread) are ranked by their best line, and a unit's
    # lines stay together in reading order — a reply must never be separated from the comment
    # it answers, least of all by the MAX_ITEMS cut.
    items.sort(key=lambda i: (-i["unit_likes"], i["unit"], i["depth"], i["ts"]))
    return items[:MAX_ITEMS]


def _comment_row(conn, comment_id: str) -> dict | None:
    r = conn.execute(
        """SELECT comment_id AS id, content, like_count AS likes, create_time_ms AS ts,
                  note_id, parent_comment_id FROM comments WHERE comment_id=?""",
        (comment_id,),
    ).fetchone()
    return dict(r, head=None) if r else None


def thread_items(conn, picked: dict[str, dict], sizes: dict, fanouts: dict) -> list[dict]:
    """Turn the selected comments into conversations rather than a pile of lines.

    A reply is close to worthless on its own — 别追，我出货了 says nothing until you know it
    answers 海力士还能上车吗. So each root is emitted immediately followed by its replies, and
    the reply's prompt text drops the parent quote entirely (the parent is the line above it),
    which reads better *and* costs fewer tokens than repeating it.

    A parent that wasn't selected on its own merits is pulled in anyway, as context for the
    reply that was."""
    for cid in list(picked):
        pid = picked[cid].get("parent_comment_id")
        if pid and pid not in picked:
            parent = _comment_row(conn, pid)
            if parent:
                parent["context_only"] = True
                picked[pid] = parent

    threads: dict[str, list[dict]] = {}
    for r in picked.values():
        root = r.get("parent_comment_id") or r["id"]
        threads.setdefault(root, []).append(r)

    out: list[dict] = []
    for root_id, rows in threads.items():
        rows.sort(key=lambda r: (r["id"] != root_id, r["ts"]))  # root first, then replies in time
        built: list[dict] = []
        for r in rows:
            text = " ".join((r["content"] or "").split())
            subs = substance(text)
            if is_bot_prompt(text) or subs < MIN_COMMENT_SUBSTANCE:
                continue
            is_reply = bool(r.get("parent_comment_id"))
            parent_shown = is_reply and any(b["id"] == r["parent_comment_id"] for b in built)
            if is_reply and not parent_shown:
                # orphaned: its parent was dropped, so it has to carry its own context
                p = picked.get(r["parent_comment_id"], {})
                snippet = " ".join((p.get("content") or "").split())[:30]
                card = f"回复「{snippet}」: {text}" if snippet else text
                prompt = card
            elif is_reply:
                card = f"回复「{' '.join((picked[r['parent_comment_id']]['content'] or '').split())[:30]}」: {text}"
                prompt = f"↳ {text}"  # the parent is the previous line; do not pay to repeat it
            else:
                card = prompt = f"{r['head']}: {text}" if r.get("head") else text
            built.append({
                "type": "comment", "id": r["id"], "text": card[:COMMENT_TRUNC],
                "prompt_text": prompt[:COMMENT_TRUNC], "likes": r["likes"], "ts": r["ts"],
                "url": None, "substance": subs, "cluster_size": sizes.get(r["id"], 1),
                "fanout": fanouts.get(r["id"], 1), "depth": 1 if is_reply else 0,
                "unit": root_id,
            })
        if not built:
            continue
        # a thread rides on its best line, so a sharp reply can carry a dull root onto the board
        top = max(b["likes"] for b in built)
        for b in built:
            b["unit_likes"] = top
        out += built
    return out


def pick_quotes(items: list[dict], k: int = 3) -> list[str]:
    """The keyless card's evidence. Focused sources first — a dedicated post beats a
    12-ticker roundup as this ticker's quote even when the roundup has far more likes —
    and only text that says something standalone, falling back to whatever exists rather
    than showing an empty card.

    Substance is judged on the item's own words, not on the "主帖「…」下的评论:" framing
    gather_items wraps them in, which would otherwise let a four-word reaction over the bar.

    Asides sink to the bottom rather than being dropped: a post that names the ticker once
    in a thousand characters is a poor quote for it, but for a ticker that is only ever
    named in passing it is all there is, and an empty card would be the bigger lie.
    """
    ranked = sorted(items, key=lambda i: (i.get("aside", False), i.get("fanout", 1), -i["likes"]))
    strong = [i for i in ranked if i.get("substance", 0) >= QUOTE_MIN_SUBSTANCE]
    return [i["text"] for i in (strong or ranked)[:k]]


def input_hash(items: list[dict]) -> str:
    """WHICH items came in — deliberately order-insensitive.

    This drives the analysis cache, and the question it answers is "is there new material to
    read?". A re-crawl that only bumps like counts reshuffles the list without changing a word
    of it, and re-paying DeepSeek to read the same posts in a different order would be waste."""
    return sha256_hex("|".join(sorted(f"{i['type']}:{i['id']}" for i in items)))


def evidence_hash(items: list[dict]) -> str:
    """WHICH items, IN WHAT ORDER — the contract for anything that cites items by number.

    Not the same question as input_hash, and the difference is not academic. Quote ids are
    positions in this list, and the list is ranked by likes. A note going viral between an
    agent reading the evidence and submitting its rating reorders it *without changing the
    set* — so input_hash matches, the staleness guard passes, and quote [3] silently resolves
    to a different post than the one the agent actually read."""
    return sha256_hex("|".join(f"{i['type']}:{i['id']}" for i in items))


def build_prompt(ticker: str, name_cn: str, items: list[dict], lang: str, now: int,
                 prev_summary: str | None = None, window_hours: int = 24) -> tuple[str, str]:
    lines = []
    for n, i in enumerate(items, 1):
        age_h = max(0, (now - i["ts"]) // 3_600_000)
        dup = f" [×{i['cluster_size']}相似]" if i["cluster_size"] > 1 else ""
        roundup = f" [盘点·提及{i['fanout']}股]" if i.get("fanout", 1) >= FANOUT_ROUNDUP else ""
        aside = " [顺带提及]" if i.get("aside") else ""
        body = i.get("prompt_text") or i["text"]
        lines.append(f"[{n}] [{i['type']}] [{age_h}小时前] [赞:{i['likes']}]{dup}{roundup}{aside} {body}")
    lang_name = "英文(English)" if lang == "en" else "中文"
    name = f"{ticker}（{name_cn}）" if name_cn else ticker
    prev_block = ""
    change_hint = ""
    if prev_summary:
        prev_block = (
            "\n【背景参考】上一周期的分析结论——仅用于对比舆论变化，不是本次判断的依据；"
            f"如与本次列出的内容矛盾，一律以本次内容为准：\n{prev_summary}\n"
        )
        change_hint = "如舆论方向或核心论点相比上一周期有明显变化，在 summary 末尾用一句话点明变化；无明显变化则不提。"
    system = (
        "你是一位资深美股分析师，从小红书的帖子和评论里提炼散户对某只股票的真实看法。"
        "小红书上大量内容是教学、引流和生活分享，只是顺带提到了股票——把这些剔除干净，"
        "比硬凑出一个观点更重要。没有观点时，如实说没有。"
    )
    user = f"""以下是过去{window_hours}小时内小红书上提及 {name} 的帖子和评论，每行格式：[编号] [类型] [发布时间] [点赞数] 内容。

标注含义：
- [×N相似]：N条重复转发已合并为一条。重复转发不代表更多独立观点。
- [盘点·提及N股]：该帖同时罗列了N只股票（财报日历、涨幅盘点等）。被罗列本身不构成观点。
- [顺带提及]：全文只出现一次 {ticker}，帖子主题多半不是它——期权教学拿它举例、持仓表里的一行、求职或开户经历里的公司名。
- 正文过长时，只截取到提及 {ticker} 的那一段，省略处用 … 标出。
- 以 ↳ 开头的是上一条的回复，按对话顺序排列。回复要结合它上面那一条来读：「别追，我出货了」单独看没有信息，接在「还能上车吗」后面才是明确看空。一问一答只是一次交流，不要当成两个独立观点。

{chr(10).join(lines)}
{prev_block}
请完成三步：
1. 剔除没有表达 {ticker} 投资观点的条目，数量记为 irrelevant_item_count。判断标准是「这条内容有没有对 {ticker} 表达看法」，不是「有没有出现 {ticker}」。常见应剔除的：同名歧义（水果苹果）、教学/科普里把 {ticker} 当例子、晒单晒产品、引流广告、把公司名当背景板的生活分享。
2. 对剩下的条目逐条判断立场（bullish/bearish/neutral），汇总为 sentiment_counts。论点相同的只算一个观点，按论点的数量与质量权衡，不按重复次数。若剩下0条，三个计数都填0，并在 summary 里直接说明本周期没有实质讨论——不要从被剔除的内容里推测立场。
3. 用{lang_name}写总结：summary 以 "{ticker}: " 开头，不超过120词；bull_points 最多4条看多要点，bear_points 最多4条看空要点（均用{lang_name}）；notable_quote_ids 最多3个编号，从**未被剔除**的条目里选最有代表性的，只给编号，不要重写原文。{change_hint}

只输出一个JSON对象，格式：
{{"summary": "...", "sentiment_counts": {{"bullish": 0, "bearish": 0, "neutral": 0}}, "bull_points": ["..."], "bear_points": ["..."], "notable_quote_ids": [1, 2], "irrelevant_item_count": 0}}"""
    return system, user


def quotes_from_ids(items: list[dict], ids: list[int], k: int = 3) -> list[str]:
    """Map the model's chosen item numbers back to their text, ignoring anything it made up."""
    seen, out = set(), []
    for n in ids:
        if 1 <= n <= len(items) and n not in seen:
            seen.add(n)
            out.append(items[n - 1]["text"])
    return out[:k]


def _call_llm(settings, system: str, user: str):
    from openai import OpenAI

    client = OpenAI(api_key=settings.DEEPSEEK_API_KEY, base_url=settings.LLM_BASE_URL)
    return client.chat.completions.create(
        model=settings.LLM_MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format={"type": "json_object"},
        max_tokens=2000,
        temperature=0.3,
    )


def analysis_cols(ticker: str, items: list[dict], settings, score: float,
                  run_id: int | None, now: int, model: str) -> dict:
    note_count = sum(1 for i in items if i["type"] == "note")
    return dict(
        ticker=ticker, run_id=run_id, generated_at_ms=now,
        window_start_ms=now - settings.fresh_window_ms, window_end_ms=now,
        note_count=note_count, comment_count=len(items) - note_count, popularity_score=score,
        input_item_count=len(items), input_hash=input_hash(items), model=model,
    )


def insert_analysis(conn, base_cols: dict, status: str, **extra) -> None:
    cols = {**base_cols, **extra, "status": status}
    keys = ",".join(cols)
    conn.execute(
        f"INSERT INTO stock_analyses({keys}) VALUES({','.join('?' * len(cols))})",
        tuple(cols.values()),
    )
    conn.commit()


def store_result(conn, ticker: str, items: list[dict], result: "AnalysisResult",
                 base_cols: dict, **extra) -> list[str]:
    """Persist a rating, whichever brain produced it — DeepSeek over HTTP, or an agent over
    MCP. Both go through the same validation and the same row, so the two are comparable and
    the dashboard cannot tell them apart except by the `model` column."""
    summary = result.summary.strip()
    if not summary.upper().startswith(f"{ticker}:"):
        summary = f"{ticker}: {summary}"
    quotes = quotes_from_ids(items, result.notable_quote_ids)
    insert_analysis(
        conn, base_cols, "ok",
        sentiment_counts=json.dumps(result.sentiment_counts.model_dump()),
        summary=summary,
        bull_points=json.dumps(result.bull_points[:4], ensure_ascii=False),
        bear_points=json.dumps(result.bear_points[:4], ensure_ascii=False),
        notable_quotes=json.dumps(quotes, ensure_ascii=False),
        irrelevant_item_count=result.irrelevant_item_count,
        **extra,
    )
    return quotes


def analysis_is_current(conn, ticker: str, ihash: str,
                        statuses: tuple[str, ...] = ("ok", "no_api_key")) -> bool:
    """Has this ticker already been analysed on exactly this evidence?

    `statuses` is the caller's definition of "analysed". The DeepSeek path counts a
    `no_api_key` row, because without a key it has nothing better to write and re-running would
    only duplicate it. An agent asking what still needs rating must NOT count it: that row holds
    fallback quotes and no judgement at all, which is precisely the gap the agent is there to
    fill."""
    last = conn.execute(
        "SELECT input_hash, status FROM stock_analyses WHERE ticker=? ORDER BY generated_at_ms DESC LIMIT 1",
        (ticker,),
    ).fetchone()
    return bool(last and last["input_hash"] == ihash and last["status"] in statuses)


def analyze_ticker(conn, ticker: str, settings=None, name_cn: str = "", score: float = 0.0,
                   run_id: int | None = None, now: int | None = None, force: bool = False) -> str:
    settings = settings or default_settings
    now = now or now_ms()
    items = gather_items(conn, ticker, settings.fresh_window_ms, now)
    if not items:
        return "no_items"
    ihash = input_hash(items)

    # unchanged inputs → keep the previous row as "latest" instead of duplicating it
    if not force and analysis_is_current(conn, ticker, ihash):
        log.info("%s: inputs unchanged, skipping", ticker)
        return "skipped_unchanged"

    base_cols = analysis_cols(ticker, items, settings, score, run_id, now, settings.LLM_MODEL)

    def insert(status, **extra):
        insert_analysis(conn, base_cols, status, **extra)

    if not settings.DEEPSEEK_API_KEY:
        quotes = pick_quotes(items)
        insert("no_api_key", notable_quotes=json.dumps(quotes, ensure_ascii=False))
        return "no_api_key"

    prev_row = conn.execute(
        """SELECT summary FROM stock_analyses WHERE ticker=? AND status='ok' AND summary IS NOT NULL
           AND generated_at_ms >= ? ORDER BY generated_at_ms DESC LIMIT 1""",
        (ticker, now - PREV_SUMMARY_MAX_AGE_MS),
    ).fetchone()
    prev_summary = prev_row["summary"].strip()[:PREV_SUMMARY_TRUNC] if prev_row else None

    system, user = build_prompt(ticker, name_cn, items, settings.SUMMARY_LANG, now, prev_summary,
                                settings.FRESH_WINDOW_HOURS)
    last_err = None
    for attempt in range(2):
        try:
            resp = _call_llm(settings, system, user)
            result = AnalysisResult.model_validate_json(resp.choices[0].message.content)
            summary = result.summary.strip()
            if not summary.upper().startswith(f"{ticker}:"):
                summary = f"{ticker}: {summary}"
            quotes = quotes_from_ids(items, result.notable_quote_ids)
            usage = resp.usage
            cost = (usage.prompt_tokens * COST_IN_PER_M + usage.completion_tokens * COST_OUT_PER_M) / 1e6
            insert(
                "ok",
                sentiment_counts=json.dumps(result.sentiment_counts.model_dump()),
                summary=summary,
                bull_points=json.dumps(result.bull_points[:4], ensure_ascii=False),
                bear_points=json.dumps(result.bear_points[:4], ensure_ascii=False),
                notable_quotes=json.dumps(quotes, ensure_ascii=False),
                irrelevant_item_count=result.irrelevant_item_count,
                input_tokens=usage.prompt_tokens, output_tokens=usage.completion_tokens,
                cost_usd=round(cost, 6),
            )
            return "ok"
        except Exception as e:  # per-ticker isolation: API, parse, and validation errors alike
            last_err = e
            log.warning("%s: analysis attempt %d failed: %s", ticker, attempt + 1, e)
            time.sleep(1)
    insert("error", error=str(last_err)[:500])
    return "error"


def analyze_all(conn, settings, dict_data: dict, stats: dict[str, dict],
                tracked: set[str], min_mentions: int, max_stocks: int,
                run_id: int | None = None, now: int | None = None,
                force: bool = False, max_indexes: int = 3) -> dict[str, str]:
    names = {s["ticker"]: s.get("name_cn", "") for s in dict_data.get("stocks", [])}
    indexes = index_tickers(dict_data)
    # Budgeted separately, or 纳指 and 标普 would take most of the stock budget on score alone.
    ranked = sorted(
        (e for e in stats.values()
         if e["ticker"] not in indexes and is_rankable(e, min_mentions)),
        key=lambda e: e["score"], reverse=True,
    )[:max_stocks]
    ranked += sorted(
        (e for e in stats.values()
         if e["ticker"] in indexes and e.get("mentions", 0) >= min_mentions),
        key=lambda e: e["score"], reverse=True,
    )[:max_indexes]
    candidates = [e["ticker"] for e in ranked]
    for t in sorted(tracked):
        if t not in candidates and stats.get(t, {}).get("mentions", 0) >= 1:
            candidates.append(t)

    results = {}
    for t in candidates:
        results[t] = analyze_ticker(
            conn, t, settings, names.get(t, ""), stats.get(t, {}).get("score", 0.0),
            run_id, now, force,
        )
        if settings.DEEPSEEK_API_KEY:
            time.sleep(0.5)
    log.info("analyze_all: %s", results)
    return results


if __name__ == "__main__":
    from . import mentions, scoring

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("ticker")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    conn = connect()
    dict_data = mentions.load_stock_dict()
    names = {s["ticker"]: s.get("name_cn", "") for s in dict_data["stocks"]}
    stats = scoring.compute_stats(conn, default_settings.fresh_window_ms)
    t = args.ticker.upper()
    status = analyze_ticker(conn, t, default_settings, names.get(t, ""),
                            stats.get(t, {}).get("score", 0.0), force=args.force)
    print(f"{t}: {status}")
    row = conn.execute(
        "SELECT status, summary FROM stock_analyses WHERE ticker=? ORDER BY generated_at_ms DESC LIMIT 1",
        (t,),
    ).fetchone()
    if row:
        print(row["status"], "-", (row["summary"] or "")[:200])
