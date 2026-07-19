"""Deprecated shim — the MediaCrawler integration lives in
app.providers.mediacrawler behind the SourceProvider interface.

Kept only so the legacy PowerShell scripts keep working until the unified CLI
(PL-01/PL-04) replaces them. New code must import from app.providers.
"""

from pathlib import Path

from .providers.base import SearchRequest
from .providers.mediacrawler import (  # noqa: F401
    CODE_PATCHES,
    EXPIRED_MARKERS,
    LOGIN_HINTS,
    PATCHES,
    MediaCrawlerProvider,
    patch_config,
)


def run_crawl(keywords: list[str], run_dir: Path, settings) -> dict:
    provider = MediaCrawlerProvider(settings)
    result = provider.search(SearchRequest(
        keywords=keywords, run_dir=run_dir,
        max_notes_per_keyword=settings.MAX_NOTES_PER_KEYWORD,
        max_comments_per_note=settings.MAX_COMMENTS_PER_NOTE,
        include_sub_comments=settings.ENABLE_SUB_COMMENTS,
        timeout_min=settings.CRAWL_TIMEOUT_MIN,
    ))
    return {"exit_code": result.exit_code, "timed_out": result.timed_out,
            "log_path": result.log_path}


def login_looks_required(log_path: Path, notes_fresh: int) -> bool:
    from .config import settings

    return MediaCrawlerProvider(settings).login_looks_required(log_path, notes_fresh)
