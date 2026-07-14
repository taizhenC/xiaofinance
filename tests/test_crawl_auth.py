from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.crawler_runner import run_crawl


def fake_settings(tmp_path, cookies=""):
    return SimpleNamespace(
        MEDIACRAWLER_DIR=tmp_path, UV_EXE="uv", XHS_COOKIES=cookies,
        ENABLE_SUB_COMMENTS=False, BROWSER_HEADLESS=True,
        MAX_NOTES_PER_KEYWORD=20, MAX_COMMENTS_PER_NOTE=20, CRAWL_TIMEOUT_MIN=1,
    )


def captured_cmd(tmp_path, cookies):
    with patch("app.crawler_runner.patch_config"), patch("subprocess.Popen") as popen:
        popen.return_value.returncode = 0
        popen.return_value.wait.return_value = 0
        run_crawl(["美股"], tmp_path / "run", fake_settings(tmp_path, cookies))
        return popen.call_args[0][0]


def test_qr_login_when_no_cookies(tmp_path):
    cmd = captured_cmd(tmp_path, "")
    assert "qrcode" in cmd
    assert "--cookies" not in cmd


def test_cookie_login_when_cookies_set(tmp_path):
    cmd = captured_cmd(tmp_path, "  a1=abc; web_session=xyz  ")
    assert cmd[cmd.index("--lt") + 1] == "cookie"
    # whitespace-stripped, passed as a single argv entry (never shell-interpolated)
    assert cmd[cmd.index("--cookies") + 1] == "a1=abc; web_session=xyz"
    assert "qrcode" not in cmd
