/** All interface copy, centralized (UX-03). v1 interface language is
 * Chinese — the audience-first call; an i18n toggle arrives with P1.
 * Every error string states what happened AND the next action. */

export const S = {
  appName: "infinance",
  tagline: "小红书美股热度看板",

  // top bar / status
  windowBadge: (h: number) => `窗口：最近 ${h} 小时`,
  lastFetch: (t: string, status: string) => `上次抓取：${t}（${statusLabel(status)}）`,
  lastFetchNever: "上次抓取：从未",
  nextAutoRun: (t: string) => `下次自动抓取：${t}`,
  fetchNow: "立即抓取",
  fetching: "抓取中…",
  cancelFetch: "取消",
  themeToggle: "切换深浅色",

  // stages (DC-02)
  stage: {
    starting: "准备中",
    "crawl:discovery": "抓取·发现",
    "crawl:tracked": "抓取·自选",
    "ingest:discovery": "入库·发现",
    "ingest:tracked": "入库·自选",
    dedup: "去重",
    mentions: "识别提及",
    analyze: "AI 分析",
    prices: "行情核对",
    cleanup: "清理",
    slang_scan: "黑话扫描",
    cancelling: "正在取消…",
    cancelled: "已取消",
    done: "完成",
  } as Record<string, string>,
  crawlProgress: (notes: number, comments: number) => `已抓 ${notes} 帖 · ${comments} 评`,
  analyzeProgress: (done: number, total: number, ticker?: string) =>
    `${done}/${total}${ticker ? ` · ${ticker}` : ""}`,
  elapsed: (t: string) => `已用时 ${t}`,

  // banners & guardrails
  loginRequiredBanner: "小红书登录已失效 — 打开「登录与会话」面板重新扫码，或粘贴浏览器 Cookie。",
  noApiKeyBanner: "未配置 DEEPSEEK_API_KEY — 卡片只显示热门引用，没有 AI 总结。在 .env 中设置后重启即可。",
  cooldownBanner: (t: string) => `登录失效后的保护性冷却中（${t} 后解除）— 反复重试会提高账号风险，请先在下方重新登录。`,
  budgetBanner: (used: number, limit: number) =>
    `已达每日抓取预算（${used}/${limit} 条/24h）— 保护账号，明天自动恢复。确需继续可强制抓取。`,
  gapConfirm: (t: string) =>
    `距上次抓取不足最小间隔（${t} 后解除）。频繁抓取会提高账号风险，确定现在就抓？`,
  budgetConfirm: (used: number, limit: number) =>
    `已达每日抓取预算（${used}/${limit} 条/24h）。继续抓取会超出保护线，确定强制抓取？`,
  fetchStarted: "抓取已开始",
  fetchCancelRequested: "已请求取消，正在停止爬虫…",
  fetchConflict: "已有抓取任务在运行。",
  requestFailed: (what: string) => `${what}失败 — 请检查服务是否在运行，稍后重试。`,

  // panels
  rankingTitle: "热度排行",
  rankingSubtitle: "popularity score · 24h",
  legendBull: "偏多",
  legendBear: "偏空",
  legendNeutral: "中性/无分析",
  emptyRanking: "还没有数据 — 点右上角「立即抓取」抓一轮（首次需要先完成登录）。",
  radarTitle: "雷达观察",
  radarSubtitle: "提及数低于分析门槛的票",
  trackedTitle: "自选跟踪",
  trackedEmpty: "暂无跟踪 — 加一个试试，比如 COST",
  trackedPlaceholderTicker: "代码，如 COST",
  trackedPlaceholderKw: "中文关键词（可选，逗号分隔）",
  trackedAdd: "＋ 跟踪",
  trackedRemove: "移除",
  scoreboardTitle: "舆论准确率",
  scoreboardEmpty: "暂无可评估的判断 — 需要积累几天的分析与价格数据（舆论明显偏向后对照次日/7日股价）。",
  scoreboardNote: (d: number) =>
    `近 ${d} 天，舆论明显偏向（|▲−▼|≥2）后股价是否同向。这是对「人群历史判断」的描述性记录 — 不是预测，不构成建议。`,
  scoreboardNextDay: "次日",
  scoreboard7d: "7日",
  scoreboardPending: "待定",
  suggestionsTitle: "黑话/别名建议",
  suggestionsEmpty: "暂无待审核的别名建议（黑话扫描定期运行）。",
  accept: "接受",
  reject: "拒绝",
  runsTitle: "抓取记录",
  runsHeaders: ["#", "类型", "状态", "新内容", "请求量", "时长", "开始时间", "备注"],
  runMode: { discovery: "发现", tracked: "自选" } as Record<string, string>,

  // cards
  tracked: "已跟踪",
  notesComments: (n: number, c: number) => `${n} 帖 · ${c} 评`,
  rawCounts: (n: number, c: number) => `（含转发 ${n}帖/${c}评）`,
  latestContent: (t: string) => `最新内容 ${t}`,
  analyzedAt: (t: string) => `分析于 ${t}`,
  noiseRemoved: (x: number, total: number) => `噪音 ${x}/${total} 已剔除`,
  analysisFailed: (e: string) => `最近一次分析失败（${e}）。下轮抓取会自动重试；也可稍后手动抓取。`,
  noFreshContent: "最近 24 小时没有新内容。",
  belowThreshold: "提及数不足，尚未生成分析。",
  keylessQuotes: "未配置 AI 总结，显示热门引用：",
  viewSources: (n: number) => `查看 ${n} 条来源`,
  sourceOriginal: "原帖",
  clusterDup: (n: number) => `×${n} 相似`,
  bullPoints: "看多要点",
  bearPoints: "看空要点",
  quotesTitle: "高赞原文引用（逐字校验）",
  detailOpen: "查看详情",
  scoreTrend: "热度走势",

  // sentiment
  bullish: "看多",
  bearish: "看空",
  neutral: "中性",
  noAnalysis: "无分析",

  // badges
  badgeNew: "🔥 新上榜",
  badgeUp: (p: number) => `↑ 升温 +${p}%`,
  badgeDown: (p: number) => `↓ 降温 ${p}%`,
  badgeDivergence: "🔀 舆论与股价背离",
  divergenceTitle: "小红书舆论倾向与最近股价方向相反 — 可能已被定价，或情绪滞后/领先",
  quoteTitle: (d: string) => `最近交易日收盘 vs 前一交易日（${d}，Yahoo 免费数据，非实时）`,
  trendTitleNew: "上一抓取周期未出现",
  trendTitle: "热度较上一抓取周期的变化",

  // detail view
  backHome: "← 返回总览",
  detailItems: "全部来源内容",
  priceContext: "价格参考",
  detailNoData: "该股票最近 24 小时没有数据。",

  // session center (DC-01)
  sessionTitle: "登录与会话",
  sessionState: {
    valid: "会话有效",
    expired: "登录已失效",
    unauthorized: "搜索被拒绝",
    unknown: "状态未知",
  } as Record<string, string>,
  sessionSource: { qrcode: "扫码登录", cookie: "Cookie 会话" } as Record<string, string>,
  diagnosis: {
    none: "",
    expired: "会话已过期。点击「重新登录」，在弹出的浏览器里用小红书 App 扫码。",
    backend_mismatch:
      "登录成功但搜索被拒（没有权限访问）。你的账号很可能注册在国际版 rednote.com — 它与 xiaohongshu.com 是两套后端。点击下方按钮切换到国际版后端后重试。",
    try_cookie:
      "已切换国际版仍被拒。若你的浏览器里能正常搜索，把浏览器的 Cookie 粘贴到下面 — 复用平台已信任的会话。",
    account_gated:
      "使用浏览器 Cookie 仍被拒 — 账号本身可能被平台风控。请先在 App/浏览器正常使用几天并确认手机号已验证；不要继续重试，重试会提高风险分。",
  } as Record<string, string>,
  loginButton: "重新登录（扫码）",
  loginRunning: "等待扫码中…（浏览器窗口已打开）",
  loginOk: (d: string) => `登录成功 — ${d}`,
  loginFail: (d: string) => `登录未完成：${d}`,
  loginBusyFetch: "抓取进行中，无法同时登录 — 等它结束或先取消。",
  intlToggleOn: "切换到国际版后端（rednote.com）",
  intlToggleOff: "切回大陆版后端（xiaohongshu.com）",
  intlCurrent: (on: boolean) => (on ? "当前：国际版 rednote.com 后端" : "当前：大陆版 xiaohongshu.com 后端"),
  cookiePasteLabel: "粘贴浏览器 Cookie（替代 .env 手工编辑）",
  cookiePasteHint:
    "登录小红书网页版 → F12 → Network → 任一请求 → Request Headers → 复制整个 cookie 值（需包含 a1= 与 web_session=）。它等同密码，只保存在本机。",
  cookieSave: "保存 Cookie",
  cookieClear: "清除",
  cookieConfigured: "已配置浏览器 Cookie 会话",
  cookieBadFormat: "Cookie 缺少 a1= 或 web_session= — 请复制完整的 cookie 请求头值。",
  cookieSaved: "Cookie 已保存，之后的抓取将使用该会话。",
  cookieCleared: "已清除，恢复扫码登录。",

  // wizard (UX-01)
  wizardTitle: "开始使用 infinance",
  wizardSteps: ["须知", "环境检查", "登录小红书", "首次抓取"],
  wizardWelcome:
    "infinance 在你自己的电脑上运行：用你自己的小红书账号抓取最近 24 小时的美股讨论，汇总人群观点。",
  wizardAckTitle: "开始前请知悉",
  wizardAckPoints: [
    "本工具只汇总信息，不提供投资建议 — 不荐股、不给买卖信号，排序只反映讨论热度。",
    "抓取使用你自己的小红书账号，存在被平台限制的风险。默认的低频率与保护性限制就是为了降低这个风险；建议使用小号。",
    "数据仅保存在本机，原始内容 7 天后自动删除；MediaCrawler 为非商业学习用途的第三方组件，请保持个人使用。",
  ],
  wizardAckButton: "我已了解，继续",
  wizardEnvTitle: "环境检查",
  wizardEnvRefresh: "重新检查",
  wizardEnvAllGood: "环境就绪！",
  wizardEnvProblem: "有问题需要处理 — 按提示修复后点「重新检查」。",
  wizardLoginTitle: "登录小红书",
  wizardLoginDone: "会话有效，可以抓取。",
  wizardFetchTitle: "第一次抓取",
  wizardFetchIntro:
    "一轮完整抓取（抓取 → 入库 → 去重 → 识别 → 分析）约需 10–15 分钟 — 慢是刻意的：限速保护你的账号。",
  wizardFetchStart: "开始首次抓取",
  wizardFetchRunning: "抓取进行中 — 可以离开这个页面，完成后总览会自动更新。",
  wizardDone: "完成，进入看板",
  wizardSkip: "跳过向导",
  wizardOpen: "设置向导",

  // demo mode (UX-01)
  demoBadge: "示例数据 DEMO",
  demoBanner: "当前展示的是内置示例数据，用于预览界面 — 完成登录并抓取后自动切换为真实数据。",
  demoExit: "隐藏示例",
  demoEnter: "预览示例数据",

  // disclaimer (TR-01)
  disclaimer: "仅供参考，不构成投资建议",
  disclaimerLong:
    "本页面汇总公开社交平台讨论，仅描述舆论热度与观点分布，不构成任何投资建议。数据有延迟且可能不完整；投资决策请自行判断。",
  footer: "数据来自你自己的小红书账号 · 仅显示最近 24 小时内容 · 本工具仅供个人学习研究",

  // misc
  justNow: "刚刚",
  minutesAgo: (m: number) => `${m}分钟前`,
  hoursAgo: (h: number) => `${h}小时前`,
  daysAgo: (d: number) => `${d}天前`,
  never: "—",
  loading: "加载中…",
  confirm: "确认",
  cancel: "取消",
};

export function statusLabel(s: string): string {
  return (
    { running: "进行中", success: "成功", partial: "部分成功", failed: "失败" } as Record<string, string>
  )[s] ?? s;
}

export function runErrorLabel(error: string | null): string {
  if (!error) return "";
  if (error === "login_required") return "需要重新登录";
  if (error === "cancelled") return "已取消";
  if (error.startsWith("timeout")) return "超时（已保留已抓内容）";
  if (error.startsWith("crawler exit")) return `爬虫异常退出（${error}）`;
  if (error.includes("malformed")) return "部分数据格式异常，已跳过";
  if (error.includes("stale")) return "服务重启中断";
  return error;
}
