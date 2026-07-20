/** Bundled synthetic demo dataset (UX-01): lets a fresh install see a fully
 * populated dashboard in seconds, decoupling "is this tool for me?" from
 * "did my login work?". Every text below is invented for the demo — no real
 * XHS content is redistributed — and every surface renders the 示例数据
 * watermark while demo mode is on. */

import type {
  RankingResponse, RankingEntry, Scoreboard, StockDetail, Status,
} from "./types";

const H = 3_600_000;
const NOW = () => Date.now();

function hist(base: number, shape: number[]): { ts: number; score: number }[] {
  const now = NOW();
  return shape.map((f, i) => ({
    ts: now - (shape.length - 1 - i) * 5 * H,
    score: Math.round(base * f * 10) / 10,
  }));
}

interface DemoSpec {
  ticker: string;
  name_cn: string;
  score: number;
  notes: number;
  comments: number;
  bull: number;
  bear: number;
  neutral: number;
  price: number;
  change: number;
  trendDir: "new" | "up" | "down" | "flat";
  trendPct: number | null;
  divergence?: boolean;
  tracked?: boolean;
  shape: number[];
  summary: string;
  bulls: string[];
  bears: string[];
  quotes: string[];
}

const SPECS: DemoSpec[] = [
  {
    ticker: "NVDA", name_cn: "英伟达", score: 86.5, notes: 14, comments: 52,
    bull: 18, bear: 5, neutral: 9, price: 1183.2, change: 2.4,
    trendDir: "up", trendPct: 38, tracked: true,
    shape: [0.4, 0.45, 0.5, 0.62, 0.6, 0.72, 0.86, 1.0],
    summary:
      "NVDA: 示例总结 — 财报临近，讨论热度明显升温。多数帖子看好数据中心需求持续，普遍讨论「这次指引会不会再超预期」；少数声音担心估值过高、获利了结。相比上一周期，看多情绪增强。",
    bulls: ["数据中心订单能见度高（示例观点）", "新品发布节奏快，护城河稳固（示例观点）", "回调即被资金接走，筹码结构健康（示例观点）"],
    bears: ["估值透支未来两年业绩（示例观点）", "出口管制的不确定性仍在（示例观点）"],
    quotes: ["示例引用：财报前上车还来得及吗？", "示例引用：老黄又要开发布会了，先冲为敬", "示例引用：估值这么高，睡不着的可以减点"],
  },
  {
    ticker: "TSLA", name_cn: "特斯拉", score: 64.2, notes: 11, comments: 41,
    bull: 9, bear: 14, neutral: 8, price: 244.6, change: 3.1,
    trendDir: "flat", trendPct: 4, divergence: true,
    shape: [0.8, 0.75, 0.9, 0.85, 0.8, 0.78, 0.83, 0.8],
    summary:
      "TSLA: 示例总结 — 舆论整体偏谨慎：交付数据与价格战是主要担忧，但当日股价反弹，出现「舆论与股价背离」。部分帖子讨论 Robotaxi 进展，观点分歧明显。",
    bulls: ["Robotaxi 叙事重新升温（示例观点）", "能源业务被低估（示例观点）"],
    bears: ["降价压缩毛利（示例观点）", "交付增速放缓（示例观点）", "竞争车型密集上市（示例观点）"],
    quotes: ["示例引用：今天这波反弹是逼空还是反转？", "示例引用：价格战打到最后受伤的是利润表"],
  },
  {
    ticker: "AAPL", name_cn: "苹果", score: 41.0, notes: 8, comments: 25,
    bull: 7, bear: 6, neutral: 10, price: 228.9, change: -0.6,
    trendDir: "down", trendPct: -31,
    shape: [1.0, 0.9, 0.85, 0.7, 0.75, 0.6, 0.55, 0.48],
    summary:
      "AAPL: 示例总结 — 讨论降温。AI 功能落地节奏与大中华区销量仍是分歧点，观点整体中性偏观望，无明显方向性共识。",
    bulls: ["服务收入占比提升，盈利质量改善（示例观点）"],
    bears: ["硬件创新周期放缓（示例观点）", "大中华区竞争加剧（示例观点）"],
    quotes: ["示例引用：等 AI 功能全量推送再看看", "示例引用：果子最近没什么新故事"],
  },
  {
    ticker: "PLTR", name_cn: "Palantir", score: 33.8, notes: 6, comments: 22,
    bull: 12, bear: 3, neutral: 4, price: 88.4, change: 5.2,
    trendDir: "new", trendPct: null,
    shape: [0, 0, 0, 0, 0.2, 0.4, 0.7, 1.0],
    summary:
      "PLTR: 示例总结 — 新上榜。政府订单叙事带动散户热情，帖子以「错过英伟达别错过它」类情绪为主，理性分析较少，追高情绪浓。",
    bulls: ["政府合同持续放量（示例观点）", "AI 平台商业化超预期（示例观点）"],
    bears: ["散户浓度过高，波动剧烈（示例观点）"],
    quotes: ["示例引用：这票的信仰成分比基本面多", "示例引用：回调到 80 以下我就加仓"],
  },
  {
    ticker: "QQQ", name_cn: "纳指100ETF", score: 27.5, notes: 5, comments: 18,
    bull: 6, bear: 5, neutral: 7, price: 512.3, change: 0.8,
    trendDir: "flat", trendPct: -2, tracked: true,
    shape: [0.6, 0.66, 0.6, 0.63, 0.58, 0.62, 0.6, 0.61],
    summary:
      "QQQ: 示例总结 — 定投打卡帖为主，情绪平稳。讨论集中在「回调是否加仓」与美联储路径，无明显方向倾斜。",
    bulls: ["长期定投共识稳定（示例观点）"],
    bears: ["集中度风险：前十大权重过高（示例观点）"],
    quotes: ["示例引用：跌了就买，涨了就拿着，纪律最重要"],
  },
  {
    ticker: "BABA", name_cn: "阿里巴巴", score: 21.2, notes: 4, comments: 15,
    bull: 8, bear: 4, neutral: 3, price: 132.8, change: -2.9,
    trendDir: "up", trendPct: 45, divergence: true,
    shape: [0.3, 0.28, 0.35, 0.3, 0.42, 0.5, 0.66, 0.8],
    summary:
      "BABA: 示例总结 — 中概情绪回暖带动讨论升温，但当日股价下跌，形成背离。看多者押注电商基本盘企稳与回购力度，看空者担忧竞争格局。",
    bulls: ["回购规模可观（示例观点）", "云业务重回增长（示例观点）"],
    bears: ["电商份额仍在被蚕食（示例观点）"],
    quotes: ["示例引用：中概这波是反弹还是反转，先上车再说", "示例引用：便宜是便宜，就是没催化"],
  },
];

const RADAR = [
  { ticker: "SMCI", name_cn: "超微电脑", mentions: 1, top_quote: "示例：服务器需求还在爆", trend: null },
  { ticker: "COIN", name_cn: "Coinbase", mentions: 1, top_quote: "示例：币圈回暖交易量起来了", trend: { dir: "new" as const, delta_pct: null, prev_score: 0 } },
  { ticker: "MSTR", name_cn: "MicroStrategy", mentions: 1, top_quote: "示例：这是币的杠杆代理", trend: null },
];

function entry(s: DemoSpec): RankingEntry {
  return {
    ticker: s.ticker, name_cn: s.name_cn, score: s.score,
    note_count: s.notes, comment_count: s.comments,
    note_count_raw: s.notes + Math.round(s.notes * 0.4),
    comment_count_raw: s.comments + Math.round(s.comments * 0.2),
    mentions: s.notes + s.comments, tracked: !!s.tracked,
    sentiment_counts: { bullish: s.bull, bearish: s.bear, neutral: s.neutral },
    quote: {
      price: s.price, change_pct: s.change,
      market_date: new Date(NOW() - 18 * H).toISOString().slice(0, 10),
      quoted_at_ms: NOW() - 2 * H,
    },
    divergence: !!s.divergence,
    analysis_status: "ok",
    analysis_age_ms: 2 * H + 12 * 60_000,
    latest_item_age_ms: 34 * 60_000,
    trend: { dir: s.trendDir, delta_pct: s.trendPct, prev_score: s.score * 0.8 },
    history: hist(s.score, s.shape),
  };
}

export function demoRanking(): RankingResponse {
  return { ranking: SPECS.map(entry), radar: RADAR, now_ms: NOW() };
}

export function demoDetail(ticker: string): StockDetail | null {
  const s = SPECS.find((x) => x.ticker === ticker);
  if (!s) return null;
  return {
    ticker: s.ticker, name_cn: s.name_cn,
    analysis: {
      status: "ok", summary: s.summary,
      sentiment_counts: { bullish: s.bull, bearish: s.bear, neutral: s.neutral },
      bull_points: s.bulls, bear_points: s.bears, notable_quotes: s.quotes,
      irrelevant_item_count: 2, input_item_count: s.notes + s.comments,
      age_ms: 2 * H, generated_at_ms: NOW() - 2 * H, error: null,
      model: "demo", cost_usd: 0,
    },
    items: s.quotes.map((q, i) => ({
      type: i === 0 ? "note" : "comment", text: q,
      likes: 300 - i * 90, age_ms: (i + 1) * 2 * H, url: null, cluster_size: i === 1 ? 3 : 1,
    })),
    now_ms: NOW(),
  };
}

export function demoScoreboard(): Scoreboard {
  const day = (n: number) => new Date(NOW() - n * 24 * H).toISOString().slice(0, 10);
  const calls = [
    { ticker: "NVDA", date: day(1), dir: "up" as const, net: 9, move_1d_pct: 1.8, correct_1d: true, move_7d_pct: null, correct_7d: null },
    { ticker: "TSLA", date: day(2), dir: "down" as const, net: -5, move_1d_pct: 3.1, correct_1d: false, move_7d_pct: null, correct_7d: null },
    { ticker: "PLTR", date: day(3), dir: "up" as const, net: 7, move_1d_pct: 4.2, correct_1d: true, move_7d_pct: 6.5, correct_7d: true },
    { ticker: "BABA", date: day(5), dir: "up" as const, net: 4, move_1d_pct: -1.2, correct_1d: false, move_7d_pct: 2.8, correct_7d: true },
    { ticker: "AAPL", date: day(6), dir: "down" as const, net: -3, move_1d_pct: -0.8, correct_1d: true, move_7d_pct: -2.1, correct_7d: true },
  ];
  return {
    window_days: 30,
    overall: { evaluated_1d: 5, correct_1d: 3, hit_rate_1d: 60.0, evaluated_7d: 3, correct_7d: 3, hit_rate_7d: 100.0 },
    by_ticker: {},
    calls,
  };
}

export function demoStatus(): Status {
  return {
    last_run: null, running: false, job: null, login_required: false,
    guardrails: {
      cooldown_until_ms: null, gap_until_ms: null,
      budget: { limit: 15000, used_24h: 0, estimated_next_cycle: 2520, exhausted: false },
    },
    scheduler: { enabled: true, interval_hours: 5, next_run_at_ms: null },
    now_ms: NOW(), window_hours: 24, has_api_key: true,
  };
}
