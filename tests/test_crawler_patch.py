from types import SimpleNamespace

from app.crawler_runner import patch_config


MAC_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/126.0.0.0 Safari/537.36"
WIN_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/149.0.0.0 Safari/537.36"


def fake_vendor(tmp_path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "xhs_config.py").write_text('SORT_TYPE = "general"\n', encoding="utf-8")
    (cfg / "base_config.py").write_text(
        "XHS_INTERNATIONAL = False\n"
        "ENABLE_CDP_MODE = False\n"
        "CDP_HEADLESS = True\n"
        "CDP_CONNECT_EXISTING = True\n"
        "CRAWLER_MAX_SLEEP_SEC = 2\n",
        encoding="utf-8",
    )
    core = tmp_path / "media_platform" / "xhs"
    core.mkdir(parents=True)
    (core / "core.py").write_text(
        "        # self.user_agent = utils.get_user_agent()\n"
        f'        self.user_agent = "{MAC_UA}"\n'
        "            self.context_page = await self.browser_context.new_page()\n"
        "            await self.context_page.goto(self.index_url)\n"
        '                        ) for post_item in notes_res.get("items", {}) '
        'if post_item.get("model_type") not in ("rec_query", "hot_query")\n'
        "            await self.xhs_client.get_note_all_comments(\n"
        "                note_id=note_id,\n"
        "                xsec_token=xsec_token,\n"
        "                crawl_interval=crawl_interval,\n"
        "                callback=xhs_store.batch_update_xhs_note_comments,\n"
        "                max_count=config.CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES,\n"
        "            )\n",
        encoding="utf-8",
    )
    (core / "client.py").write_text(
        "        if response.status_code == 471 or response.status_code == 461:\n"
        "            # someday someone maybe will bypass captcha\n"
        '            verify_type = response.headers["Verifytype"]\n'
        '            verify_uuid = response.headers["Verifyuuid"]\n'
        "\n"
        "        for comment in comments:\n"
        "            try:\n"
        '                sub_comments = comment.get("sub_comments")\n'
        "                if sub_comments and callback:\n"
        "                    await callback(note_id, sub_comments)\n"
        "\n"
        '                sub_comment_has_more = comment.get("sub_comment_has_more")\n'
        "                if not sub_comment_has_more:\n"
        "                    continue\n"
        "\n"
        '                root_comment_id = comment.get("id")\n'
        "                while sub_comment_has_more:\n"
        "                    comments_res = await self.get_note_sub_comments(...)\n",
        encoding="utf-8",
    )
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "browser_launcher.py").write_text(
        "            # Try to get version info\n"
        "            try:\n"
        '                result = subprocess.run([browser_path, "--version"],\n'
        "                                      capture_output=True, text=True, encoding='utf-8', errors='ignore', timeout=5)\n"
        "                version = result.stdout.strip() if result.stdout else \"Unknown Version\"\n"
        "            except:\n"
        '                version = "Unknown Version"\n',
        encoding="utf-8",
    )
    return tmp_path


def settings(intl=False, ua=WIN_UA, sleep=8, max_notes=10, headless=True):
    return SimpleNamespace(
        XHS_INTERNATIONAL=intl, BROWSER_USER_AGENT=ua, CRAWL_SLEEP_SEC=sleep,
        MAX_NOTES_PER_KEYWORD=max_notes, BROWSER_HEADLESS=headless,
    )


def test_patch_config_switches_to_rednote_backend(tmp_path):
    mc = fake_vendor(tmp_path)
    patch_config(mc, settings(intl=True))
    assert "XHS_INTERNATIONAL = True" in (mc / "config" / "base_config.py").read_text(encoding="utf-8")


def test_patch_config_replaces_mac_ua_and_leaves_comment_alone(tmp_path):
    mc = fake_vendor(tmp_path)
    patch_config(mc, settings())
    core = (mc / "media_platform" / "xhs" / "core.py").read_text(encoding="utf-8")
    assert f'self.user_agent = "{WIN_UA}"' in core
    assert MAC_UA not in core
    assert "# self.user_agent = utils.get_user_agent()" in core


def test_user_agent_repatches_when_changed(tmp_path):
    mc = fake_vendor(tmp_path)
    patch_config(mc, settings())
    patch_config(mc, settings(ua="CustomUA/1.0"))
    core = (mc / "media_platform" / "xhs" / "core.py").read_text(encoding="utf-8")
    assert 'self.user_agent = "CustomUA/1.0"' in core
    assert core.count("self.user_agent = ") == 2  # the commented line + the real one


def test_patch_config_applies_base_patches(tmp_path):
    mc = fake_vendor(tmp_path)
    patch_config(mc, settings())
    base = (mc / "config" / "base_config.py").read_text(encoding="utf-8")
    assert "ENABLE_CDP_MODE = True" in base
    assert "CDP_HEADLESS = True" in base
    assert 'SORT_TYPE = "time_descending"' in (mc / "config" / "xhs_config.py").read_text(encoding="utf-8")
    assert "set_default_timeout(120_000)" in (mc / "media_platform" / "xhs" / "core.py").read_text(encoding="utf-8")


def test_login_can_request_a_visible_browser(tmp_path):
    mc = fake_vendor(tmp_path)
    patch_config(mc, settings(), browser_headless=False)
    base = (mc / "config" / "base_config.py").read_text(encoding="utf-8")
    assert "CDP_HEADLESS = False" in base


def test_request_rate_comes_from_settings(tmp_path):
    """The throttle is the one lever against XHS risk control — it must not be hardcoded."""
    mc = fake_vendor(tmp_path)
    patch_config(mc, settings(sleep=12))
    assert "CRAWLER_MAX_SLEEP_SEC = 12" in (mc / "config" / "base_config.py").read_text(encoding="utf-8")


def test_index_navigation_waits_only_for_domcontentloaded(tmp_path):
    mc = fake_vendor(tmp_path)
    patch_config(mc, settings())
    core = (mc / "media_platform" / "xhs" / "core.py").read_text(encoding="utf-8")
    assert 'goto(self.index_url, wait_until="domcontentloaded")' in core


def test_patch_config_is_idempotent(tmp_path):
    mc = fake_vendor(tmp_path)
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


def test_details_and_comments_are_sliced_to_the_newest_n_notes(tmp_path):
    """The search page is 1 request for a fixed 20 notes; the details and comments behind
    it are the other ~97%. Slicing there is what lets MAX_NOTES_PER_KEYWORD < 20 cut a
    keyword's cost — MediaCrawler itself rounds the count cap up to a full page."""
    mc = fake_vendor(tmp_path)
    patch_config(mc, settings(max_notes=10))
    core = (mc / "media_platform" / "xhs" / "core.py").read_text(encoding="utf-8")
    assert 'for post_item in notes_res.get("items", {})[:10] if' in core


def test_detail_slice_repatches_when_n_changes(tmp_path):
    mc = fake_vendor(tmp_path)
    patch_config(mc, settings(max_notes=10))
    patch_config(mc, settings(max_notes=8))
    core = (mc / "media_platform" / "xhs" / "core.py").read_text(encoding="utf-8")
    assert '[:8]' in core and '[:10]' not in core
    assert core.count("for post_item in notes_res") == 1


def test_inline_replies_are_kept_but_never_paged_for(tmp_path):
    """The free half of sub-comments, without the half that walls the account: XHS nests the
    first replies in the parent's own response, so keep those and never call back for more."""
    mc = fake_vendor(tmp_path)
    patch_config(mc, settings())
    client = (mc / "media_platform" / "xhs" / "client.py").read_text(encoding="utf-8")

    assert "await callback(note_id, sub_comments)" in client  # inline replies still stored
    assert "continue  # xiaofinance: inline replies only" in client
    # the paging loop's guard is gone, so the request-per-comment loop is unreachable
    assert 'sub_comment_has_more = comment.get("sub_comment_has_more")' not in client
    body = client.split("inline replies only, never page for more")[1]
    assert "get_note_sub_comments" in body  # still present, just dead — we only cut the path


def test_a_walled_461_without_verifytype_header_cannot_crash_the_detector(tmp_path):
    """Run 16: XHS answered 461 with no Verifytype header, headers[] raised KeyError before
    "CAPTCHA appeared" was logged, and the run died anonymously. The detector must survive
    the very response it exists to detect."""
    mc = fake_vendor(tmp_path)
    patch_config(mc, settings())
    client = (mc / "media_platform" / "xhs" / "client.py").read_text(encoding="utf-8")
    assert 'response.headers["Verifytype"]' not in client
    assert 'response.headers.get("Verifytype", "unknown")' in client
    assert 'response.headers.get("Verifyuuid", "unknown")' in client


def test_a_failed_comment_fetch_skips_the_note_instead_of_killing_the_run(tmp_path):
    """One bad response in batch_get_note_comments propagates through asyncio.gather and
    aborts every keyword still in the queue — run 16 lost 3 of its 6 to that."""
    mc = fake_vendor(tmp_path)
    patch_config(mc, settings())
    core = (mc / "media_platform" / "xhs" / "core.py").read_text(encoding="utf-8")
    assert "try:\n                await self.xhs_client.get_note_all_comments(" in core
    assert "except Exception as ex:" in core
    assert "skipping" in core


def test_reused_browser_context_closes_its_leftover_default_tab(tmp_path):
    """Chrome always opens its own New Tab Page in the context CDP reuses; MediaCrawler
    then opens a second page for XHS and never touches the first, leaving an unrelated
    tab open for the whole crawl. The cleanup must run after new_page(), not before —
    closing a real Chrome window's last tab first would close the window itself."""
    mc = fake_vendor(tmp_path)
    patch_config(mc, settings())
    core = (mc / "media_platform" / "xhs" / "core.py").read_text(encoding="utf-8")
    new_page_pos = core.index("self.context_page = await self.browser_context.new_page()")
    cleanup_pos = core.index("for leftover_page in self.browser_context.pages:")
    assert new_page_pos < cleanup_pos
    assert "if leftover_page is not self.context_page:" in core
    assert "await leftover_page.close()" in core


def test_chrome_profile_is_set_to_keep_session_cookies_across_restarts(tmp_path):
    """XHS's login cookie is session-scoped, and each keyword launches a brand-new Chrome
    process against the same profile — Chromium purges session cookies on every fresh
    launch unless the profile says to continue where it left off, so without this the
    account was logging out before the next keyword's launch."""
    import json

    mc = fake_vendor(tmp_path)
    patch_config(mc, settings())
    prefs = json.loads(
        (mc / "browser_data" / "cdp_xhs_user_data_dir" / "Default" / "Preferences").read_text(
            encoding="utf-8"
        )
    )
    assert prefs["session"]["restore_on_startup"] == 1


def test_browser_version_probe_no_longer_shells_out(tmp_path):
    """chrome.exe --version has no --user-data-dir, so it targets the user's own default
    Chrome profile. If that's already running, Chrome's singleton IPC forwards the call
    to the live instance instead of printing a version string — popping a blank window
    in the user's real browser on every keyword's crawl process (confirmed live: this
    box's `chrome.exe --version` printed "Opening in existing browser session." instead
    of a version). The string is log-only, so drop the subprocess call entirely."""
    mc = fake_vendor(tmp_path)
    patch_config(mc, settings())
    launcher = (mc / "tools" / "browser_launcher.py").read_text(encoding="utf-8")
    assert "subprocess.run([browser_path" not in launcher
    assert 'version = "Unknown Version"' in launcher


def test_chrome_profile_patch_preserves_other_existing_preferences(tmp_path):
    import json

    mc = fake_vendor(tmp_path)
    prefs_path = mc / "browser_data" / "cdp_xhs_user_data_dir" / "Default" / "Preferences"
    prefs_path.parent.mkdir(parents=True)
    prefs_path.write_text(json.dumps({"some_other_setting": True}), encoding="utf-8")
    patch_config(mc, settings())
    prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
    assert prefs["some_other_setting"] is True
    assert prefs["session"]["restore_on_startup"] == 1
