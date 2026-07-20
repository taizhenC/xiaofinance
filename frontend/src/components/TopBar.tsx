import { countdown, duration, localTime } from "../format";
import { navigate } from "../router";
import { cancelFetch, demoActive, startFetch, status } from "../state";
import { S } from "../strings";
import { toggleTheme } from "../theme";
import { DemoBadge, Disclaimer } from "./bits";

function FetchControls() {
  const s = status.value;
  if (!s) return null;
  const g = s.guardrails;
  const blocked = g.cooldown_until_ms != null;
  if (s.running) {
    return (
      <button class="btn" onClick={() => void cancelFetch()}>
        <span class="spinner" /> {S.cancelFetch}
      </button>
    );
  }
  return (
    <button
      class="btn btn-primary"
      disabled={blocked}
      title={blocked && g.cooldown_until_ms ? S.cooldownBanner(countdown(g.cooldown_until_ms, s.now_ms)) : undefined}
      onClick={() => void startFetch()}
    >
      {S.fetchNow}
    </button>
  );
}

export function ProgressBar() {
  const s = status.value;
  const job = s?.job;
  if (!s || !job || job.done || !s.running) return null;
  const stageLabel = S.stage[job.stage] ?? job.stage;
  const d = job.detail;
  let detailText = "";
  if (job.stage.startsWith("crawl:")) {
    detailText = S.crawlProgress(Number(d.notes_seen ?? 0), Number(d.comments_seen ?? 0));
  } else if (job.stage === "analyze" && d.total != null) {
    detailText = S.analyzeProgress(Number(d.done ?? 0), Number(d.total), d.ticker as string | undefined);
  }
  const stages = ["crawl", "ingest", "dedup", "mentions", "analyze", "prices", "cleanup"];
  const currentIdx = stages.findIndex((x) => job.stage.startsWith(x));
  return (
    <div class="progress-bar" role="status">
      <span class="spinner" />
      <div class="progress-stages">
        {stages.map((st, i) => (
          <span
            key={st}
            class={`progress-stage ${i < currentIdx ? "done" : ""} ${i === currentIdx ? "active" : ""}`}
          >
            {S.stage[st === "crawl" ? job.stage.startsWith("crawl") ? job.stage : "crawl:discovery" : st === "ingest" ? "ingest:discovery" : st]?.split("·")[0] ?? st}
          </span>
        ))}
      </div>
      <span class="progress-detail">
        {stageLabel}{detailText ? ` · ${detailText}` : ""} · {S.elapsed(duration((s.now_ms ?? Date.now()) - job.started_at_ms))}
      </span>
    </div>
  );
}

export function TopBar() {
  const s = status.value;
  return (
    <header class="topbar">
      <div class="brand" onClick={() => navigate({ name: "home" })}>
        <h1>{S.appName}</h1>
        <span class="badge badge-window">{s ? S.windowBadge(s.window_hours) : S.tagline}</span>
        {demoActive.value ? <DemoBadge /> : null}
        <Disclaimer compact />
      </div>
      <div class="topbar-status">
        <span class="muted small">
          {s?.last_run
            ? S.lastFetch(localTime(s.last_run.finished_at_ms ?? s.last_run.started_at_ms), s.last_run.status)
            : S.lastFetchNever}
        </span>
        {s?.scheduler.enabled && s.scheduler.next_run_at_ms ? (
          <span class="muted small hide-narrow">{S.nextAutoRun(localTime(s.scheduler.next_run_at_ms))}</span>
        ) : null}
        <button class="btn btn-icon" title={S.themeToggle} onClick={toggleTheme}>◐</button>
        <FetchControls />
      </div>
    </header>
  );
}
