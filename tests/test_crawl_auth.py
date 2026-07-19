"""Cookie hygiene (TR-03): the session cookie must reach MediaCrawler through
its config file, never through argv (visible in the OS process list), and must
be cleared from the vendor checkout when the run ends."""

from types import SimpleNamespace

from app.providers.base import SearchRequest
from app.providers.mediacrawler import MediaCrawlerProvider

COOKIE = "a1=abc; web_session=xyz"


def fake_settings(vendor, cookies=""):
    return SimpleNamespace(
        MEDIACRAWLER_DIR=vendor, UV_EXE="uv", XHS_COOKIES=cookies,
        XHS_INTERNATIONAL=False, BROWSER_USER_AGENT="UA/1.0",
        ENABLE_SUB_COMMENTS=False,
        MAX_NOTES_PER_KEYWORD=20, MAX_COMMENTS_PER_NOTE=20, CRAWL_TIMEOUT_MIN=1,
    )


def request(tmp_path):
    return SearchRequest(
        keywords=["美股"], run_dir=tmp_path / "run",
        max_notes_per_keyword=20, max_comments_per_note=20,
        include_sub_comments=False, timeout_min=1,
    )


def run_search(tmp_path, make_vendor, cookies=""):
    """Run provider.search with a fake spawn; capture argv and the vendor
    config as it looked while the subprocess would have been alive."""
    vendor = make_vendor()
    provider = MediaCrawlerProvider(fake_settings(vendor, cookies))
    seen = {}

    def spawn(cmd, log_path, timeout_s):
        seen["cmd"] = cmd
        seen["config_during_run"] = (vendor / "config" / "base_config.py").read_text(encoding="utf-8")
        log_path.write_text("update_xhs_note ok\n", encoding="utf-8")
        return 0, False

    provider._spawn = spawn
    result = provider.search(request(tmp_path))
    seen["config_after_run"] = (vendor / "config" / "base_config.py").read_text(encoding="utf-8")
    seen["result"] = result
    return seen


def test_qr_login_when_no_cookies(tmp_path, make_vendor):
    seen = run_search(tmp_path, make_vendor, cookies="")
    cmd = seen["cmd"]
    assert cmd[cmd.index("--lt") + 1] == "qrcode"
    assert "--cookies" not in cmd


def test_cookie_login_goes_through_config_not_argv(tmp_path, make_vendor):
    seen = run_search(tmp_path, make_vendor, cookies=f"  {COOKIE}  ")
    cmd = seen["cmd"]
    assert cmd[cmd.index("--lt") + 1] == "cookie"
    assert "--cookies" not in cmd
    # the live cookie appears in no argv element at all
    assert all(COOKIE not in part for part in cmd)
    # ...but was present in the vendor config while the crawl ran
    assert COOKIE in seen["config_during_run"]


def test_cookie_cleared_from_vendor_config_after_run(tmp_path, make_vendor):
    seen = run_search(tmp_path, make_vendor, cookies=COOKIE)
    assert COOKIE not in seen["config_after_run"]
    assert 'COOKIES = ""' in seen["config_after_run"]


def test_cookie_cleared_even_when_spawn_raises(tmp_path, make_vendor):
    vendor = make_vendor()
    provider = MediaCrawlerProvider(fake_settings(vendor, COOKIE))

    def spawn(cmd, log_path, timeout_s):
        raise RuntimeError("boom")

    provider._spawn = spawn
    try:
        provider.search(request(tmp_path))
    except RuntimeError:
        pass
    text = (vendor / "config" / "base_config.py").read_text(encoding="utf-8")
    assert COOKIE not in text


def test_search_returns_structured_result(tmp_path, make_vendor):
    seen = run_search(tmp_path, make_vendor)
    r = seen["result"]
    assert r.exit_code == 0
    assert r.timed_out is False
    assert r.cancelled is False
    assert r.log_path == tmp_path / "run" / "crawler.log"
