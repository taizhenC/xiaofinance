import { relTime } from "../format";
import { navigate } from "../router";
import { S } from "../strings";
import type { RankingEntry, StockDetail } from "../types";
import {
  DivergenceBadge, QuoteBadge, SentimentDonut, SentimentLegend, Skeleton, Sparkline, TrendBadge,
} from "./bits";

export function CardMeta({ e, d }: { e: RankingEntry; d: StockDetail | null }) {
  const a = d?.analysis ?? null;
  const stale = a != null && a.age_ms > 6 * 3600_000;
  return (
    <div class="card-meta">
      <TrendBadge trend={e.trend} />
      <QuoteBadge e={e} />
      <DivergenceBadge on={e.divergence} />
      <span class="badge">
        {S.notesComments(e.note_count, e.comment_count)}
        {e.note_count_raw > e.note_count ? (
          <span title="含转发重复"> {S.rawCounts(e.note_count_raw, e.comment_count_raw)}</span>
        ) : null}
      </span>
      {e.latest_item_age_ms != null ? (
        <span class="badge">{S.latestContent(relTime(e.latest_item_age_ms))}</span>
      ) : null}
      {a ? (
        <span class={`badge ${stale ? "badge-amber" : ""}`}>{S.analyzedAt(relTime(a.age_ms))}</span>
      ) : null}
      {a?.status === "ok" && a.irrelevant_item_count != null && a.input_item_count ? (
        <span class="badge" title="模型判定与该股投资无关的条目">
          {S.noiseRemoved(a.irrelevant_item_count, a.input_item_count)}
        </span>
      ) : null}
    </div>
  );
}

export function AnalysisBody({ e, d, full = false }: {
  e: RankingEntry | null; d: StockDetail; full?: boolean;
}) {
  const a = d.analysis;
  if (a && (a.status === "ok" || a.status === "no_api_key")) {
    return (
      <>
        {a.status === "ok" && a.sentiment_counts ? (
          <div class="senti-row">
            <SentimentDonut sc={a.sentiment_counts} />
            <SentimentLegend sc={a.sentiment_counts} />
          </div>
        ) : null}
        {a.status === "no_api_key" ? <p class="muted small">{S.keylessQuotes}</p> : null}
        {a.summary ? <p class="summary">{a.summary}</p> : null}
        {a.bull_points?.length ? (
          <>
            {full ? <h3 class="points-title bull-title">▲ {S.bullPoints}</h3> : null}
            <ul class="points bull">{a.bull_points.map((p) => <li key={p}>{p}</li>)}</ul>
          </>
        ) : null}
        {a.bear_points?.length ? (
          <>
            {full ? <h3 class="points-title bear-title">▼ {S.bearPoints}</h3> : null}
            <ul class="points bear">{a.bear_points.map((p) => <li key={p}>{p}</li>)}</ul>
          </>
        ) : null}
        {a.notable_quotes?.length ? (
          <>
            {full ? <h3 class="points-title">{S.quotesTitle}</h3> : null}
            {a.notable_quotes.map((q) => <blockquote class="quote" key={q}>{q}</blockquote>)}
          </>
        ) : null}
      </>
    );
  }
  if (a?.status === "error") {
    return <p class="muted small">{S.analysisFailed(a.error ?? "unknown")}</p>;
  }
  if (e && e.mentions === 0) return <p class="muted small">{S.noFreshContent}</p>;
  return <p class="muted small">{S.belowThreshold}</p>;
}

export function SourceItems({ d, open = false }: { d: StockDetail; open?: boolean }) {
  if (!d.items.length) return null;
  return (
    <details class="items-toggle" open={open}>
      <summary>{S.viewSources(d.items.length)}</summary>
      {d.items.map((i, idx) => (
        <div class="item-line" key={idx}>
          <span class="q-meta">
            [{i.type === "note" ? "帖" : "评"}] {relTime(i.age_ms)} 赞{i.likes}
            {i.cluster_size > 1 ? ` ${S.clusterDup(i.cluster_size)}` : ""}
          </span>
          {i.text}
          {i.url ? (
            <>
              {" "}
              <a href={i.url} target="_blank" rel="noopener noreferrer">{S.sourceOriginal}</a>
            </>
          ) : null}
        </div>
      ))}
    </details>
  );
}

export function StockCard({ e, d }: { e: RankingEntry; d: StockDetail | null }) {
  return (
    <article class="card" id={`card-${e.ticker}`}>
      <div class="card-head">
        <button class="tk tk-link" onClick={() => navigate({ name: "stock", ticker: e.ticker })}>
          {e.ticker}
        </button>
        <span class="cn">{e.name_cn}</span>
        {e.tracked ? <span class="pin" title={S.tracked}>📌</span> : null}
        <a class="detail-link" href={`#/stock/${e.ticker}`}>{S.detailOpen} →</a>
      </div>
      {e.history.length >= 3 ? (
        <div class="spark-row">
          <Sparkline history={e.history} />
          <span class="muted small">{S.scoreTrend}</span>
        </div>
      ) : null}
      {d ? (
        <>
          <CardMeta e={e} d={d} />
          <AnalysisBody e={e} d={d} />
          <SourceItems d={d} />
        </>
      ) : (
        <Skeleton lines={4} />
      )}
    </article>
  );
}
