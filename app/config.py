from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    DEEPSEEK_API_KEY: str = ""
    LLM_MODEL: str = "deepseek-chat"
    LLM_BASE_URL: str = "https://api.deepseek.com"
    SUMMARY_LANG: str = "en"

    # Used only when DISCOVERY_POOL is empty (a static, non-rotating keyword list).
    DISCOVERY_KEYWORDS: str = (
        "美股,纳斯达克,纳指,标普500,美股投资,美股分析,美股日记,美股小白,中概股,美股财报"
    )
    # Crawled every cycle: the broad terms that carry general US-stock discussion.
    DISCOVERY_CORE: str = "美股,美股财报"
    # Rotated across cycles, KEYWORDS_PER_CYCLE at a time, so a day's cycles cover sectors
    # a single cycle can't afford. Every one below was measured with app.probe; the rule
    # that fell out is that 美股-prefixed terms return US-stock content while bare Chinese
    # sector terms return A-share or consumer posts — 电动车 returns scooter rentals, 黄金股
    # returns 紫金/老铺黄金, 减肥药 returns diet pills. Probe before adding, never guess.
    DISCOVERY_POOL: str = "中概股,美股医药,美股银行,巴菲特,美股打新"
    # A keyword costs ~5 min (20 notes + their comments), so 6 is what fits inside
    # CRAWL_TIMEOUT_MIN. Overshoot and the crawler is killed mid-list, silently dropping
    # the cycle's tail keywords.
    KEYWORDS_PER_CYCLE: int = 6
    # XHS search pages hold 20 notes and MediaCrawler rounds anything smaller up to a full
    # page (core.py: `if CRAWLER_MAX_NOTES_COUNT < xhs_limit_count`), so values below 20
    # buy nothing — the old 12 always fetched 20.
    MAX_NOTES_PER_KEYWORD: int = 20
    MAX_COMMENTS_PER_NOTE: int = 20
    FETCH_INTERVAL_HOURS: float = 5
    # MediaCrawler sleeps this long after every note detail and every comment fetch — not
    # just once per page — so it is the real request rate. Its default of 3s walled the
    # account partway through a 6-keyword cycle; 8s costs ~10 min per keyword.
    CRAWL_SLEEP_SEC: int = 8
    CRAWL_TIMEOUT_MIN: int = 70
    # Once XHS decides the account is a crawler it answers with a CAPTCHA (461) and tenacity
    # retries each walled request — one run ground through 192 of them, which can only
    # deepen the flag. Stop instead: whatever was fetched before the wall is already ingested.
    CAPTCHA_ABORT_COUNT: int = 10
    ENABLE_SUB_COMMENTS: bool = False
    # Accounts registered on rednote.com (the international app) are not valid against
    # mainland xiaohongshu.com: the API host and cookie domain differ, and the search API
    # answers 您当前登录的账号没有权限访问. Switches to webapi.rednote.com / .rednote.com.
    XHS_INTERNATIONAL: bool = False
    # MediaCrawler hardcodes a macOS Chrome 126 UA, which mismatches the real client on a
    # Windows box (XHS logs the session as an "unknown" device) — send a truthful one.
    BROWSER_USER_AGENT: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
    )
    # Paste a logged-in browser's cookie string to crawl with that session instead of a
    # QR-login one. Must come from the same site the account lives on (see XHS_INTERNATIONAL).
    XHS_COOKIES: str = ""

    # The board scores the last FRESH_WINDOW_HOURS: it answers "what is hot today", and
    # trend badges compare snapshots taken on that basis.
    FRESH_WINDOW_HOURS: int = 24
    # But XHS discusses non-tech at roughly a post a day (美股医药 returned 1 fresh note out
    # of 20, the other 19 spanning ten days), so a 24h view of a sector is mostly empty.
    # Notes are kept and mention-scanned over this wider window, and the sector strip and
    # radar read from it — the board itself stays 24h.
    CONTEXT_WINDOW_HOURS: int = 72
    MIN_MENTIONS_FOR_ANALYSIS: int = 2
    MAX_ANALYZED_STOCKS: int = 15
    SLANG_SCAN_EVERY_N_CYCLES: int = 20

    ENABLE_PRICE_QUOTES: bool = True
    QUOTE_REFRESH_MIN: int = 30

    MEDIACRAWLER_DIR: Path = BASE_DIR / "vendor" / "MediaCrawler"
    UV_EXE: str = "uv"
    DB_PATH: Path = BASE_DIR / "data" / "infinance.db"
    RAW_DIR: Path = BASE_DIR / "data" / "raw"
    STOCK_DICT_PATH: Path = BASE_DIR / "app" / "data" / "stock_dict.json"
    STOCK_DICT_LOCAL_PATH: Path = BASE_DIR / "data" / "stock_dict_local.json"

    HOST: str = "127.0.0.1"
    PORT: int = 8000

    @property
    def fresh_window_ms(self) -> int:
        return self.FRESH_WINDOW_HOURS * 3600 * 1000

    @property
    def context_window_ms(self) -> int:
        return max(self.CONTEXT_WINDOW_HOURS, self.FRESH_WINDOW_HOURS) * 3600 * 1000

    @staticmethod
    def _split(raw: str) -> list[str]:
        seen, out = set(), []
        for k in (k.strip() for k in raw.split(",")):
            if k and k not in seen:
                seen.add(k)
                out.append(k)
        return out

    @property
    def discovery_keywords_list(self) -> list[str]:
        return self._split(self.DISCOVERY_KEYWORDS)

    @property
    def discovery_core_list(self) -> list[str]:
        return self._split(self.DISCOVERY_CORE)

    @property
    def discovery_pool_list(self) -> list[str]:
        return self._split(self.DISCOVERY_POOL)


settings = Settings()
