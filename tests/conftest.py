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
            "CDP_HEADLESS = True\n"
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
        tools = base / "tools"
        tools.mkdir(exist_ok=True)
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
        return base

    return _make
