"""Environment diagnosis shared by `infinance doctor` (CLI) and /api/doctor
(the onboarding wizard's checklist). Each check returns a structured result
with a fix hint; the CLI prints them, the wizard renders them with fix-it
actions."""

import shutil
import socket
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Check:
    key: str
    label: str
    ok: bool
    required: bool = True
    detail: str = ""
    fix: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def playwright_cache_dir() -> Path:
    if sys.platform == "win32":
        import os

        return Path(os.environ.get("LOCALAPPDATA", "~")).expanduser() / "ms-playwright"
    if sys.platform == "darwin":
        return Path("~/Library/Caches/ms-playwright").expanduser()
    return Path("~/.cache/ms-playwright").expanduser()


def run_checks(settings, include_port: bool = True) -> list[Check]:
    from .db import connect
    from .migrations import LATEST_VERSION, get_version
    from .providers.mediacrawler import VENDOR_PIN

    checks: list[Check] = []

    checks.append(Check(
        key="python", label="Python ≥ 3.11", ok=sys.version_info >= (3, 11),
        detail=sys.version.split()[0],
    ))
    checks.append(Check(
        key="uv", label="uv 可用", ok=shutil.which("uv") is not None,
        fix="安装 uv：https://docs.astral.sh/uv/",
    ))

    mc_dir = Path(settings.MEDIACRAWLER_DIR)
    vendor_ok = (mc_dir / "main.py").exists()
    checks.append(Check(
        key="vendor", label="MediaCrawler 已就位", ok=vendor_ok,
        detail=str(mc_dir), fix="运行 `infinance setup` 获取",
    ))
    if vendor_ok:
        head = ""
        try:
            head = subprocess.run(
                ["git", "-C", str(mc_dir), "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=15,
            ).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
        checks.append(Check(
            key="vendor_pin", label="MediaCrawler 版本锁定", ok=head == VENDOR_PIN,
            detail=head[:12] or "unknown",
            fix="运行 `infinance setup` 重新锁定版本（上游变动会破坏补丁契约）",
        ))
        checks.append(Check(
            key="vendor_deps", label="爬虫依赖已安装", ok=(mc_dir / ".venv").exists(),
            fix="运行 `infinance setup`",
        ))

    pw = playwright_cache_dir()
    has_chromium = pw.exists() and any(p.name.startswith("chromium") for p in pw.iterdir())
    checks.append(Check(
        key="chromium", label="Playwright Chromium（登录浏览器）", ok=has_chromium,
        fix="运行 `infinance setup`",
    ))

    checks.append(Check(
        key="llm_key", label="LLM API Key（可选）", ok=bool(settings.DEEPSEEK_API_KEY),
        required=False,
        detail=f"{settings.LLM_MODEL} @ {settings.LLM_BASE_URL}" if settings.DEEPSEEK_API_KEY else "",
        fix="未配置时卡片只显示热门引用，没有 AI 总结。在 .env 中设置 DEEPSEEK_API_KEY",
    ))

    cookies = (settings.XHS_COOKIES or "").strip()
    if cookies:
        checks.append(Check(
            key="cookie_format", label="XHS_COOKIES 格式（a1= 与 web_session=）",
            ok="a1=" in cookies and "web_session=" in cookies,
            fix="从已登录浏览器复制完整 cookie 请求头值",
        ))

    try:
        conn = connect()
        version = get_version(conn)
        conn.close()
        checks.append(Check(
            key="db", label="数据库可用", ok=version == LATEST_VERSION,
            detail=f"schema v{version} / latest v{LATEST_VERSION}",
        ))
    except Exception as e:
        checks.append(Check(key="db", label="数据库可用", ok=False, detail=str(e)[:200]))

    if include_port:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind((settings.HOST, settings.PORT))
            s.close()
            port_free = True
        except OSError:
            port_free = False
        checks.append(Check(
            key="port", label=f"端口 {settings.PORT} 空闲", ok=port_free, required=False,
            fix="看板可能已在运行；或在 .env 中修改 PORT",
        ))

    return checks
