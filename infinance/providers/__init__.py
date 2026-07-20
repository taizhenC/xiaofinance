from .base import (  # noqa: F401
    LoginOutcome,
    RunResult,
    SearchRequest,
    SessionState,
    SourceProvider,
)


def get_provider(settings=None):
    """The configured content source. Only one adapter exists today; a second
    platform or a licensed-data source becomes another module here, chosen by
    config — the pipeline never changes."""
    from .mediacrawler import MediaCrawlerProvider

    return MediaCrawlerProvider(settings)
