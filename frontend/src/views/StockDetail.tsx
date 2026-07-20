/** UX-02's routed per-stock detail view: summary → bull/bear → verified
 * quotes with source links → sparkline + price context → all source items.
 * URL (#/stock/NVDA) is shareable locally. */

import { useEffect, useState } from "preact/hooks";
import { api } from "../api";
import { useChart } from "../charts";
import { Disclaimer, Empty, Panel, Skeleton, Sparkline } from "../components/bits";
import { AnalysisBody, CardMeta, SourceItems } from "../components/StockCard";
import { demoDetail } from "../demo";
import { relTime } from "../format";
import { demoActive, displayRanking } from "../state";
import { S } from "../strings";
import type { StockDetail as Detail } from "../types";

function PriceStrip({ ticker }: { ticker: string }) {
  const entry = displayRanking.value?.ranking.find((e) => e.ticker === ticker);
  const q = entry?.quote;
  if (!q || q.change_pct == null) return null;
  const cls = q.change_pct > 0 ? "quote-up" : q.change_pct < 0 ? "quote-down" : "";
  return (
    <Panel title={S.priceContext}>
      <p>
        <b class="tk">${q.price}</b>{" "}
        <b class={cls}>{q.change_pct > 0 ? "+" : ""}{q.change_pct}%</b>
        <span class="muted small"> · {S.quoteTitle(q.market_date)}</span>
      </p>
      {entry && entry.history.length >= 3 ? (
        <div class="spark-row">
          <Sparkline history={entry.history} />
          <span class="muted small">{S.scoreTrend}</span>
        </div>
      ) : null}
      <HistoryChart ticker={ticker} />
    </Panel>
  );
}

function HistoryChart({ ticker }: { ticker: string }) {
  const entry = displayRanking.value?.ranking.find((e) => e.ticker === ticker);
  const hist = entry?.history ?? [];
  const { ref } = useChart(
    (t) => {
      if (hist.length < 3) return null;
      return {
        backgroundColor: "transparent",
        grid: { left: 8, right: 16, top: 10, bottom: 20, containLabel: true },
        xAxis: {
          type: "category",
          data: hist.map((p) => new Date(p.ts).toLocaleString("zh-CN", {
            month: "numeric", day: "numeric", hour: "2-digit", hour12: false,
          })),
          axisLabel: { color: t.ink3, fontSize: 10 },
          axisLine: { lineStyle: { color: t.grid } },
        },
        yAxis: {
          type: "value",
          axisLabel: { color: t.ink3, fontSize: 10 },
          splitLine: { lineStyle: { color: t.grid } },
        },
        series: [{
          type: "line", smooth: true, symbol: "circle", symbolSize: 4,
          data: hist.map((p) => p.score),
          lineStyle: { color: t.accent, width: 2 },
          itemStyle: { color: t.accent },
          areaStyle: { opacity: 0.08, color: t.accent },
        }],
        tooltip: {
          trigger: "axis",
          backgroundColor: t.panel2, borderColor: t.grid,
          textStyle: { color: t.ink, fontSize: 12 },
        },
      };
    },
    [ticker, hist.length],
  );
  if (hist.length < 3) return null;
  return <div ref={ref} style={{ height: "180px" }} />;
}

export function StockDetailView({ ticker }: { ticker: string }) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState(false);
  const demo = demoActive.value;

  useEffect(() => {
    setDetail(null);
    setError(false);
    if (demo) {
      setDetail(demoDetail(ticker));
      return;
    }
    let alive = true;
    api.stock(ticker).then(
      (d) => alive && setDetail(d),
      () => alive && setError(true),
    );
    return () => {
      alive = false;
    };
  }, [ticker, demo]);

  const entry = displayRanking.value?.ranking.find((e) => e.ticker === ticker) ?? null;

  return (
    <main>
      <div class="detail-nav">
        <a href="#/" class="btn btn-mini">{S.backHome}</a>
        <Disclaimer />
      </div>
      <section class="panel detail-head">
        <div class="card-head">
          <span class="tk tk-xl">{ticker}</span>
          <span class="cn">{detail?.name_cn ?? entry?.name_cn ?? ""}</span>
          {entry?.tracked ? <span class="pin">📌 {S.tracked}</span> : null}
          {detail?.analysis ? (
            <span class="muted small" style={{ marginLeft: "auto" }}>
              {S.analyzedAt(relTime(detail.analysis.age_ms))}
            </span>
          ) : null}
        </div>
        {entry && detail ? <CardMeta e={entry} d={detail} /> : null}
      </section>

      {error ? (
        <Empty>{S.requestFailed("加载详情")}</Empty>
      ) : !detail ? (
        <section class="panel"><Skeleton lines={6} /></section>
      ) : (
        <>
          <section class="panel">
            <AnalysisBody e={entry} d={detail} full />
          </section>
          <PriceStrip ticker={ticker} />
          <Panel title={S.detailItems}>
            {detail.items.length === 0 ? (
              <Empty>{S.detailNoData}</Empty>
            ) : (
              <SourceItems d={detail} open />
            )}
          </Panel>
        </>
      )}
      <footer class="muted small">{S.footer} · <b>{S.disclaimer}</b></footer>
    </main>
  );
}
