# One-time setup: uv, app deps, MediaCrawler (pinned), Playwright chromium, echarts.
# Idempotent — safe to re-run.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$MC_DIR = Join-Path $root "vendor\MediaCrawler"
$MC_REPO = "https://github.com/NanmiCoder/MediaCrawler.git"
$MC_PIN = "3bde9e2015f912f2e19ee63b615a0f48b9a90315"

Write-Host "== infinance setup ==" -ForegroundColor Cyan

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv..." -ForegroundColor Yellow
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
Write-Host "uv: $(uv --version)"

Write-Host "Syncing app dependencies..." -ForegroundColor Yellow
Set-Location $root
uv sync

if (-not (Test-Path (Join-Path $MC_DIR ".git"))) {
    Write-Host "Cloning MediaCrawler..." -ForegroundColor Yellow
    git clone $MC_REPO $MC_DIR
}
Set-Location $MC_DIR
git fetch --depth 200 origin 2>$null
git checkout $MC_PIN 2>$null
if ($LASTEXITCODE -ne 0) {
    git fetch --unshallow origin 2>$null
    git checkout $MC_PIN
}
Write-Host "MediaCrawler pinned at $(git rev-parse --short HEAD)"

Write-Host "Syncing MediaCrawler dependencies..." -ForegroundColor Yellow
uv sync
Write-Host "Installing Playwright chromium..." -ForegroundColor Yellow
uv run playwright install chromium

$echarts = Join-Path $root "static\vendor\echarts.min.js"
if (-not (Test-Path $echarts) -or (Get-Item $echarts).Length -lt 900KB) {
    Write-Host "Downloading echarts..." -ForegroundColor Yellow
    New-Item -ItemType Directory -Force (Split-Path $echarts) | Out-Null
    try {
        Invoke-WebRequest "https://registry.npmmirror.com/echarts/latest/files/dist/echarts.min.js" -OutFile $echarts -TimeoutSec 120
    } catch {
        Invoke-WebRequest "https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js" -OutFile $echarts -TimeoutSec 120
    }
}
Write-Host "echarts: $((Get-Item $echarts).Length) bytes"

if (-not (Test-Path (Join-Path $root ".env"))) {
    Copy-Item (Join-Path $root ".env.example") (Join-Path $root ".env")
    Write-Host "Created .env from .env.example — add your DEEPSEEK_API_KEY there." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Setup done. Next steps:" -ForegroundColor Green
Write-Host "  1. scripts\login_xhs.ps1   (scan QR with the XHS app, once)"
Write-Host "  2. uv run uvicorn app.main:app --host 127.0.0.1 --port 8000"
