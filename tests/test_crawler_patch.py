from types import SimpleNamespace

from conftest import MAC_UA

from infinance.providers.mediacrawler import clear_cookie_config, patch_config, set_cookie_config

WIN_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/149.0.0.0 Safari/537.36"


def settings(intl=False, ua=WIN_UA, sleep=8, max_notes=10, headless=True):
    return SimpleNamespace(
        XHS_INTERNATIONAL=intl, BROWSER_USER_AGENT=ua, CRAWL_SLEEP_SEC=sleep,
        MAX_NOTES_PER_KEYWORD=max_notes, BROWSER_HEADLESS=headless,
    )


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
    assert "ENABLE_CDP_MODE = True" in base
    assert "CDP_HEADLESS = True" in base
    assert 'SORT_TYPE = "time_descending"' in (mc / "config" / "xhs_config.py").read_text(encoding="utf-8")
    assert "set_default_timeout(120_000)" in (mc / "media_platform" / "xhs" / "core.py").read_text(encoding="utf-8")


def test_login_can_request_a_visible_browser(make_vendor):
    mc = make_vendor()
    patch_config(mc, settings(), browser_headless=False)
    base = (mc / "config" / "base_config.py").read_text(encoding="utf-8")
    assert "CDP_HEADLESS = False" in base


def test_request_rate_comes_from_settings(make_vendor):
    """The throttle is the one lever against XHS risk control — it must not be hardcoded."""
    mc = make_vendor()
    patch_config(mc, settings(sleep=12))
    assert "CRAWLER_MAX_SLEEP_SEC = 12" in (mc / "config" / "base_config.py").read_text(encoding="utf-8")


def test_index_navigation_waits_only_for_domcontentloaded(make_vendor):
    mc = make_vendor()
    patch_config(mc, settings())
    core = (mc / "media_platform" / "xhs" / "core.py").read_text(encoding="utf-8")
    assert 'goto(self.index_url, wait_until="domcontentloaded")' in core


def test_patch_config_is_idempotent(make_vendor):
    mc = make_vendor()
    patch_config(mc, settings())
    patch_config(mc, settings())
    base = (mc / "config" / "base_config.py").read_text(encoding="utf-8")
    assert base.count("ENABLE_CDP_MODE") == 1
    core = (mc / "media_platform" / "xhs" / "core.py").read_text(encoding="utf-8")
    assert core.count("set_default_timeout(120_000)") == 1
    assert core.count("domcontentloaded") == 1
    assert core.count("[:10]") == 1
    client = (mc / "media_platform" / "xhs" / "client.py").read_text(encoding="utf-8")
    assert client.count("inline replies only") == 1


def test_details_and_comments_are_sliced_to_the_newest_n_notes(make_vendor):
    """The search page is 1 request for a fixed 20 notes; the details and comments behind
    it are the other ~97%. Slicing there is what lets MAX_NOTES_PER_KEYWORD < 20 cut a
    keyword's cost — MediaCrawler itself rounds the count cap up to a full page."""
    mc = make_vendor()
    patch_config(mc, settings(max_notes=10))
    core = (mc / "media_platform" / "xhs" / "core.py").read_text(encoding="utf-8")
    assert 'for post_item in notes_res.get("items", {})[:10] if' in core


def test_detail_slice_repatches_when_n_changes(make_vendor):
    mc = make_vendor()
    patch_config(mc, settings(max_notes=10))
    patch_config(mc, settings(max_notes=8))
    core = (mc / "media_platform" / "xhs" / "core.py").read_text(encoding="utf-8")
    assert "[:8]" in core and "[:10]" not in core
    assert core.count("for post_item in notes_res") == 1


def test_inline_replies_are_kept_but_never_paged_for(make_vendor):
    """The free half of sub-comments, without the half that walls the account: XHS nests the
    first replies in the parent's own response, so keep those and never call back for more."""
    mc = make_vendor()
    patch_config(mc, settings())
    client = (mc / "media_platform" / "xhs" / "client.py").read_text(encoding="utf-8")

    assert "await callback(note_id, sub_comments)" in client  # inline replies still stored
    assert "continue  # xiaofinance: inline replies only" in client
    # the paging loop's guard is gone, so the request-per-comment loop is unreachable
    assert 'sub_comment_has_more = comment.get("sub_comment_has_more")' not in client
    body = client.split("inline replies only, never page for more")[1]
    assert "get_note_sub_comments" in body  # still present, just dead — we only cut the path


def test_a_walled_461_without_verifytype_header_cannot_crash_the_detector(make_vendor):
    """Run 16: XHS answered 461 with no Verifytype header, headers[] raised KeyError before
    "CAPTCHA appeared" was logged, and the run died anonymously. The detector must survive
    the very response it exists to detect."""
    mc = make_vendor()
    patch_config(mc, settings())
    client = (mc / "media_platform" / "xhs" / "client.py").read_text(encoding="utf-8")
    assert 'response.headers["Verifytype"]' not in client
    assert 'response.headers.get("Verifytype", "unknown")' in client
    assert 'response.headers.get("Verifyuuid", "unknown")' in client


def test_a_failed_comment_fetch_skips_the_note_instead_of_killing_the_run(make_vendor):
    """One bad response in batch_get_note_comments propagates through asyncio.gather and
    aborts every keyword still in the queue — run 16 lost 3 of its 6 to that."""
    mc = make_vendor()
    patch_config(mc, settings())
    core = (mc / "media_platform" / "xhs" / "core.py").read_text(encoding="utf-8")
    assert "try:\n                await self.xhs_client.get_note_all_comments(" in core
    assert "except Exception as ex:" in core
    assert "skipping" in core


def test_reused_browser_context_closes_its_leftover_default_tab(make_vendor):
    """Chrome always opens its own New Tab Page in the context CDP reuses; MediaCrawler
    then opens a second page for XHS and never touches the first, leaving an unrelated
    tab open for the whole crawl. The cleanup must run after new_page(), not before —
    closing a real Chrome window's last tab first would close the window itself."""
    mc = make_vendor()
    patch_config(mc, settings())
    core = (mc / "media_platform" / "xhs" / "core.py").read_text(encoding="utf-8")
    new_page_pos = core.index("self.context_page = await self.browser_context.new_page()")
    cleanup_pos = core.index("for leftover_page in self.browser_context.pages:")
    assert new_page_pos < cleanup_pos
    assert "if leftover_page is not self.context_page:" in core
    assert "await leftover_page.close()" in core


def test_chrome_profile_is_set_to_keep_session_cookies_across_restarts(make_vendor):
    """XHS's login cookie is session-scoped, and each keyword launches a brand-new Chrome
    process against the same profile — Chromium purges session cookies on every fresh
    launch unless the profile says to continue where it left off, so without this the
    account was logging out before the next keyword's launch."""
    import json

    mc = make_vendor()
    patch_config(mc, settings())
    prefs = json.loads(
        (mc / "browser_data" / "cdp_xhs_user_data_dir" / "Default" / "Preferences").read_text(
            encoding="utf-8"
        )
    )
    assert prefs["session"]["restore_on_startup"] == 1


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
