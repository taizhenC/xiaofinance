/** Home-page side panels: radar strip, tracked manager, scoreboard,
 * alias suggestions, runs table. */

import { useState } from "preact/hooks";
import { api } from "../api";
import { duration, localTime, pct } from "../format";
import { navigate } from "../router";
import { displayScoreboard, refreshData, suggestions, toast, tracked } from "../state";
import { runErrorLabel, S, statusLabel } from "../strings";
import type { FetchRun, RadarEntry, ScoreboardCall } from "../types";
import { Empty, Panel } from "./bits";

export function RadarStrip({ radar }: { radar: RadarEntry[] }) {
  if (!radar.length) return null;
  return (
    <Panel title={S.radarTitle} subtitle={S.radarSubtitle}>
      <div class="chip-row">
        {radar.map((r) => (
          <span class="chip" title={r.top_quote ?? ""} key={r.ticker}>
            {r.trend?.dir === "new" ? "🔥 " : ""}
            <span class="tk">{r.ticker}</span> {r.name_cn} ·{r.mentions}
            <button
              title={S.trackedAdd}
              onClick={() => {
                void api.trackAdd(r.ticker).then(refreshData)
                  .catch(() => toast("error", S.requestFailed("添加跟踪")));
              }}
            >
              ＋
            </button>
          </span>
        ))}
      </div>
    </Panel>
  );
}

export function TrackedPanel() {
  const [ticker, setTicker] = useState("");
  const [kws, setKws] = useState("");
  const submit = (ev: Event) => {
    ev.preventDefault();
    const t = ticker.trim().toUpperCase();
    if (!/^[A-Z]{1,5}$/.test(t)) return;
    void api.trackAdd(t, kws.trim() || undefined)
      .then(() => {
        setTicker(""); setKws("");
        return refreshData();
      })
      .catch(() => toast("error", S.requestFailed("添加跟踪")));
  };
  return (
    <Panel title={S.trackedTitle}>
      <form class="track-form" onSubmit={submit}>
        <input
          value={ticker}
          onInput={(e) => setTicker((e.target as HTMLInputElement).value)}
          placeholder={S.trackedPlaceholderTicker}
          maxLength={5} required pattern="[A-Za-z]{1,5}"
        />
        <input
          value={kws}
          onInput={(e) => setKws((e.target as HTMLInputElement).value)}
          placeholder={S.trackedPlaceholderKw}
        />
        <button class="btn" type="submit">{S.trackedAdd}</button>
      </form>
      <div class="chip-row">
        {tracked.value.length === 0 ? (
          <span class="muted small">{S.trackedEmpty}</span>
        ) : (
          tracked.value.map((t) => (
            <span class="chip" key={t.ticker}>
              <span class="tk">{t.ticker}</span>
              {t.custom_keywords.length ? ` ${t.custom_keywords.join(", ")}` : ""}
              <button
                title={S.trackedRemove}
                onClick={() => {
                  void api.trackRemove(t.ticker).then(refreshData)
                    .catch(() => toast("error", S.requestFailed("移除跟踪")));
                }}
              >
                ✕
              </button>
            </span>
          ))
        )}
      </div>
    </Panel>
  );
}

function callCell(mv: number | null, ok: boolean | null) {
  if (mv == null) return <td class="muted">{S.scoreboardPending}</td>;
  return <td>{pct(mv)} {ok ? "✓" : "✗"}</td>;
}

export function ScoreboardPanel() {
  const data = displayScoreboard.value;
  const fmt = (rate: number | null, correct: number, n: number) =>
    rate == null ? "—" : `${rate}% (${correct}/${n})`;
  return (
    <details class="panel collapsible">
      <summary>
        {S.scoreboardTitle}{" "}
        {data?.overall.hit_rate_1d != null ? (
          <span class="badge">{S.scoreboardNextDay} {data.overall.hit_rate_1d}%</span>
        ) : null}
      </summary>
      {!data || !data.calls.length ? (
        <p class="muted small">{S.scoreboardEmpty}</p>
      ) : (
        <>
          <p class="small muted">
            {S.scoreboardNote(data.window_days)}{" "}
            {S.scoreboardNextDay} <b>{fmt(data.overall.hit_rate_1d, data.overall.correct_1d, data.overall.evaluated_1d)}</b>
            {" · "}
            {S.scoreboard7d} <b>{fmt(data.overall.hit_rate_7d, data.overall.correct_7d, data.overall.evaluated_7d)}</b>
          </p>
          <div class="table-wrap">
            <table>
              <thead>
                <tr><th>日期</th><th>代码</th><th>舆论</th><th>{S.scoreboardNextDay}</th><th>{S.scoreboard7d}</th></tr>
              </thead>
              <tbody>
                {data.calls.map((c: ScoreboardCall) => (
                  <tr key={`${c.ticker}-${c.date}`}>
                    <td>{c.date}</td>
                    <td class="tk">{c.ticker}</td>
                    <td>
                      {c.dir === "up"
                        ? <span class="lean-bull">▲ {S.bullish}</span>
                        : <span class="lean-bear">▼ {S.bearish}</span>}{" "}
                      ({c.net > 0 ? "+" : ""}{c.net})
                    </td>
                    {callCell(c.move_1d_pct, c.correct_1d)}
                    {callCell(c.move_7d_pct, c.correct_7d)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </details>
  );
}

export function SuggestionsPanel() {
  const list = suggestions.value;
  const act = (id: number, action: "accept" | "reject") => {
    void api.suggestionAction(id, action)
      .then(refreshData)
      .catch(() => toast("error", S.requestFailed("处理建议")));
  };
  return (
    <details class="panel collapsible">
      <summary>
        {S.suggestionsTitle} {list.length ? <span class="badge">{list.length}</span> : null}
      </summary>
      {list.length === 0 ? (
        <p class="muted small">{S.suggestionsEmpty}</p>
      ) : (
        list.map((s) => (
          <div class="sugg-row" key={s.id}>
            <span class="term">{s.term}</span> →
            <span class="tk">{s.guessed_ticker}</span>
            <button class="btn btn-mini" onClick={() => act(s.id, "accept")}>{S.accept}</button>
            <button class="btn btn-mini" onClick={() => act(s.id, "reject")}>{S.reject}</button>
            <span class="evi">“{s.evidence_quote ?? ""}”</span>
          </div>
        ))
      )}
    </details>
  );
}

export function RunsPanel({ runs }: { runs: FetchRun[] }) {
  return (
    <details class="panel collapsible">
      <summary>{S.runsTitle}</summary>
      {runs.length === 0 ? (
        <Empty>{S.emptyRanking}</Empty>
      ) : (
        <div class="table-wrap">
          <table>
            <thead>
              <tr>{S.runsHeaders.map((h) => <th key={h}>{h}</th>)}</tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id}>
                  <td>{r.id}</td>
                  <td>{S.runMode[r.mode] ?? r.mode}</td>
                  <td class={`st-${r.status}`}>{statusLabel(r.status)}</td>
                  <td>{r.notes_fresh}帖/{r.comments_fresh}评</td>
                  <td title="本轮抓取的内容条数（笔记+评论页），计入每日预算">{r.requests_est || 0}</td>
                  <td>{r.finished_at_ms ? duration(r.finished_at_ms - r.started_at_ms) : "—"}</td>
                  <td>{localTime(r.started_at_ms)}</td>
                  <td class="err" title={r.error ?? ""}>{runErrorLabel(r.error)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </details>
  );
}

export function ChipLink({ ticker }: { ticker: string }) {
  return (
    <button class="tk tk-link" onClick={() => navigate({ name: "stock", ticker })}>{ticker}</button>
  );
}
