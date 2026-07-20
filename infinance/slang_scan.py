import logging
import re

from pydantic import BaseModel, Field

from .mentions import Matcher
from .util import norm_text, now_ms

log = logging.getLogger(__name__)

MAX_NOTES = 50
TICKER_RE = re.compile(r"^[A-Z]{1,5}$")


class Suggestion(BaseModel):
    item: int = 0
    term: str
    ticker: str
    evidence: str = ""


class ScanResult(BaseModel):
    suggestions: list[Suggestion] = Field(default_factory=list)


def _known_terms(dict_data: dict) -> set[str]:
    known = set()
    for s in dict_data.get("stocks", []):
        known.add(s["ticker"].lower())
        for a in s.get("aliases", []) + s.get("ambiguous", []):
            known.add(a.lower())
    return known


def run_slang_scan(conn, settings, dict_data: dict, now: int | None = None) -> dict:
    """Mine fresh finance-context notes that matched NO ticker for censorship-dodging
    nicknames (谐音/黑话). Results go to alias_suggestions for human review — never auto-added."""
    if not settings.DEEPSEEK_API_KEY:
        return {"skipped": "no_api_key"}
    now = now or now_ms()
    cutoff = now - settings.fresh_window_ms
    matcher = Matcher(dict_data)

    rows = conn.execute(
        """SELECT note_id, title, note_desc, liked_count FROM notes
           WHERE publish_time_ms >= ? AND dup_group_id IS NULL
             AND NOT EXISTS (SELECT 1 FROM stock_mentions m
                             WHERE m.source_type='note' AND m.source_id = notes.note_id)
           ORDER BY liked_count DESC LIMIT 200""",
        (cutoff,),
    ).fetchall()
    candidates = []
    for r in rows:
        text = f"{r['title'] or ''} {r['note_desc'] or ''}".strip()
        if text and matcher.has_context(norm_text(text).lower()):
            candidates.append({"note_id": r["note_id"], "text": text[:200]})
        if len(candidates) >= MAX_NOTES:
            break
    if not candidates:
        return {"candidates": 0, "suggestions": 0}

    lines = "\n".join(f"{i + 1}. {c['text']}" for i, c in enumerate(candidates))
    system = "你研究中文社交平台上规避审核的股票黑话、谐音梗和绰号。"
    user = f"""以下小红书帖子涉及投资/股票话题，但没有匹配到任何已知的美股代码或公司别名。
判断其中是否有帖子用绰号、谐音、拼音缩写或黑话指代某只具体的美股上市公司或ETF。只报告你有把握的，宁缺毋滥。

{lines}

只输出一个JSON对象：{{"suggestions": [{{"item": 帖子编号, "term": "文中使用的词", "ticker": "对应的美股代码(大写)", "evidence": "原文片段"}}]}}
没有发现则返回 {{"suggestions": []}}"""

    from openai import OpenAI

    client = OpenAI(api_key=settings.DEEPSEEK_API_KEY, base_url=settings.LLM_BASE_URL)
    try:
        resp = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            max_tokens=1500,
            temperature=0.2,
        )
        result = ScanResult.model_validate_json(resp.choices[0].message.content)
    except Exception as e:
        log.warning("slang scan failed: %s", e)
        return {"error": str(e)[:200]}

    known = _known_terms(dict_data)
    inserted = 0
    for s in result.suggestions:
        term = s.term.strip()
        ticker = s.ticker.strip().upper()
        if not term or not TICKER_RE.match(ticker):
            continue
        if term.lower() in known or term.upper() == ticker:
            continue
        note_id = None
        if 1 <= s.item <= len(candidates):
            note_id = candidates[s.item - 1]["note_id"]
        cur = conn.execute(
            """INSERT OR IGNORE INTO alias_suggestions
               (term, guessed_ticker, evidence_quote, evidence_note_id, suggested_at_ms)
               VALUES(?,?,?,?,?)""",
            (term, ticker, s.evidence.strip()[:300], note_id, now),
        )
        inserted += cur.rowcount
    conn.commit()
    log.info("slang scan: %d candidates, %d new suggestions", len(candidates), inserted)
    return {"candidates": len(candidates), "suggestions": inserted}
