import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from infinance.db import connect  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    yield c
    c.close()


MAC_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/126.0.0.0 Safari/537.36"


@pytest.fixture
def make_vendor(tmp_path):
    """A minimal fake MediaCrawler checkout with every file/anchor the patch
    contract touches. Returns the vendor dir."""

    def _make(root: Path | None = None) -> Path:
        base = root or (tmp_path / "vendor")
        cfg = base / "config"
        cfg.mkdir(parents=True, exist_ok=True)
        (base / "main.py").write_text("# stub\n", encoding="utf-8")
        (cfg / "xhs_config.py").write_text('SORT_TYPE = "general"\n', encoding="utf-8")
        (cfg / "base_config.py").write_text(
            "XHS_INTERNATIONAL = False\n"
            "ENABLE_CDP_MODE = False\n"
            "CDP_CONNECT_EXISTING = True\n"
            "CRAWLER_MAX_SLEEP_SEC = 2\n"
            'COOKIES = ""\n',
            encoding="utf-8",
        )
        core = base / "media_platform" / "xhs"
        core.mkdir(parents=True, exist_ok=True)
        (core / "core.py").write_text(
            "        # self.user_agent = utils.get_user_agent()\n"
            f'        self.user_agent = "{MAC_UA}"\n'
            "            self.context_page = await self.browser_context.new_page()\n",
            encoding="utf-8",
        )
        return base

    return _make
