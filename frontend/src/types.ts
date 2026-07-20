/** Typed models of the FastAPI payloads. These are the contract between the
 * Python backend and the UI — drift here used to be discoverable only as
 * blank cards at runtime. */

export interface Quote {
  price: number;
  change_pct: number | null;
  market_date: string;
  quoted_at_ms: number;
}

export interface SentimentCounts {
  bullish: number;
  bearish: number;
  neutral: number;
}

export interface Trend {
  dir: "new" | "up" | "down" | "flat";
  delta_pct: number | null;
  prev_score: number;
}

export interface HistoryPoint {
  ts: number;
  score: number;
}

export interface RankingEntry {
  ticker: string;
  name_cn: string;
  score: number;
  note_count: number;
  comment_count: number;
  note_count_raw: number;
  comment_count_raw: number;
  mentions: number;
  tracked: boolean;
  sentiment_counts: SentimentCounts | null;
  quote: Quote | null;
  divergence: boolean;
  analysis_status: string | null;
  analysis_age_ms: number | null;
  latest_item_age_ms: number | null;
  trend: Trend | null;
  history: HistoryPoint[];
}

export interface RadarEntry {
  ticker: string;
  name_cn: string;
  mentions: number;
  top_quote: string | null;
  trend: Trend | null;
}

export interface RankingResponse {
  ranking: RankingEntry[];
  radar: RadarEntry[];
  now_ms: number;
}

export interface Analysis {
  status: "ok" | "no_api_key" | "error" | "skipped_unchanged";
  summary: string | null;
  sentiment_counts: SentimentCounts | null;
  bull_points: string[] | null;
  bear_points: string[] | null;
  notable_quotes: string[] | null;
  irrelevant_item_count: number | null;
  input_item_count: number | null;
  age_ms: number;
  generated_at_ms: number;
  error: string | null;
  model: string | null;
  cost_usd: number | null;
}

export interface StockItem {
  type: "note" | "comment";
  text: string;
  likes: number;
  age_ms: number;
  url: string | null;
  cluster_size: number;
}

export interface StockDetail {
  ticker: string;
  name_cn: string;
  analysis: Analysis | null;
  items: StockItem[];
  now_ms: number;
}

export interface FetchRun {
  id: number;
  mode: "discovery" | "tracked";
  status: "running" | "success" | "partial" | "failed";
  keywords: string | null;
  started_at_ms: number;
  finished_at_ms: number | null;
  notes_fetched: number;
  notes_fresh: number;
  comments_fresh: number;
  requests_est: number;
  error: string | null;
}

export interface JobSnapshot {
  id: number;
  mode: string;
  stage: string;
  detail: Record<string, number | string>;
  started_at_ms: number;
  finished_at_ms: number | null;
  cancel_requested: boolean;
  cancelled: boolean;
  done: boolean;
  error: string | null;
}

export interface GuardrailBudget {
  limit: number;
  used_24h: number;
  estimated_next_cycle: number;
  exhausted: boolean;
}

export interface GuardrailState {
  cooldown_until_ms: number | null;
  gap_until_ms: number | null;
  budget: GuardrailBudget;
}

export interface Status {
  last_run: FetchRun | null;
  running: boolean;
  job: JobSnapshot | null;
  login_required: boolean;
  guardrails: GuardrailState;
  scheduler: {
    enabled: boolean;
    interval_hours: number;
    next_run_at_ms: number | null;
  };
  now_ms: number;
  window_hours: number;
  has_api_key: boolean;
}

export interface FetchBlock {
  reason: "auth_cooldown" | "min_gap" | "daily_budget";
  until_ms: number | null;
  retry_after_ms: number | null;
  force_allowed: boolean;
  budget?: GuardrailBudget;
}

export type SessionStateKind = "valid" | "expired" | "unauthorized" | "unknown";
export type DiagnosisKind =
  | "none"
  | "expired"
  | "backend_mismatch"
  | "try_cookie"
  | "account_gated";

export interface LoginJob {
  running: boolean;
  started_at_ms: number | null;
  finished_at_ms: number | null;
  outcome: { ok: boolean; state: string; detail: string } | null;
}

export interface SessionHealth {
  state: SessionStateKind;
  source: "qrcode" | "cookie";
  diagnosis: DiagnosisKind;
  xhs_international: boolean;
  cookie: { configured: boolean; format_ok: boolean | null };
  login_verified_at_ms: number | null;
  last_run_id: number | null;
  login_job: LoginJob;
}

export interface DoctorCheck {
  key: string;
  label: string;
  ok: boolean;
  required: boolean;
  detail: string;
  fix: string;
}

export interface TrackedStock {
  ticker: string;
  added_at_ms: number;
  custom_keywords: string[];
}

export interface AliasSuggestion {
  id: number;
  term: string;
  guessed_ticker: string;
  evidence_quote: string | null;
  evidence_note_id: string | null;
  suggested_at_ms: number;
  status: string;
}

export interface ScoreboardCall {
  ticker: string;
  date: string;
  dir: "up" | "down";
  net: number;
  move_1d_pct: number | null;
  correct_1d: boolean | null;
  move_7d_pct: number | null;
  correct_7d: boolean | null;
}

export interface ScoreboardAggregate {
  evaluated_1d: number;
  correct_1d: number;
  hit_rate_1d: number | null;
  evaluated_7d: number;
  correct_7d: number;
  hit_rate_7d: number | null;
}

export interface Scoreboard {
  window_days: number;
  overall: ScoreboardAggregate;
  by_ticker: Record<string, ScoreboardAggregate & { calls: number }>;
  calls: ScoreboardCall[];
}
