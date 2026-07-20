import { S } from "./strings";

export function relTime(ageMs: number | null | undefined): string {
  if (ageMs == null) return S.never;
  const m = Math.floor(ageMs / 60000);
  if (m < 1) return S.justNow;
  if (m < 60) return S.minutesAgo(m);
  const h = Math.floor(m / 60);
  if (h < 48) return S.hoursAgo(h);
  return S.daysAgo(Math.floor(h / 24));
}

export function localTime(ms: number | null | undefined): string {
  return ms ? new Date(ms).toLocaleString("zh-CN", { hour12: false }) : S.never;
}

export function shortTime(ms: number | null | undefined): string {
  return ms
    ? new Date(ms).toLocaleString("zh-CN", {
        month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
      })
    : S.never;
}

export function duration(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}min${s % 60 ? ` ${s % 60}s` : ""}`;
  return `${Math.floor(m / 60)}h ${m % 60}min`;
}

export function countdown(untilMs: number, nowMs: number): string {
  const s = Math.max(0, Math.round((untilMs - nowMs) / 1000));
  const m = Math.floor(s / 60);
  if (m >= 60) return `${Math.floor(m / 60)}小时${m % 60}分`;
  if (m >= 1) return `${m}分${s % 60}秒`;
  return `${s}秒`;
}

export function pct(v: number | null | undefined): string {
  if (v == null) return "";
  return `${v > 0 ? "+" : ""}${v}%`;
}
