import argparse
import json
import logging
import time

from pydantic import BaseModel, Field, ValidationError

from .config import settings as default_settings
from .db import connect
from .dedup import comment_cluster_sizes, note_cluster_sizes
from .util import now_ms, sha256_hex

log = logging.getLogger(__name__)

MAX_ITEMS = 60
NOTE_TRUNC = 300
COMMENT_TRUNC = 150
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
    notable_quotes: list[str] = Field(default_factory=list)
    irrelevant_item_count: int = 0


def gather_items(conn, ticker: str, fresh_window_ms: int, now: int | None = None) -> list[dict]:
    """Canonical fresh items mentioning ticker, most-liked first, capped at MAX_ITEMS."""
    now = now or now_ms()
    cutoff = now - fresh_window_ms
    note_sizes = note_cluster_sizes(conn, fresh_window_ms, now)
    comment_sizes = comment_cluster_sizes(conn, fresh_window_ms, now)
    items = []
    for r in conn.execute(
        """SELECT n.note_id AS id, n.title, n.note_desc, n.liked_count AS likes,
                  n.publish_time_ms AS ts, n.note_url
           FROM stock_mentions m JOIN notes n ON n.note_id = m.source_id
           WHERE m.ticker=? AND m.source_type='note' AND m.content_time_ms>=?
             AND n.dup_group_id IS NULL""",
        (ticker, cutoff),
    ):
        text = f"{r['title'] or ''} {r['note_desc'] or ''}".strip()[:NOTE_TRUNC]
        items.append({"type": "note", "id": r["id"], "text": text, "likes": r["likes"],
                      "ts": r["ts"], "url": r["note_url"],
                      "cluster_size": note_sizes.get(r["id"], 1)})
    for r in conn.execute(
        """SELECT c.comment_id AS id, c.content, c.like_count AS likes, c.create_time_ms AS ts,
                  p.content AS parent_content
           FROM stock_mentions m JOIN comments c ON c.comment_id = m.source_id
           LEFT JOIN comments p ON p.comment_id = c.parent_comment_id
           WHERE m.ticker=? AND m.source_type='comment' AND m.content_time_ms>=?
             AND c.dup_group_id IS NULL""",
        (ticker, cutoff),
    ):
        text = (r["content"] or "").strip()
        if r["parent_content"]:
            # a bare reply ("同意楼上") is meaningless without its thread
            parent = " ".join((r["parent_content"] or "").split())[:30]
            text = f"回复「{parent}」: {text}"
        items.append({"type": "comment", "id": r["id"], "text": text[:COMMENT_TRUNC],
                      "likes": r["likes"], "ts": r["ts"], "url": None,
                      "cluster_size": comment_sizes.get(r["id"], 1)})
    items.sort(key=lambda i: i["likes"], reverse=True)
    return items[:MAX_ITEMS]


def input_hash(items: list[dict]) -> str:
    return sha256_hex("|".join(sorted(f"{i['type']}:{i['id']}" for i in items)))


def build_prompt(ticker: str, name_cn: str, items: list[dict], lang: str, now: int,
                 prev_summary: str | None = None) -> tuple[str, str]:
    lines = []
    for i in items:
        age_h = max(0, (now - i["ts"]) // 3_600_000)
        dup = f" [×{i['cluster_size']}相似]" if i["cluster_size"] > 1 else ""
        lines.append(f"[{i['type']}] [{age_h}小时前] [赞:{i['likes']}]{dup} {i['text']}")
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
    system = "你是一位资深美股分析师，负责从小红书帖子和评论中提炼散户对某只股票的真实看法。"
    user = f"""以下是过去24小时内小红书上提及 {name} 的帖子和评论，每行格式：[类型] [发布时间] [点赞数] 内容。
标注 [×N相似] 表示有N条相似的转发/复制内容已合并为一条——重复转发不代表更多独立观点。

{chr(10).join(lines)}
{prev_block}
请完成三步：
1. 剔除与 {ticker} 股票投资无关的条目（如水果苹果、单纯晒产品等），数量记为 irrelevant_item_count。
2. 对剩余条目逐条判断立场（bullish/bearish/neutral），汇总为 sentiment_counts。注意：内容相似或论点相同的条目只算一个观点，按不同论点的数量与质量权衡，不按重复次数。
3. 用{lang_name}写总结：summary 必须以 "{ticker}: " 开头（不超过120词），bull_points 最多4条看多要点，bear_points 最多4条看空要点（均用{lang_name}），notable_quotes 最多3条最有代表性的原文引用（保留中文原文）。{change_hint}

只输出一个JSON对象，格式：
{{"summary": "...", "sentiment_counts": {{"bullish": 0, "bearish": 0, "neutral": 0}}, "bull_points": ["..."], "bear_points": ["..."], "notable_quotes": ["..."], "irrelevant_item_count": 0}}"""
    return system, user


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


def analyze_ticker(conn, ticker: str, settings=None, name_cn: str = "", score: float = 0.0,
                   run_id: int | None = None, now: int | None = None, force: bool = False) -> str:
    settings = settings or default_settings
    now = now or now_ms()
    items = gather_items(conn, ticker, settings.fresh_window_ms, now)
    if not items:
        return "no_items"
    ihash = input_hash(items)
    note_count = sum(1 for i in items if i["type"] == "note")
    comment_count = len(items) - note_count

    last = conn.execute(
        "SELECT input_hash, status FROM stock_analyses WHERE ticker=? ORDER BY generated_at_ms DESC LIMIT 1",
        (ticker,),
    ).fetchone()
    # unchanged inputs → keep the previous row as "latest" instead of duplicating it
    if not force and last and last["input_hash"] == ihash and last["status"] in ("ok", "no_api_key"):
        log.info("%s: inputs unchanged, skipping", ticker)
        return "skipped_unchanged"

    base_cols = dict(
        ticker=ticker, run_id=run_id, generated_at_ms=now,
        window_start_ms=now - settings.fresh_window_ms, window_end_ms=now,
        note_count=note_count, comment_count=comment_count, popularity_score=score,
        input_item_count=len(items), input_hash=ihash, model=settings.LLM_MODEL,
    )

    def insert(status, **extra):
        cols = {**base_cols, **extra, "status": status}
        keys = ",".join(cols)
        conn.execute(
            f"INSERT INTO stock_analyses({keys}) VALUES({','.join('?' * len(cols))})",
            tuple(cols.values()),
        )
        conn.commit()

    if not settings.DEEPSEEK_API_KEY:
        quotes = [i["text"] for i in items if i["type"] == "comment"][:3] or [i["text"] for i in items[:3]]
        insert("no_api_key", notable_quotes=json.dumps(quotes, ensure_ascii=False))
        return "no_api_key"

    prev_row = conn.execute(
        """SELECT summary FROM stock_analyses WHERE ticker=? AND status='ok' AND summary IS NOT NULL
           AND generated_at_ms >= ? ORDER BY generated_at_ms DESC LIMIT 1""",
        (ticker, now - PREV_SUMMARY_MAX_AGE_MS),
    ).fetchone()
    prev_summary = prev_row["summary"].strip()[:PREV_SUMMARY_TRUNC] if prev_row else None

    system, user = build_prompt(ticker, name_cn, items, settings.SUMMARY_LANG, now, prev_summary)
    last_err = None
    for attempt in range(2):
        try:
            resp = _call_llm(settings, system, user)
            result = AnalysisResult.model_validate_json(resp.choices[0].message.content)
            summary = result.summary.strip()
            if not summary.upper().startswith(f"{ticker}:"):
                summary = f"{ticker}: {summary}"
            usage = resp.usage
            cost = (usage.prompt_tokens * COST_IN_PER_M + usage.completion_tokens * COST_OUT_PER_M) / 1e6
            insert(
                "ok",
                sentiment_counts=json.dumps(result.sentiment_counts.model_dump()),
                summary=summary,
                bull_points=json.dumps(result.bull_points[:4], ensure_ascii=False),
                bear_points=json.dumps(result.bear_points[:4], ensure_ascii=False),
                notable_quotes=json.dumps(result.notable_quotes[:3], ensure_ascii=False),
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
                run_id: int | None = None, now: int | None = None) -> dict[str, str]:
    names = {s["ticker"]: s.get("name_cn", "") for s in dict_data.get("stocks", [])}
    ranked = sorted(
        (e for e in stats.values() if e["mentions"] >= min_mentions),
        key=lambda e: e["score"], reverse=True,
    )[:max_stocks]
    candidates = [e["ticker"] for e in ranked]
    for t in sorted(tracked):
        if t not in candidates and stats.get(t, {}).get("mentions", 0) >= 1:
            candidates.append(t)

    results = {}
    for t in candidates:
        results[t] = analyze_ticker(
            conn, t, settings, names.get(t, ""), stats.get(t, {}).get("score", 0.0), run_id, now
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
