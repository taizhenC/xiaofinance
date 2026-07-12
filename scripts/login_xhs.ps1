# (Re)do XHS QR login: runs a tiny visible crawl; scan the QR with the XHS app.
# Session is cached in MediaCrawler's browser profile (SAVE_LOGIN_STATE=True), so this is needed
# only on first run or after login expiry (red banner in the dashboard).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

# Apply the same MediaCrawler config patches the app applies before each crawl
# (CDP mode per settings, launch-new browser instead of waiting on port 9222, etc.)
Set-Location $root
uv run python -c "import logging; logging.basicConfig(level=logging.INFO); from app.crawler_runner import patch_config; from app.config import settings; patch_config(settings.MEDIACRAWLER_DIR, settings)"

Set-Location (Join-Path $root "vendor\MediaCrawler")
uv run main.py --platform xhs --lt qrcode --type search `
    --keywords "美股" `
    --save_data_option jsonl --save_data_path (Join-Path $root "data\raw\login_check") `
    --get_comment no --get_sub_comment no `
    --crawler_max_notes_count 3 --start 1 --headless no --max_concurrency_num 1
Write-Host ""
Write-Host "If notes were fetched above, login is cached. You can close this window." -ForegroundColor Green
