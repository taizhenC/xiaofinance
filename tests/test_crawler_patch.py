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
        "            await self.context_page.goto(self.index_url)\n",
        encoding="utf-8",
    )
    (core / "client.py").write_text(
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
    return tmp_path


def settings(intl=False, ua=WIN_UA, sleep=8):
    return SimpleNamespace(XHS_INTERNATIONAL=intl, BROWSER_USER_AGENT=ua, CRAWL_SLEEP_SEC=sleep)


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
    assert "ENABLE_CDP_MODE = False" in base
    assert 'SORT_TYPE = "time_descending"' in (mc / "config" / "xhs_config.py").read_text(encoding="utf-8")
    assert "set_default_timeout(120_000)" in (mc / "media_platform" / "xhs" / "core.py").read_text(encoding="utf-8")


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
    client = (mc / "media_platform" / "xhs" / "client.py").read_text(encoding="utf-8")
    assert client.count("inline replies only") == 1


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
