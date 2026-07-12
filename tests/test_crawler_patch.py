from types import SimpleNamespace

from app.crawler_runner import patch_config


def fake_vendor(tmp_path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "xhs_config.py").write_text('SORT_TYPE = "general"\n', encoding="utf-8")
    (cfg / "base_config.py").write_text(
        "ENABLE_CDP_MODE = False\n"
        "CDP_CONNECT_EXISTING = True\n"
        "CRAWLER_MAX_SLEEP_SEC = 2\n",
        encoding="utf-8",
    )
    core = tmp_path / "media_platform" / "xhs"
    core.mkdir(parents=True)
    (core / "core.py").write_text(
        "            self.context_page = await self.browser_context.new_page()\n",
        encoding="utf-8",
    )
    return tmp_path


def test_patch_config_enables_cdp_and_launch_new(tmp_path):
    mc = fake_vendor(tmp_path)
    patch_config(mc, SimpleNamespace(ENABLE_CDP_MODE=True))
    base = (mc / "config" / "base_config.py").read_text(encoding="utf-8")
    assert "ENABLE_CDP_MODE = True" in base
    assert "CDP_CONNECT_EXISTING = False" in base
    assert 'SORT_TYPE = "time_descending"' in (mc / "config" / "xhs_config.py").read_text(encoding="utf-8")
    assert "set_default_timeout(120_000)" in (mc / "media_platform" / "xhs" / "core.py").read_text(encoding="utf-8")


def test_patch_config_respects_cdp_disabled_and_is_idempotent(tmp_path):
    mc = fake_vendor(tmp_path)
    patch_config(mc, SimpleNamespace(ENABLE_CDP_MODE=False))
    patch_config(mc, SimpleNamespace(ENABLE_CDP_MODE=False))
    base = (mc / "config" / "base_config.py").read_text(encoding="utf-8")
    assert "ENABLE_CDP_MODE = False" in base
    assert base.count("ENABLE_CDP_MODE") == 1
    core = (mc / "media_platform" / "xhs" / "core.py").read_text(encoding="utf-8")
    assert core.count("set_default_timeout(120_000)") == 1
