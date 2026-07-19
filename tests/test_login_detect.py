from types import SimpleNamespace

from infinance.providers.base import SessionState
from infinance.providers.mediacrawler import MediaCrawlerProvider


def provider(tmp_path):
    return MediaCrawlerProvider(SimpleNamespace(MEDIACRAWLER_DIR=tmp_path))


def write(tmp_path, text):
    p = tmp_path / "crawler.log"
    p.write_text(text, encoding="utf-8")
    return p


def test_expired_session_flagged_even_when_notes_were_fetched(tmp_path):
    # a session can die partway: the crawl banks some notes, then the platform kicks it
    log = write(tmp_path, "update_xhs_note ok\nDataFetchError: 登录已过期\n")
    assert provider(tmp_path).login_looks_required(log, notes_fresh=39) is True


def test_healthy_run_with_notes_is_not_flagged(tmp_path):
    log = write(tmp_path, "login_by_qrcode Begin login\nupdate_xhs_note ok\n")
    assert provider(tmp_path).login_looks_required(log, notes_fresh=20) is False


def test_empty_run_mentioning_login_is_flagged(tmp_path):
    log = write(tmp_path, "login failed , have not found qrcode\n")
    assert provider(tmp_path).login_looks_required(log, notes_fresh=0) is True


def test_missing_log_is_not_flagged(tmp_path):
    assert provider(tmp_path).login_looks_required(tmp_path / "nope.log", notes_fresh=0) is False


def test_classify_log_distinguishes_the_failure_classes(tmp_path):
    p = provider(tmp_path)
    assert p.classify_log("DataFetchError: 登录已过期") == SessionState.EXPIRED
    assert p.classify_log("您当前登录的账号没有权限访问该内容") == SessionState.UNAUTHORIZED
    assert p.classify_log("update_xhs_note ok") == SessionState.UNKNOWN
    # unauthorized wins over expired: it is the more specific diagnosis
    assert p.classify_log("登录已过期\n没有权限访问") == SessionState.UNAUTHORIZED
