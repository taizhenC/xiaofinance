import { useChart } from "../charts";
import { navigate } from "../router";
import { S } from "../strings";
import type { RankingEntry } from "../types";
import { useEffect } from "preact/hooks";

function netColorKey(e: RankingEntry): "bull" | "bear" | "neut" {
  const sc = e.sentiment_counts;
  if (!sc) return "neut";
  if (sc.bullish > sc.bearish) return "bull";
  if (sc.bearish > sc.bullish) return "bear";
  return "neut";
}

export function RankingChart({ entries }: { entries: RankingEntry[] }) {
  const withData = entries.filter((e) => e.mentions > 0);
  const rows = [...withData].sort((a, b) => a.score - b.score); // bottom-up for horizontal bars

  const { ref, chartRef } = useChart(
    (t) => {
      if (rows.length === 0) return null;
      return {
        backgroundColor: "transparent",
        grid: { left: 8, right: 30, top: 6, bottom: 22, containLabel: true },
        xAxis: {
          type: "value",
          axisLabel: { color: t.ink3, fontSize: 11 },
          splitLine: { lineStyle: { color: t.grid } },
          axisLine: { show: false },
        },
        yAxis: {
          type: "category",
          data: rows.map((e) => e.ticker),
          axisLabel: { color: t.ink, fontFamily: t.mono, fontSize: 12 },
          axisLine: { show: false },
          axisTick: { show: false },
        },
        series: [{
          type: "bar",
          barWidth: 14,
          data: rows.map((e) => ({
            value: e.score,
            itemStyle: { color: t[netColorKey(e)], borderRadius: [0, 4, 4, 0] },
          })),
        }],
        tooltip: {
          backgroundColor: t.panel2, borderColor: t.grid,
          textStyle: { color: t.ink, fontSize: 12 },
          formatter: (p: { dataIndex: number }) => {
            const e = rows[p.dataIndex];
            const sc = e.sentiment_counts;
            const senti = sc ? `▲${sc.bullish} ▼${sc.bearish} —${sc.neutral}` : S.noAnalysis;
            const raw =
              e.note_count_raw > e.note_count || e.comment_count_raw > e.comment_count
                ? S.rawCounts(e.note_count_raw, e.comment_count_raw) : "";
            const px = e.quote && e.quote.change_pct != null
              ? ` · $${e.quote.price} (${e.quote.change_pct > 0 ? "+" : ""}${e.quote.change_pct}%)` : "";
            return (
              `<b style="font-family:${t.mono}">${e.ticker}</b> ${e.name_cn}${px}<br>` +
              `${S.scoreWord} ${e.score} · ${S.notesComments(e.note_count, e.comment_count)} ${raw}<br>` +
              `${senti}${e.divergence ? `<br>${S.badgeDivergence}` : ""}<br>` +
              `<span style="color:${t.ink3}">${S.detailOpen} →</span>`
            );
          },
        },
      };
    },
    [entries],
  );

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    chart.off("click");
    chart.on("click", (p) => {
      const e = rows[p.dataIndex as number];
      if (e) navigate({ name: "stock", ticker: e.ticker });
    });
  });

  return (
    <div
      ref={ref}
      class="ranking-chart"
      style={{ height: `${Math.max(120, withData.length * 30 + 50)}px` }}
    />
  );
}
