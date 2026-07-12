# Smallest possible real crawl (3 notes, 美股, no comments) to test whether XHS will
# actually serve the search API for your session. Uses XHS_COOKIES from .env if set,
# otherwise falls back to QR login. Prints where the JSONL landed.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$outDir = Join-Path $root "data\raw\smoke"
uv run python -c @"
import logging, sys
from pathlib import Path
logging.basicConfig(level=logging.INFO, format='%(message)s')
from app.config import Settings
from app.crawler_runner import run_crawl

s = Settings()
mode = 'cookie session from .env' if s.XHS_COOKIES.strip() else 'QR login'
print(f'--- smoke crawl: 美股 x3 notes, auth = {mode} ---')
out = Path(r'$outDir')
res = run_crawl(['美股'], out, s)
print('exit code:', res['exit_code'], '| timed out:', res['timed_out'])

files = list(out.rglob('*.jsonl'))
total = sum(sum(1 for _ in f.open(encoding='utf-8')) for f in files)
if total:
    print(f'SUCCESS: {total} rows across {len(files)} jsonl file(s) in {out}')
else:
    print('NO DATA. Check the log:', res['log_path'])
    log = Path(res['log_path']).read_text(encoding='utf-8', errors='replace')
    if '没有权限访问' in log:
        print('>>> XHS refused the search API for this session (risk control).')
        print('>>> If xiaohongshu.com search works in your browser, set XHS_COOKIES in .env.')
    sys.exit(1)
"@
