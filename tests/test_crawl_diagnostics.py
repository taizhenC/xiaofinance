from app.crawler_runner import _CaptchaWatcher, crawl_progress, failure_reason

CAPTCHA = (
    "2026-07-11 21:11:09 MediaCrawler ERROR (client.py:140) - CAPTCHA appeared, request "
    "failed, Verifytype: 216, Verifyuuid: sg__02dcc8ec, Response: <Response [461]>\n"
)


def test_captcha_storm_is_named_not_reported_as_exit_code_1(tmp_path):
    log = tmp_path / "crawler.log"
    log.write_text(CAPTCHA * 192 + "tenacity.RetryError\n", encoding="utf-8")
    reason = failure_reason(log, 1)
    assert "192" in reason and "rate-limit" in reason


def test_a_verifytype_keyerror_is_named_risk_control_not_retries_ran_out(tmp_path):
    """Run 16's log: a 461 without the Verifytype header crashed the CAPTCHA detector, so
    zero "CAPTCHA appeared" lines exist — but the KeyError itself proves the 461 happened."""
    log = tmp_path / "crawler.log"
    log.write_text(
        "  File \"...client.py\", line 137, in request\n"
        "    verify_type = response.headers[\"Verifytype\"]\n"
        "KeyError: 'Verifytype'\n"
        "tenacity.RetryError: RetryError[<Future at 0x1 state=finished raised KeyError>]\n",
        encoding="utf-8",
    )
    reason = failure_reason(log, 1)
    assert "risk control" in reason and "Verifytype" in reason
    assert "retries ran out" not in reason


def test_network_failure_is_not_mistaken_for_risk_control(tmp_path):
    log = tmp_path / "crawler.log"
    log.write_text("httpx.ConnectError: connection refused\n", encoding="utf-8")
    assert "ConnectError" in failure_reason(log, 1)


def test_unrecognised_failure_still_says_something(tmp_path):
    log = tmp_path / "crawler.log"
    log.write_text("ValueError: boom\n", encoding="utf-8")
    assert failure_reason(log, 1) == "crawler exit code 1"


def test_watcher_counts_only_what_is_new(tmp_path):
    log = tmp_path / "crawler.log"
    log.write_text(CAPTCHA * 3, encoding="utf-8")
    w = _CaptchaWatcher(log)
    assert w.poll() == 3
    assert w.poll() == 3  # nothing new — no double counting
    with open(log, "a", encoding="utf-8") as f:
        f.write(CAPTCHA * 2)
    assert w.poll() == 5


def test_watcher_catches_a_marker_split_across_two_reads(tmp_path):
    """The log is read while the crawler writes it, so a marker lands astride a boundary."""
    log = tmp_path / "crawler.log"
    head, tail = CAPTCHA[:70], CAPTCHA[70:]
    log.write_text(head, encoding="utf-8")
    w = _CaptchaWatcher(log)
    w.poll()
    with open(log, "a", encoding="utf-8") as f:
        f.write(tail)
    assert w.poll() == 1


def test_progress_reads_the_crawler_own_artifacts(tmp_path):
    jsonl = tmp_path / "xhs" / "jsonl"
    jsonl.mkdir(parents=True)
    (jsonl / "search_contents_2026-07-11.jsonl").write_text("{}\n" * 51, encoding="utf-8")
    (jsonl / "search_comments_2026-07-11.jsonl").write_text("{}\n" * 96, encoding="utf-8")
    (tmp_path / "crawler.log").write_text(
        "[XiaoHongShuCrawler.search] Current search keyword: 美股\n"
        "[XiaoHongShuCrawler.search] Current search keyword: 美股财报\n" + CAPTCHA * 4,
        encoding="utf-8",
    )
    p = crawl_progress(tmp_path, ["美股", "美股财报", "中概股"])
    assert p["notes"] == 51 and p["comments"] == 96
    assert p["keyword"] == "美股财报"
    assert (p["keyword_index"], p["keyword_total"]) == (2, 3)
    assert p["captchas"] == 4


def test_progress_survives_a_run_that_has_not_written_anything_yet(tmp_path):
    p = crawl_progress(tmp_path, ["美股"])
    assert p["notes"] == 0 and p["keyword"] is None and p["keyword_index"] is None
    assert p["phase"] == "starting" and p["last_activity_ms"] is None
    assert [k["notes"] for k in p["per_keyword"]] == [0]


def test_progress_attributes_counts_to_keywords_and_names_the_phase(tmp_path):
    """Totals alone can't say where a 30-minute crawl is; the keyword segments can."""
    jsonl = tmp_path / "xhs" / "jsonl"
    jsonl.mkdir(parents=True)
    (jsonl / "search_contents_2026-07-12.jsonl").write_text(
        '{"note_id": "n1", "source_keyword": "美股"}\n'
        '{"note_id": "n2", "source_keyword": "美股"}\n'
        '{"note_id": "n3", "source_keyword": "美股财报"}\n',
        encoding="utf-8",
    )
    # comments carry no keyword of their own — they must inherit the note's
    (jsonl / "search_comments_2026-07-12.jsonl").write_text(
        '{"comment_id": "c1", "note_id": "n1"}\n'
        '{"comment_id": "c2", "note_id": "n1"}\n'
        '{"comment_id": "c3", "note_id": "n3"}\n',
        encoding="utf-8",
    )
    (tmp_path / "crawler.log").write_text(
        "x INFO (core.py:140) - [XiaoHongShuCrawler.search] Current search keyword: 美股\n"
        "x INFO (__init__.py:130) - [store.xhs.update_xhs_note] xhs note: {}\n"
        "x INFO (core.py:140) - [XiaoHongShuCrawler.search] Current search keyword: 美股财报\n"
        "x INFO (core.py:348) - [XiaoHongShuCrawler.get_comments] Begin get note id comments n3\n",
        encoding="utf-8",
    )
    p = crawl_progress(tmp_path, ["美股", "美股财报", "中概股"])
    per = {k["keyword"]: k for k in p["per_keyword"]}
    assert per["美股"]["notes"] == 2 and per["美股"]["comments"] == 2
    assert per["美股财报"]["notes"] == 1 and per["美股财报"]["comments"] == 1
    assert per["中概股"]["notes"] == 0 and per["中概股"]["comments"] == 0
    assert p["phase"] == "comments"
    assert p["kw_comment_notes_done"] == 1  # one note of the current keyword reached comments
    assert p["last_activity_ms"] is not None


def test_progress_surfaces_the_last_error_line(tmp_path):
    (tmp_path / "crawler.log").write_text(
        "2026-07-12 01:00:00 MediaCrawler ERROR (client.py:140) - first error\n"
        "2026-07-12 01:05:00 MediaCrawler INFO (core.py:1) - fine\n"
        "2026-07-12 01:10:00 MediaCrawler ERROR (core.py:355) - comments failed for note n9, skipping: KeyError\n",
        encoding="utf-8",
    )
    p = crawl_progress(tmp_path, ["美股"])
    assert p["last_error"].startswith("comments failed for note n9")
