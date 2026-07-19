from types import SimpleNamespace

from conftest import MAC_UA

from infinance.providers.mediacrawler import clear_cookie_config, patch_config, set_cookie_config

WIN_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/149.0.0.0 Safari/537.36"


def settings(intl=False, ua=WIN_UA):
    return SimpleNamespace(XHS_INTERNATIONAL=intl, BROWSER_USER_AGENT=ua)


def test_patch_config_switches_to_rednote_backend(make_vendor):
    mc = make_vendor()
    patch_config(mc, settings(intl=True))
    assert "XHS_INTERNATIONAL = True" in (mc / "config" / "base_config.py").read_text(encoding="utf-8")


def test_patch_config_replaces_mac_ua_and_leaves_comment_alone(make_vendor):
    mc = make_vendor()
    patch_config(mc, settings())
    core = (mc / "media_platform" / "xhs" / "core.py").read_text(encoding="utf-8")
    assert f'self.user_agent = "{WIN_UA}"' in core
    assert MAC_UA not in core
    assert "# self.user_agent = utils.get_user_agent()" in core


def test_user_agent_repatches_when_changed(make_vendor):
    mc = make_vendor()
    patch_config(mc, settings())
    patch_config(mc, settings(ua="CustomUA/1.0"))
    core = (mc / "media_platform" / "xhs" / "core.py").read_text(encoding="utf-8")
    assert 'self.user_agent = "CustomUA/1.0"' in core
    assert core.count("self.user_agent = ") == 2  # the commented line + the real one


def test_patch_config_applies_base_patches(make_vendor):
    mc = make_vendor()
    patch_config(mc, settings())
    base = (mc / "config" / "base_config.py").read_text(encoding="utf-8")
    assert "ENABLE_CDP_MODE = False" in base
    assert "CRAWLER_MAX_SLEEP_SEC = 3" in base
    assert 'SORT_TYPE = "time_descending"' in (mc / "config" / "xhs_config.py").read_text(encoding="utf-8")
    assert "set_default_timeout(120_000)" in (mc / "media_platform" / "xhs" / "core.py").read_text(encoding="utf-8")


def test_patch_config_is_idempotent(make_vendor):
    mc = make_vendor()
    patch_config(mc, settings())
    patch_config(mc, settings())
    base = (mc / "config" / "base_config.py").read_text(encoding="utf-8")
    assert base.count("ENABLE_CDP_MODE") == 1
    core = (mc / "media_platform" / "xhs" / "core.py").read_text(encoding="utf-8")
    assert core.count("set_default_timeout(120_000)") == 1


def test_cookie_config_roundtrip_survives_quotes_and_newlines(make_vendor):
    mc = make_vendor()
    nasty = 'a1=x"y\\z; web_session=00\n0abc'
    set_cookie_config(mc, nasty)
    base_path = mc / "config" / "base_config.py"
    text = base_path.read_text(encoding="utf-8")
    # the written line is a valid Python literal equal to the sanitized cookie
    line = next(ln for ln in text.splitlines() if ln.startswith("COOKIES"))
    stored = eval(line.split("=", 1)[1].strip())  # noqa: S307 - literal we just wrote
    assert stored == 'a1=x"y\\z; web_session=00 0abc'

    clear_cookie_config(mc)
    text = base_path.read_text(encoding="utf-8")
    assert 'COOKIES = ""' in text
    assert "web_session" not in text
