import type {
  AliasSuggestion,
  DoctorCheck,
  FetchBlock,
  RankingResponse,
  Scoreboard,
  SessionHealth,
  Status,
  StockDetail,
  FetchRun,
  TrackedStock,
} from "./types";

/** Error carrying the HTTP status and the parsed FastAPI `detail`, so the UI
 * can turn guardrail blocks (429) and validation errors into real guidance. */
export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(path: string, status: number, detail: unknown) {
    super(`${path}: ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    let detail: unknown = null;
    try {
      detail = (await res.json())?.detail ?? null;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(path, res.status, detail);
  }
  return res.json() as Promise<T>;
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
}

export const api = {
  status: () => request<Status>("/api/status"),
  ranking: () => request<RankingResponse>("/api/ranking"),
  stock: (ticker: string) => request<StockDetail>(`/api/stocks/${ticker}`),
  runs: (limit = 20) => request<FetchRun[]>(`/api/runs?limit=${limit}`),
  scoreboard: () => request<Scoreboard>("/api/scoreboard"),
  suggestions: () => request<AliasSuggestion[]>("/api/alias_suggestions"),
  suggestionAction: (id: number, action: "accept" | "reject") =>
    post(`/api/alias_suggestions/${id}`, { action }),
  tracked: () => request<TrackedStock[]>("/api/tracked"),
  trackAdd: (ticker: string, custom_keywords?: string) =>
    post<TrackedStock>("/api/tracked", { ticker, custom_keywords }),
  trackRemove: (ticker: string) =>
    request(`/api/tracked/${ticker}`, { method: "DELETE" }),
  fetchNow: (mode = "both", force = false) =>
    post<{ started: boolean; job_id: number }>("/api/fetch", { mode, force }),
  fetchCancel: () => post("/api/fetch/cancel"),
  session: () => request<SessionHealth>("/api/session"),
  sessionLogin: (timeout_min = 6) => post("/api/session/login", { timeout_min }),
  sessionCookies: (cookies: string) => post("/api/session/cookies", { cookies }),
  sessionCookiesClear: () =>
    request<{ configured: boolean }>("/api/session/cookies", { method: "DELETE" }),
  sessionConfig: (xhs_international: boolean) =>
    post("/api/session/config", { xhs_international }),
  doctor: () => request<{ checks: DoctorCheck[] }>("/api/doctor"),
};

export function asFetchBlock(err: unknown): FetchBlock | null {
  if (err instanceof ApiError && err.status === 429 && err.detail && typeof err.detail === "object") {
    return err.detail as FetchBlock;
  }
  return null;
}
