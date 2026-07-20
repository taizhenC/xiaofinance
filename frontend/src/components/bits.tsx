/** Small shared presentational pieces: badges, sparkline, sentiment donut,
 * skeletons, empty states, the ever-present disclaimer (TR-01). */

import type { ComponentChildren } from "preact";
import { useChart } from "../charts";
import { pct } from "../format";
import { S } from "../strings";
import type { RankingEntry, SentimentCounts, Trend } from "../types";

export function TrendBadge({ trend }: { trend: Trend | null }) {
  if (!trend) return null;
  if (trend.dir === "new") {
    return <span class="badge badge-heat" title={S.trendTitleNew}>{S.badgeNew}</span>;
  }
  if (trend.dir === "up" && trend.delta_pct != null) {
    return <span class="badge badge-heat" title={S.trendTitle}>{S.badgeUp(trend.delta_pct)}</span>;
  }
  if (trend.dir === "down" && trend.delta_pct != null) {
    return <span class="badge badge-cool" title={S.trendTitle}>{S.badgeDown(trend.delta_pct)}</span>;
  }
  return null;
}

export function QuoteBadge({ e }: { e: RankingEntry }) {
  const q = e.quote;
  if (!q || q.change_pct == null) return null;
  const cls = q.change_pct > 0 ? "quote-up" : q.change_pct < 0 ? "quote-down" : "";
  return (
    <span class="badge" title={S.quoteTitle(q.market_date)}>
      股价 ${q.price} <b class={cls}>{pct(q.change_pct)}</b>
    </span>
  );
}

export function DivergenceBadge({ on }: { on: boolean }) {
  if (!on) return null;
  return <span class="badge badge-amber" title={S.divergenceTitle}>{S.badgeDivergence}</span>;
}

export function DemoBadge() {
  return <span class="badge badge-demo">{S.demoBadge}</span>;
}

export function Disclaimer({ compact = false }: { compact?: boolean }) {
  return (
    <span class={`disclaimer ${compact ? "disclaimer-compact" : ""}`} title={S.disclaimerLong}>
      {S.disclaimer}
    </span>
  );
}

export function Sparkline({ history }: { history: { ts: number; score: number }[] }) {
  if (!history || history.length < 3) return null;
  const w = 130, h = 26, padding = 3;
  const scores = history.map((p) => p.score);
  const max = Math.max(...scores, 1);
  const pts = history.map((p, i) => {
    const x = padding + (i * (w - 2 * padding)) / (history.length - 1);
    const y = h - padding - (p.score / max) * (h - 2 * padding);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const [lx, ly] = pts[pts.length - 1].split(",");
  const title = `${S.scoreTrend}（近${history.length}个抓取周期）`;
  return (
    <svg class="spark" width={w} height={h} viewBox={`0 0 ${w} ${h}`} role="img" aria-label={title}>
      <title>{title}</title>
      <polyline
        points={pts.join(" ")} fill="none" stroke="var(--accent)"
        stroke-width="1.5" stroke-linejoin="round"
      />
      <circle cx={lx} cy={ly} r="2.2" fill="var(--accent)" />
    </svg>
  );
}

export function SentimentDonut({ sc }: { sc: SentimentCounts }) {
  const net = sc.bullish - sc.bearish;
  const { ref } = useChart(
    (t) => ({
      backgroundColor: "transparent",
      series: [{
        type: "pie",
        radius: ["62%", "85%"],
        itemStyle: { borderColor: t.panel2, borderWidth: 2 },
        label: { show: false },
        silent: true,
        data: [
          { value: sc.bullish, itemStyle: { color: t.bull } },
          { value: sc.bearish, itemStyle: { color: t.bear } },
          { value: sc.neutral, itemStyle: { color: t.neut } },
        ],
      }],
      graphic: [{
        type: "text", left: "center", top: "middle",
        style: {
          text: net > 0 ? `▲${net}` : net < 0 ? `▼${-net}` : "±0",
          fill: net > 0 ? t.bull : net < 0 ? t.bear : t.neut,
          fontSize: 15, fontFamily: t.mono,
        },
      }],
    }),
    [sc.bullish, sc.bearish, sc.neutral],
  );
  return <div class="senti-donut" ref={ref} />;
}

export function SentimentLegend({ sc }: { sc: SentimentCounts }) {
  return (
    <div class="senti-legend">
      <span><i class="dot dot-bull" />▲ {S.bullish} <b>{sc.bullish}</b></span>
      <span><i class="dot dot-bear" />▼ {S.bearish} <b>{sc.bearish}</b></span>
      <span><i class="dot dot-neut" />— {S.neutral} <b>{sc.neutral}</b></span>
    </div>
  );
}

export function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div class="skeleton" aria-hidden="true">
      {Array.from({ length: lines }, (_, i) => (
        <div class="skeleton-line" style={{ width: `${88 - (i % 3) * 18}%` }} key={i} />
      ))}
    </div>
  );
}

export function Empty({ children }: { children: ComponentChildren }) {
  return <div class="empty">{children}</div>;
}

export function Panel(props: {
  title: ComponentChildren;
  subtitle?: string;
  right?: ComponentChildren;
  children: ComponentChildren;
  id?: string;
}) {
  return (
    <section class="panel" id={props.id}>
      <div class="panel-head">
        <h2>
          {props.title}
          {props.subtitle ? <span class="muted small"> {props.subtitle}</span> : null}
        </h2>
        {props.right}
      </div>
      {props.children}
    </section>
  );
}
