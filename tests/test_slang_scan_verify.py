"""AN-01 in the slang scanner: a suggested term must appear verbatim in a
scanned note, and its evidence quote must be verbatim too — otherwise the
evidence is replaced with the real note text, or the suggestion is dropped."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from infinance.mentions import load_stock_dict
from infinance.slang_scan import _known_terms, run_slang_scan
from infinance.util import now_ms

H = 3_600_000

# A nonce slang nickname that must NOT already be a dictionary alias — otherwise
# the scanner correctly skips it as known, and these tests would be vacuous.
NONCE = "薛定谔厂"


@pytest.fixture(autouse=True)
def _nonce_is_novel():
    assert NONCE.lower() not in _known_terms(load_stock_dict()), (
        f"{NONCE} has entered the dictionary — pick a different nonce for these tests"
    )


def seed_notes(conn, now):
    rows = [
        # finance context (股价/美股 are context words), matches no known ticker
        ("s1", "这家公司股价又崩了", f"'{NONCE}'真不行，美股避雷", 50),
        ("s2", "今天买了点大盘", "美股股价还行", 10),
    ]
    for note_id, title, desc, likes in rows:
        conn.execute(
            "INSERT INTO notes(note_id, title, note_desc, publish_time_ms, liked_count)"
            " VALUES(?,?,?,?,?)",
            (note_id, title, desc, now - H, likes),
        )
    conn.commit()


def scan_with_llm_reply(conn, reply: dict):
    settings = SimpleNamespace(DEEPSEEK_API_KEY="k", LLM_MODEL="m", LLM_BASE_URL="u",
                               fresh_window_ms=24 * H)
    msg = SimpleNamespace(content=json.dumps(reply, ensure_ascii=False))
    resp = SimpleNamespace(choices=[SimpleNamespace(message=msg)])
    with patch("openai.OpenAI") as cls:
        cls.return_value.chat.completions.create.return_value = resp
        return run_slang_scan(conn, settings, load_stock_dict(), now=now_ms())


def suggestions(conn):
    return conn.execute("SELECT * FROM alias_suggestions").fetchall()


def test_verbatim_term_with_verbatim_evidence_is_kept(conn):
    now = now_ms()
    seed_notes(conn, now)
    scan_with_llm_reply(conn, {"suggestions": [
        {"item": 1, "term": NONCE, "ticker": "ZZZZ", "evidence": f"'{NONCE}'真不行"},
    ]})
    rows = suggestions(conn)
    assert len(rows) == 1
    assert rows[0]["term"] == NONCE
    assert rows[0]["evidence_quote"] == f"'{NONCE}'真不行"
    assert rows[0]["evidence_note_id"] == "s1"


def test_fabricated_term_is_dropped(conn):
    now = now_ms()
    seed_notes(conn, now)
    scan_with_llm_reply(conn, {"suggestions": [
        {"item": 1, "term": "幻觉厂牌", "ticker": "ZZZZ", "evidence": "编造的证据"},
    ]})
    assert suggestions(conn) == []


def test_paraphrased_evidence_replaced_with_real_note_text(conn):
    now = now_ms()
    seed_notes(conn, now)
    scan_with_llm_reply(conn, {"suggestions": [
        {"item": 1, "term": NONCE, "ticker": "ZZZZ", "evidence": "用户说这家公司完蛋了"},
    ]})
    rows = suggestions(conn)
    assert len(rows) == 1
    # the paraphrase never surfaces; the stored evidence is the actual note text
    assert "完蛋" not in rows[0]["evidence_quote"]
    assert NONCE in rows[0]["evidence_quote"]


def test_wrong_item_index_falls_back_to_the_note_containing_the_term(conn):
    now = now_ms()
    seed_notes(conn, now)
    scan_with_llm_reply(conn, {"suggestions": [
        {"item": 2, "term": NONCE, "ticker": "ZZZZ", "evidence": "x"},
    ]})
    rows = suggestions(conn)
    assert len(rows) == 1
    assert rows[0]["evidence_note_id"] == "s1"
