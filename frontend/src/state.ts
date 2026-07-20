/** Global state: signals + the polling loop. Polling is 3s while a cycle
 * runs, 30s idle (as before); a run finishing triggers a data refresh.
 * Demo mode substitutes the bundled synthetic dataset until real data exists. */

import { computed, signal } from "@preact/signals";
import { api, asFetchBlock } from "./api";
import { demoRanking, demoScoreboard } from "./demo";
import { countdown } from "./format";
import { S } from "./strings";
import type {
  AliasSuggestion, RankingResponse, Scoreboard, SessionHealth, Status, TrackedStock, FetchRun,
} from "./types";

export const status = signal<Status | null>(null);
export const ranking = signal<RankingResponse | null>(null);
export const runs = signal<FetchRun[]>([]);
export const scoreboard = signal<Scoreboard | null>(null);
export const suggestions = signal<AliasSuggestion[]>([]);
export const tracked = signal<TrackedStock[]>([]);
export const sessionHealth = signal<SessionHealth | null>(null);
export const connectionLost = signal(false);

/** Demo mode (UX-01): null = auto (on while there is no real data at all),
 * true/false = user override. */
export const demoOverride = signal<boolean | null>(null);
export const hasRealData = computed(
  () => (ranking.value?.ranking.length ?? 0) > 0 || runs.value.length > 0,
);
export const demoActive = computed(() =>
  demoOverride.value !== null ? demoOverride.value : ranking.value !== null && !hasRealData.value,
);

export const displayRanking = computed<RankingResponse | null>(() =>
  demoActive.value ? demoRanking() : ranking.value,
);
export const displayScoreboard = computed<Scoreboard | null>(() =>
  demoActive.value ? demoScoreboard() : scoreboard.value,
);

// ---- toasts ---------------------------------------------------------------

export interface Toast {
  id: number;
  kind: "info" | "error" | "success";
  text: string;
}

export const toasts = signal<Toast[]>([]);
let toastSeq = 0;

export function toast(kind: Toast["kind"], text: string, ttlMs = 5000): void {
  const id = ++toastSeq;
  toasts.value = [...toasts.value, { id, kind, text }];
  setTimeout(() => {
    toasts.value = toasts.value.filter((t) => t.id !== id);
  }, ttlMs);
}

// ---- confirm dialog (guardrail force-confirm) -----------------------------

export interface ConfirmRequest {
  text: string;
  onConfirm: () => void;
}

export const confirmRequest = signal<ConfirmRequest | null>(null);

// ---- polling --------------------------------------------------------------

let pollTimer: number | undefined;
let wasRunning = false;

export async function loadStatus(): Promise<void> {
  try {
    const s = await api.status();
    status.value = s;
    connectionLost.value = false;
    if (wasRunning && !s.running) {
      void refreshData();
      void loadSession();
    }
    wasRunning = s.running;
    schedulePoll(s.running ? 3000 : 30000);
  } catch {
    connectionLost.value = true;
    schedulePoll(10000);
  }
}

function schedulePoll(ms: number): void {
  clearTimeout(pollTimer);
  pollTimer = window.setTimeout(() => void loadStatus(), ms);
}

export async function refreshData(): Promise<void> {
  const results = await Promise.allSettled([
    api.ranking(), api.runs(), api.scoreboard(), api.suggestions(), api.tracked(),
  ]);
  const [r, ru, sb, su, tr] = results;
  if (r.status === "fulfilled") ranking.value = r.value;
  if (ru.status === "fulfilled") runs.value = ru.value;
  if (sb.status === "fulfilled") scoreboard.value = sb.value;
  if (su.status === "fulfilled") suggestions.value = su.value;
  if (tr.status === "fulfilled") tracked.value = tr.value;
  if (results.some((x) => x.status === "rejected")) {
    toast("error", S.requestFailed("刷新数据"));
  }
}

export async function loadSession(): Promise<void> {
  try {
    sessionHealth.value = await api.session();
  } catch {
    /* session panel shows unknown */
  }
}

// ---- actions --------------------------------------------------------------

export async function startFetch(force = false): Promise<void> {
  try {
    await api.fetchNow("both", force);
    toast("success", S.fetchStarted);
    void loadStatus();
  } catch (err) {
    const block = asFetchBlock(err);
    if (!block) {
      toast("error", S.fetchConflict);
      return;
    }
    const now = status.value?.now_ms ?? Date.now();
    if (block.reason === "min_gap" && block.force_allowed && block.until_ms) {
      confirmRequest.value = {
        text: S.gapConfirm(countdown(block.until_ms, now)),
        onConfirm: () => void startFetch(true),
      };
    } else if (block.reason === "daily_budget" && block.force_allowed && block.budget) {
      confirmRequest.value = {
        text: S.budgetConfirm(block.budget.used_24h, block.budget.limit),
        onConfirm: () => void startFetch(true),
      };
    } else if (block.reason === "auth_cooldown" && block.until_ms) {
      toast("error", S.cooldownBanner(countdown(block.until_ms, now)));
    }
  }
}

export async function cancelFetch(): Promise<void> {
  try {
    await api.fetchCancel();
    toast("info", S.fetchCancelRequested);
    void loadStatus();
  } catch {
    toast("error", S.requestFailed("取消"));
  }
}

export function boot(): void {
  void loadStatus();
  void refreshData();
  void loadSession();
}
