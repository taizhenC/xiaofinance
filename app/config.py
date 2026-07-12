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

    DISCOVERY_KEYWORDS: str = (
        "美股,纳斯达克,纳指,标普500,美股投资,美股分析,美股日记,美股小白,中概股,美股财报"
    )
    MAX_NOTES_PER_KEYWORD: int = 12
    MAX_COMMENTS_PER_NOTE: int = 20
    FETCH_INTERVAL_HOURS: float = 5
    CRAWL_TIMEOUT_MIN: int = 30
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

    FRESH_WINDOW_HOURS: int = 24
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
    def discovery_keywords_list(self) -> list[str]:
        return [k.strip() for k in self.DISCOVERY_KEYWORDS.split(",") if k.strip()]


settings = Settings()
