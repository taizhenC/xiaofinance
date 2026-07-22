# 安装指南（10 分钟）· Install guide

同一套命令适用于 **Windows 10/11** 与 **macOS**（Linux 理论可用，未在 CI 覆盖前视为 best-effort）。

## 前置条件

| 需要 | 用途 | 安装 |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | 管理 Python 环境（应用与爬虫各自独立） | Windows: `powershell -c "irm https://astral.sh/uv/install.ps1 \| iex"` · macOS: `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| git | 获取固定版本的 MediaCrawler | [git-scm.com](https://git-scm.com)（macOS 自带） |
| 小红书账号 | 数据来自你自己的账号（无需 API key） | 建议使用小号，见[账号安全](account-safety.md) |

DeepSeek API key 可选：不配置也能用，卡片显示热门引用而不是 AI 总结。

## 安装

**方式 A — 发布包（推荐）**

```
pipx install infinance        # 或 uv tool install infinance
```

**方式 B — 源码（开发者）**

```
git clone https://github.com/taizhenC/xiaofinance.git && cd xiaofinance
uv sync
npm ci --prefix frontend && npm run build --prefix frontend   # 构建 Web UI（发布包已内置，无需此步）
```

源码方式下所有 `infinance …` 命令写作 `uv run infinance …`。

## 初始化（一次性）

```
infinance setup
```

它会：检查 git/uv → 克隆并锁定 MediaCrawler（固定 commit，非商业学习用途，只存在于你机器上）→ 安装爬虫依赖 → 安装 Playwright Chromium（登录用浏览器）→ 生成 `.env` 配置。全程有进度提示，可重复执行。

## 启动

```
infinance run
```

打开 http://127.0.0.1:8000 。首次进入会看到**设置向导**：须知确认 → 环境检查 → 扫码登录 → 首次抓取。在真实数据出现之前，看板展示带「示例数据 DEMO」水印的内置示例，几分钟内就能看懂这个工具长什么样。

可选：在 `.env` 里填 `DEEPSEEK_API_KEY=sk-...` 后重启，卡片才有 AI 总结。

## 常用命令

| 命令 | 作用 |
|---|---|
| `infinance doctor` | 诊断安装问题，逐项给出修复方法 |
| `infinance login` | 终端里重新扫码登录（界面里也可以做） |
| `infinance smoke` | 最小真实抓取（3 帖）验证会话是否可用 |
| `infinance cycle --mode both` | 不开服务器跑一轮抓取+分析 |

装完出问题？看[故障排查](troubleshooting.md)。
