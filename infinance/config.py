import sys
from pathlib import Path

import platformdirs
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_DIR = Path(__file__).resolve().parent
BASE_DIR = PACKAGE_DIR.parent

# Repo checkout (developer flow): everything lives inside the repo, as always.
# Installed package (pipx/uvx): state goes to the platform user-data dir —
# site-packages is ephemeral and must never hold databases or vendor checkouts.
IS_REPO_CHECKOUT = (BASE_DIR / "pyproject.toml").exists()
DATA_HOME = (
    BASE_DIR if IS_REPO_CHECKOUT else Path(platformdirs.user_data_dir("infinance", appauthor=False))
)


def default_user_agent() -> str:
    """A truthful UA for the machine we actually run on. MediaCrawler hardcodes
    a macOS Chrome UA; sending that from Windows/Linux makes XHS log the session
    as an 'unknown device', which raises exactly the suspicion we want to avoid."""
    chrome = "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
    if sys.platform == "darwin":
        return f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) {chrome}"
    if sys.platform.startswith("linux"):
        return f"Mozilla/5.0 (X11; Linux x86_64) {chrome}"
    return f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) {chrome}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=DATA_HOME / ".env", env_file_encoding="utf-8", extra="ignore"
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
    # Platform-appropriate by default (see default_user_agent); override to pin one.
    BROWSER_USER_AGENT: str = default_user_agent()
    # Paste a logged-in browser's cookie string to crawl with that session instead of a
    # QR-login one. Must come from the same site the account lives on (see XHS_INTERNATIONAL).
    XHS_COOKIES: str = ""

    # Account-safety guardrails (DC-03) — on by default. They protect the
    # user's XHS account from the behaviors that get accounts restricted:
    # back-to-back manual fetches, blowing the request volume up with custom
    # keywords, and hammering a session the platform already flagged.
    MIN_FETCH_GAP_MIN: int = 45
    AUTH_COOLDOWN_MIN: int = 30
    DAILY_REQUEST_BUDGET: int = 15000

    FRESH_WINDOW_HOURS: int = 24
    MIN_MENTIONS_FOR_ANALYSIS: int = 2
    MAX_ANALYZED_STOCKS: int = 15
    SLANG_SCAN_EVERY_N_CYCLES: int = 20

    ENABLE_PRICE_QUOTES: bool = True
    QUOTE_REFRESH_MIN: int = 30

    MEDIACRAWLER_DIR: Path = DATA_HOME / "vendor" / "MediaCrawler"
    UV_EXE: str = "uv"
    DB_PATH: Path = DATA_HOME / "data" / "infinance.db"
    RAW_DIR: Path = DATA_HOME / "data" / "raw"
    STOCK_DICT_PATH: Path = PACKAGE_DIR / "data" / "stock_dict.json"
    STOCK_DICT_LOCAL_PATH: Path = DATA_HOME / "data" / "stock_dict_local.json"

    HOST: str = "127.0.0.1"
    PORT: int = 8000
    # Required to bind anything but loopback. When set (and the bind is
    # non-local), every mutating endpoint demands `Authorization: Bearer <token>`
    # and cross-origin browser requests are rejected.
    AUTH_TOKEN: str = ""

    @property
    def fresh_window_ms(self) -> int:
        return self.FRESH_WINDOW_HOURS * 3600 * 1000

    @property
    def discovery_keywords_list(self) -> list[str]:
        return [k.strip() for k in self.DISCOVERY_KEYWORDS.split(",") if k.strip()]


settings = Settings()
