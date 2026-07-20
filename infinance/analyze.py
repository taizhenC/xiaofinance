import argparse
import json
import logging
import time

from pydantic import BaseModel, Field

from .config import settings as default_settings
from .db import connect
from .dedup import comment_cluster_sizes, note_cluster_sizes
from .mentions import alias_hits, asset_classes, index_tickers, investment_tickers, is_aside
from .scoring import is_rankable, source_fanout
from .util import (
    MIN_COMMENT_SUBSTANCE,
    MIN_NOTE_SUBSTANCE,
    QUOTE_MIN_SUBSTANCE,
    clean_tags,
    is_bot_prompt,
    norm_for_hash,
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
BATCH_MAX_TICKERS = 5
ASSET_TYPE_NAMES = {
    "stock": "股票", "index": "指数或ETF", "commodity": "大宗商品",
    "bond": "债券", "fund": "基金", "crypto": "加密资产", "forex": "外汇",
}
# previous-cycle summary is offered as compare-only context; older than this it's
# no longer "上一周期" and gets dropped rather than mislead
PREV_SUMMARY_MAX_AGE_MS = 48 * 3_600_000
PREV_SUMMARY_TRUNC = 300
# deepseek-v4-flash list price, USD per 1M tokens
COST_IN_PER_M = 0.14
COST_OUT_PER_M = 0.28


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


class TickerAnalysisResult(AnalysisResult):
    ticker: str


class BatchAnalysisResult(BaseModel):
    analyses: list[TickerAnalysisResult]


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


def verify_quotes(quotes: list[str], items: list[dict]) -> tuple[list[str], int]:
    """Keep only quotes that are verbatim substrings of some input item
    (compared after norm_for_hash: NFKC, lowercased latin, punctuation and
    whitespace stripped — so the model may reflow spacing/punctuation but not
    change a single content character). Quotes are the product's proof layer;
    an LLM paraphrase presented as a quote is a fabrication.

    Returns (verified_quotes, dropped_count)."""
    haystacks = [norm_for_hash(i["text"]) for i in items]
    kept: list[str] = []
    dropped = 0
    for q in quotes:
        needle = norm_for_hash(q or "")
        # a quote that normalizes to nothing (pure emoji/punctuation) proves nothing
        if needle and any(needle in h for h in haystacks):
            kept.append(q)
        else:
            dropped += 1
    return kept, dropped


def fallback_quotes(items: list[dict]) -> list[str]:
    """Real top-liked texts, comments preferred — same shape the keyless path uses."""
    return [i["text"] for i in items if i["type"] == "comment"][:3] or [i["text"] for i in items[:3]]


def evidence_hash(items: list[dict]) -> str:
    """WHICH items, IN WHAT ORDER — the contract for anything that cites items by number.

    Not the same question as input_hash, and the difference is not academic. Quote ids are
    positions in this list, and the list is ranked by likes. A note going viral between an
    agent reading the evidence and submitting its rating reorders it *without changing the
    set* — so input_hash matches, the staleness guard passes, and quote [3] silently resolves
    to a different post than the one the agent actually read."""
    return sha256_hex("|".join(f"{i['type']}:{i['id']}" for i in items))


def build_prompt(ticker: str, name_cn: str, items: list[dict], lang: str, now: int,
                 prev_summary: str | None = None, window_hours: int = 24,
                 asset_type: str = "stock") -> tuple[str, str]:
    lines = []
    for n, i in enumerate(items, 1):
        age_h = max(0, (now - i["ts"]) // 3_600_000)
        dup = f" [×{i['cluster_size']}相似]" if i["cluster_size"] > 1 else ""
        roundup = f" [盘点·提及{i['fanout']}股]" if i.get("fanout", 1) >= FANOUT_ROUNDUP else ""
        aside = " [顺带提及]" if i.get("aside") else ""
        body = i.get("prompt_text") or i["text"]
        lines.append(f"[{n}] [{i['type']}] [{age_h}小时前] [赞:{i['likes']}]{dup}{roundup}{aside} {body}")
    markers = []
    if any(i["cluster_size"] > 1 for i in items):
        markers.append("- [×N相似]：N条重复内容已合并，不算独立观点。")
    if any(i.get("fanout", 1) >= FANOUT_ROUNDUP for i in items):
        markers.append("- [盘点·提及N股]：同时罗列N股；被列出不等于表达观点。")
    if any(i.get("aside") for i in items):
        markers.append(
            f"- [顺带提及]：全文仅出现一次{ticker}，通常不是主题"
            "（如教学例子、持仓一行、求职/开户背景）。"
        )
    if any("…" in (i.get("prompt_text") or i["text"]) for i in items):
        markers.append(f"- …：长文仅保留开头及{ticker}附近语境。")
    if any((i.get("prompt_text") or "").startswith("↳") for i in items):
        markers.append(
            "- ↳：上一条的回复，须结合上文理解；"
            "一问一答只是一次交流，不算两个独立观点。"
        )
    marker_block = f"\n标记：\n{chr(10).join(markers)}\n" if markers else ""
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
    subject_type = ASSET_TYPE_NAMES.get(asset_type, "投资标的")
    system = (
        "你是资深市场分析师。仅依据提供的小红书内容提炼散户对投资标的的真实看法；"
        "无观点就如实说明，不得推测。只输出JSON。"
    )
    user = f"""分析过去{window_hours}小时提及 {name} [{subject_type}] 的帖子/评论。每行格式：[编号] [类型] [距今小时] [赞数] 内容。
{marker_block}
{chr(10).join(lines)}
{prev_block}
任务：
1. 只保留对 {ticker} 表达投资看法的条目；剔除同名歧义、教学/科普例子、产品晒单、广告引流、仅以公司为背景的生活分享。irrelevant_item_count=剔除数。
2. 将保留条目判为 bullish/bearish/neutral 并计数。相同论点只算一次；综合论点数量与质量，不按重复次数。若无保留条目，计数全为0，summary说明本周期无实质讨论，不从剔除内容推测。
3. 用{lang_name}输出：summary以 "{ticker}: " 开头且不超过120词；bull_points、bear_points各最多4条；notable_quote_ids从保留条目选最多3个代表编号，只给编号，不抄原文。{change_hint}

JSON格式：
{{"summary": "...", "sentiment_counts": {{"bullish": 0, "bearish": 0, "neutral": 0}}, "bull_points": ["..."], "bear_points": ["..."], "notable_quote_ids": [1, 2], "irrelevant_item_count": 0}}"""
    return system, user


def _batch_evidence_key(item: dict) -> tuple[str, str, str]:
    return item["type"], item["id"], item.get("prompt_text") or item["text"]


def shared_evidence_groups(contexts: list[dict], max_size: int = BATCH_MAX_TICKERS) -> list[list[dict]]:
    order = {c["ticker"]: n for n, c in enumerate(contexts)}
    by_ticker = {c["ticker"]: c for c in contexts}
    weights = {}
    for context in contexts:
        weights[context["ticker"]] = {
            _batch_evidence_key(item): len(item.get("prompt_text") or item["text"])
            for item in context["items"] if item.get("fanout", 1) >= FANOUT_ROUNDUP
        }

    def overlap(left: str, right: str) -> int:
        shared = weights[left].keys() & weights[right].keys()
        return sum(weights[left][key] for key in shared)

    remaining = [c["ticker"] for c in contexts]
    groups = []
    while remaining:
        seed = max(
            remaining,
            key=lambda ticker: (
                sum(overlap(ticker, other) for other in remaining if other != ticker),
                -order[ticker],
            ),
        )
        remaining.remove(seed)
        group = [seed]
        while remaining and len(group) < max_size:
            candidate = max(
                remaining,
                key=lambda ticker: (sum(overlap(ticker, member) for member in group), -order[ticker]),
            )
            if sum(overlap(candidate, member) for member in group) == 0:
                break
            remaining.remove(candidate)
            group.append(candidate)
        groups.append([by_ticker[ticker] for ticker in group])
    return sorted(groups, key=lambda group: min(order[c["ticker"]] for c in group))


def build_batch_prompt(contexts: list[dict], lang: str, now: int,
                       window_hours: int = 24) -> tuple[str, str]:
    evidence_ids = {}
    evidence_lines = []
    target_blocks = []
    all_items = [item for context in contexts for item in context["items"]]
    for context in contexts:
        refs = []
        for n, item in enumerate(context["items"], 1):
            key = _batch_evidence_key(item)
            if key not in evidence_ids:
                evidence_id = len(evidence_ids) + 1
                evidence_ids[key] = evidence_id
                age_h = max(0, (now - item["ts"]) // 3_600_000)
                body = item.get("prompt_text") or item["text"]
                evidence_lines.append(
                    f"[E{evidence_id}] [{item['type']}] [{age_h}小时前] [赞:{item['likes']}] {body}"
                )
            evidence_id = evidence_ids[key]
            dup = f" [×{item['cluster_size']}相似]" if item["cluster_size"] > 1 else ""
            roundup = (
                f" [盘点·提及{item['fanout']}股]"
                if item.get("fanout", 1) >= FANOUT_ROUNDUP else ""
            )
            aside = " [顺带提及]" if item.get("aside") else ""
            refs.append(f"[{n}]=E{evidence_id}{dup}{roundup}{aside}")
        name = context["ticker"]
        if context.get("name_cn"):
            name += f"（{context['name_cn']}）"
        subject_type = ASSET_TYPE_NAMES.get(context.get("asset_type", "stock"), "投资标的")
        block = f"{name} [{subject_type}]\n{' '.join(refs)}"
        if context.get("prev_summary"):
            block += (
                "\n上一周期的分析结论（仅用于对比变化，不是本次判断的依据；"
                "冲突时以本次证据为准）：\n"
                f"{context['prev_summary']}"
            )
        target_blocks.append(block)

    markers = []
    if any(item["cluster_size"] > 1 for item in all_items):
        markers.append("- [×N相似]：N条重复内容已合并，不算独立观点。")
    if any(item.get("fanout", 1) >= FANOUT_ROUNDUP for item in all_items):
        markers.append("- [盘点·提及N股]：同时罗列N股；被列出不等于表达观点。")
    if any(item.get("aside") for item in all_items):
        markers.append(
            "- [顺带提及]：全文仅出现一次该标的，通常不是主题，"
            "如教学例子、持仓一行或背景公司名。"
        )
    if any("…" in (item.get("prompt_text") or item["text"]) for item in all_items):
        markers.append("- …：长文仅保留开头及目标附近语境。")
    if any((item.get("prompt_text") or "").startswith("↳") for item in all_items):
        markers.append("- ↳：上一条的回复，须结合目标映射中的前一条理解；一问一答只算一次交流。")
    marker_block = f"\n标记：\n{chr(10).join(markers)}\n" if markers else ""
    lang_name = "英文(English)" if lang == "en" else "中文"
    change_hint = ""
    if any(context.get("prev_summary") for context in contexts):
        change_hint = "有明显舆论变化时在对应summary末尾用一句话说明；无变化不提。"
    tickers = "、".join(context["ticker"] for context in contexts)
    system = (
        "你是资深市场分析师。仅依据提供的小红书内容提炼散户对投资标的的真实看法；"
        "无观点就如实说明，不得推测。只输出JSON。"
    )
    user = f"""同时分析过去{window_hours}小时的 {tickers}。共享证据只列一次；每个标的下的 [本地编号]=E编号 映射定义该标的应评估的完整证据集和顺序。
{marker_block}
共享证据：
{chr(10).join(evidence_lines)}

标的映射：
{chr(10).join(target_blocks)}

对每个标的独立完成：
1. 只保留对该标的表达投资看法的条目；剔除同名歧义、教学/科普例子、产品晒单、广告引流、仅以公司为背景的生活分享。irrelevant_item_count=剔除数。
2. 将保留条目判为 bullish/bearish/neutral 并计数。相同论点只算一次；综合论点数量与质量，不按重复次数。若无保留条目，计数全为0，summary说明本周期无实质讨论，不从剔除内容推测。
3. 用{lang_name}输出：summary以对应的 "TICKER: " 开头且不超过120词；bull_points、bear_points各最多4条；notable_quote_ids从保留条目选最多3个本地编号，只给编号，不给E编号、不抄原文。{change_hint}
4. analyses必须恰好包含每个请求标的一次，ticker使用上方代码。

JSON格式：
{{"analyses": [{{"ticker": "NVDA", "summary": "NVDA: ...", "sentiment_counts": {{"bullish": 0, "bearish": 0, "neutral": 0}}, "bull_points": ["..."], "bear_points": ["..."], "notable_quote_ids": [1, 2], "irrelevant_item_count": 0}}]}}"""
    return system, user


def quotes_from_ids(items: list[dict], ids: list[int], k: int = 3) -> list[str]:
    """Map the model's chosen item numbers back to their text, ignoring anything it made up."""
    seen, out = set(), []
    for n in ids:
        if 1 <= n <= len(items) and n not in seen:
            seen.add(n)
            out.append(items[n - 1]["text"])
    return out[:k]


def _call_llm(settings, system: str, user: str, max_tokens: int = 2000):
    from openai import OpenAI

    client = OpenAI(api_key=settings.DEEPSEEK_API_KEY, base_url=settings.LLM_BASE_URL)
    return client.chat.completions.create(
        model=settings.LLM_MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format={"type": "json_object"},
        max_tokens=max_tokens,
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
                   run_id: int | None = None, now: int | None = None, force: bool = False,
                   asset_type: str = "stock") -> str:
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
        # The fallback row exists to give a card *something* when nothing has judged this
        # ticker. If something has — an agent, over MCP — then writing one now would bury a
        # real rating under a bare quote list, and it would happen on every crawl: each cycle
        # changes the evidence, so this branch fires, so the only judgement the dashboard has
        # is deleted every few hours. Keep it. It carries its own age on the card, and
        # pending_ratings() re-offers the ticker the moment its evidence moves.
        if conn.execute(
            "SELECT 1 FROM stock_analyses WHERE ticker=? AND status='ok' LIMIT 1", (ticker,)
        ).fetchone():
            log.info("%s: keeping the existing rating rather than burying it in a fallback", ticker)
            return "kept_rating"
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
                                settings.FRESH_WINDOW_HOURS, asset_type)
    last_err = None
    for attempt in range(2):
        try:
            resp = _call_llm(settings, system, user)
            result = AnalysisResult.model_validate_json(resp.choices[0].message.content)
        except Exception as e:  # per-ticker isolation: API, parse, and validation errors alike
            last_err = e
            log.warning("%s: analysis attempt %d failed: %s", ticker, attempt + 1, e)
            if attempt == 0:
                time.sleep(1)
        else:
            break
    else:
        insert("error", error=str(last_err)[:500])
        return "error"

    usage = resp.usage
    cost = (usage.prompt_tokens * COST_IN_PER_M + usage.completion_tokens * COST_OUT_PER_M) / 1e6
    try:
        store_result(
            conn, ticker, items, result, base_cols,
            input_tokens=usage.prompt_tokens, output_tokens=usage.completion_tokens,
            cost_usd=round(cost, 6),
        )
    except Exception as e:
        log.warning("%s: storing analysis failed: %s", ticker, e)
        try:
            insert("error", error=str(e)[:500])
        except Exception:
            log.exception("%s: storing the analysis error row also failed", ticker)
        return "error"
    return "ok"


def _allocate_usage(total: int, weights: list[int]) -> list[int]:
    if not weights:
        return []
    weight_sum = sum(weights)
    allocated = [total * weight // weight_sum for weight in weights]
    remainders = [total * weight % weight_sum for weight in weights]
    for index in sorted(range(len(weights)), key=lambda n: (-remainders[n], n))[:total - sum(allocated)]:
        allocated[index] += 1
    return allocated


def analyze_tickers_batch(conn, contexts: list[dict], settings, run_id: int | None,
                          now: int, force: bool = False) -> dict[str, str]:
    results = {}
    prepared = []
    for context in contexts:
        ticker = context["ticker"]
        items = context["items"]
        if not items:
            results[ticker] = "no_items"
            continue
        ihash = input_hash(items)
        if not force and analysis_is_current(conn, ticker, ihash):
            results[ticker] = "skipped_unchanged"
            continue
        prev_row = conn.execute(
            """SELECT summary FROM stock_analyses
               WHERE ticker=? AND status='ok' AND summary IS NOT NULL AND generated_at_ms>=?
               ORDER BY generated_at_ms DESC LIMIT 1""",
            (ticker, now - PREV_SUMMARY_MAX_AGE_MS),
        ).fetchone()
        prepared.append({
            **context,
            "prev_summary": prev_row["summary"].strip()[:PREV_SUMMARY_TRUNC] if prev_row else None,
            "base_cols": analysis_cols(
                ticker, items, settings, context["score"], run_id, now, settings.LLM_MODEL,
            ),
        })

    if len(prepared) == 1:
        context = prepared[0]
        results[context["ticker"]] = analyze_ticker(
            conn, context["ticker"], settings, context["name_cn"], context["score"],
            run_id, now, force, context["asset_type"],
        )
        return results
    if not prepared:
        return results

    system, user = build_batch_prompt(
        prepared, settings.SUMMARY_LANG, now, settings.FRESH_WINDOW_HOURS,
    )
    expected = {context["ticker"] for context in prepared}
    last_err = None
    for attempt in range(2):
        try:
            resp = _call_llm(settings, system, user, max_tokens=min(8000, 2000 * len(prepared)))
            batch = BatchAnalysisResult.model_validate_json(resp.choices[0].message.content)
            tickers = [analysis.ticker.upper() for analysis in batch.analyses]
            if len(tickers) != len(set(tickers)) or set(tickers) != expected:
                raise ValueError(f"batch tickers mismatch: expected {sorted(expected)}, got {tickers}")
            analyses = {analysis.ticker.upper(): analysis for analysis in batch.analyses}
        except Exception as e:
            last_err = e
            log.warning("batch analysis attempt %d failed for %s: %s", attempt + 1, sorted(expected), e)
            if attempt == 0:
                time.sleep(1)
        else:
            break
    else:
        log.warning("batch analysis failed; falling back to isolated requests: %s", last_err)
        for context in prepared:
            ticker = context["ticker"]
            results[ticker] = analyze_ticker(
                conn, ticker, settings, context["name_cn"], context["score"],
                run_id, now, force, context["asset_type"],
            )
            time.sleep(0.5)
        return results

    weights = [
        max(1, sum(len(item.get("prompt_text") or item["text"]) for item in context["items"]))
        for context in prepared
    ]
    input_tokens = _allocate_usage(resp.usage.prompt_tokens, weights)
    output_tokens = _allocate_usage(resp.usage.completion_tokens, weights)
    for index, context in enumerate(prepared):
        ticker = context["ticker"]
        input_count = input_tokens[index]
        output_count = output_tokens[index]
        cost = (input_count * COST_IN_PER_M + output_count * COST_OUT_PER_M) / 1e6
        try:
            store_result(
                conn, ticker, context["items"], analyses[ticker], context["base_cols"],
                input_tokens=input_count, output_tokens=output_count, cost_usd=round(cost, 6),
            )
        except Exception as e:
            log.warning("%s: storing batched analysis failed: %s", ticker, e)
            try:
                insert_analysis(conn, context["base_cols"], "error", error=str(e)[:500])
            except Exception:
                log.exception("%s: storing the batched analysis error row also failed", ticker)
            results[ticker] = "error"
        else:
            results[ticker] = "ok"
    return results


def analyze_all(conn, settings, dict_data: dict, stats: dict[str, dict],
                tracked: set[str], min_mentions: int, max_stocks: int,
                run_id: int | None = None, now: int | None = None,
                force: bool = False, max_indexes: int = 3,
                max_investments: int = 5,
                progress=None, cancel_event=None) -> dict[str, str]:
    report = progress or (lambda stage=None, **kw: None)
    names = {s["ticker"]: s.get("name_cn", "") for s in dict_data.get("stocks", [])}
    classes = asset_classes(dict_data)
    indexes = index_tickers(dict_data)
    investments = investment_tickers(dict_data)
    non_stocks = indexes | investments
    # Budgeted separately, or 纳指 and 标普 would take most of the stock budget on score alone.
    ranked = sorted(
        (e for e in stats.values()
         if e["ticker"] not in non_stocks and is_rankable(e, min_mentions)),
        key=lambda e: e["score"], reverse=True,
    )[:max_stocks]
    ranked += sorted(
        (e for e in stats.values()
         if e["ticker"] in indexes and e.get("mentions", 0) >= min_mentions),
        key=lambda e: e["score"], reverse=True,
    )[:max_indexes]
    ranked += sorted(
        (e for e in stats.values()
         if e["ticker"] in investments and e.get("mentions", 0) >= min_mentions),
        key=lambda e: e["score"], reverse=True,
    )[:max_investments]
    candidates = [e["ticker"] for e in ranked]
    for t in sorted(tracked):
        if t not in candidates and stats.get(t, {}).get("mentions", 0) >= 1:
            candidates.append(t)

    results = {}
    total = len(candidates)

    def cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    if settings.DEEPSEEK_API_KEY:
        now = now or now_ms()
        contexts = [{
            "ticker": ticker,
            "name_cn": names.get(ticker, ""),
            "score": stats.get(ticker, {}).get("score", 0.0),
            "asset_type": classes.get(ticker, "stock"),
            "items": gather_items(conn, ticker, settings.fresh_window_ms, now),
        } for ticker in candidates]
        groups = shared_evidence_groups(contexts)
        log.info("DeepSeek analysis groups: %s", [[c["ticker"] for c in group] for group in groups])
        for group in groups:
            if cancelled():
                log.info("analyze_all cancelled after %d/%d tickers", len(results), total)
                break
            report(done=len(results), total=total, ticker=group[0]["ticker"])
            if len(group) == 1:
                context = group[0]
                results[context["ticker"]] = analyze_ticker(
                    conn, context["ticker"], settings, context["name_cn"], context["score"],
                    run_id, now, force, context["asset_type"],
                )
            else:
                results.update(analyze_tickers_batch(conn, group, settings, run_id, now, force))
            report(done=len(results), total=total)
            time.sleep(0.5)
    else:
        for ticker in candidates:
            if cancelled():
                log.info("analyze_all cancelled after %d/%d tickers", len(results), total)
                break
            report(done=len(results), total=total, ticker=ticker)
            results[ticker] = analyze_ticker(
                conn, ticker, settings, names.get(ticker, ""),
                stats.get(ticker, {}).get("score", 0.0), run_id, now, force,
                classes.get(ticker, "stock"),
            )
            report(done=len(results), total=total)
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
    classes = mentions.asset_classes(dict_data)
    stats = scoring.compute_stats(
        conn, default_settings.fresh_window_ms,
        indexes=mentions.non_stock_tickers(dict_data),
    )
    t = args.ticker.upper()
    status = analyze_ticker(conn, t, default_settings, names.get(t, ""),
                            stats.get(t, {}).get("score", 0.0), force=args.force,
                            asset_type=classes.get(t, "stock"))
    print(f"{t}: {status}")
    row = conn.execute(
        "SELECT status, summary FROM stock_analyses WHERE ticker=? ORDER BY generated_at_ms DESC LIMIT 1",
        (t,),
    ).fetchone()
    if row:
        print(row["status"], "-", (row["summary"] or "")[:200])
