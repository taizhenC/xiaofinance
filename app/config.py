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
    DISCOVERY_INVESTMENT_POOL: str = "黄金投资,美债投资,基金定投,资产配置,比特币投资"
    # Each keyword is its own crawler process now, so the cycle is no longer racing
    # CRAWL_TIMEOUT_MIN — the budget is requests per day against the account flag.
    # 6 keywords × ~21 requests ≈ what two keywords cost before the detail slice.
    KEYWORDS_PER_CYCLE: int = 6
    # XHS search pages hold a fixed 20 notes, but the search call is 1 request — the
    # per-note details and comments behind it are the other ~97%. The detail-slice patch
    # (crawler_runner) caps details+comments at the newest N of the page, which is what
    # makes values below 20 mean something. Run 21 walled at ~120 requests in 24 min;
    # 10 notes/keyword keeps a whole 6-keyword cycle near that budget.
    MAX_NOTES_PER_KEYWORD: int = 10
    # One comment page holds ~10, so 10 costs a single request per note; the old 20 paid
    # a second page per note for tail comments that rarely name a stock.
    MAX_COMMENTS_PER_NOTE: int = 10
    FETCH_INTERVAL_HOURS: float = 5
    # MediaCrawler sleeps this long after every note detail and every comment fetch — not
    # just once per page — so it is the real request rate. Its default of 3s walled the
    # account partway through a 6-keyword cycle; 8s costs ~10 min per keyword.
    CRAWL_SLEEP_SEC: int = 8
    # Per crawler process, which is now a single keyword (~3-4 min at the sliced volume);
    # this is a hung-browser backstop, not the cycle budget.
    CRAWL_TIMEOUT_MIN: int = 15
    # Pause between per-keyword crawler processes. The wall triggers on session volume
    # (runs 16-21: ~100-130 continuous requests), so the same cycle spent as short spaced
    # bursts stays under it. 6 keywords × (~4 min crawl + gap) must fit FETCH_INTERVAL_HOURS.
    KEYWORD_GAP_MIN: float = 12
    # Once XHS decides the account is a crawler it answers with a CAPTCHA (461) and tenacity
    # retries each walled request — one run ground through 192 of them, which can only
    # deepen the flag. Stop instead: whatever was fetched before the wall is already ingested.
    CAPTCHA_ABORT_COUNT: int = 10
    # After a walled run, scheduled cycles hold off this long. The run-16 flag persisted
    # 12+ hours, and every 5-hourly retry (runs 17-20) burned another dozen 461s that kept
    # it warm. Manual "Fetch now" bypasses the cooldown on purpose: someone who has just
    # cleared the CAPTCHA in the browser needs to test immediately. 0 disables.
    RISK_COOLDOWN_HOURS: float = 24
    # Replies to comments. XHS ships the first few of them inside the parent comment's own
    # response, so collecting them adds no requests and no risk — the crawler was fetching
    # and discarding them. The paging loop that would chase the rest is patched out
    # (CODE_PATCHES), so this cannot turn into extra traffic on its own.
    ENABLE_SUB_COMMENTS: bool = True
    # Accounts registered on rednote.com (the international app) are not valid against
    # mainland xiaohongshu.com: the API host and cookie domain differ, and the search API
    # answers 您当前登录的账号没有权限访问. Switches to webapi.rednote.com / .rednote.com.
    XHS_INTERNATIONAL: bool = False
    BROWSER_HEADLESS: bool = True
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

    @property
    def discovery_investment_pool_list(self) -> list[str]:
        return self._split(self.DISCOVERY_INVESTMENT_POOL)


settings = Settings()
