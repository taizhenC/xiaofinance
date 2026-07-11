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

    DISCOVERY_KEYWORDS: str = "美股,纳斯达克,纳指,标普500,美股投资"
    MAX_NOTES_PER_KEYWORD: int = 20
    MAX_COMMENTS_PER_NOTE: int = 20
    FETCH_INTERVAL_HOURS: float = 5
    CRAWL_TIMEOUT_MIN: int = 30

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
