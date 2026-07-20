import { useEffect, useState } from "preact/hooks";
import { api } from "../api";
import { demoDetail } from "../demo";
import { Empty, Panel, Skeleton } from "../components/bits";
import { Banners } from "../components/Banners";
import {
  RadarStrip, RunsPanel, ScoreboardPanel, SuggestionsPanel, TrackedPanel,
} from "../components/panels";
import { SessionCenter } from "../components/SessionCenter";
import { StockCard } from "../components/StockCard";
import { RankingChart } from "../components/RankingChart";
import { ProgressBar } from "../components/TopBar";
import {
  demoActive, demoOverride, displayRanking, hasRealData, ranking, runs, sessionHealth,
} from "../state";
import { S } from "../strings";
import type { StockDetail } from "../types";

function useCardDetails(): Record<string, StockDetail | null> {
  const [details, setDetails] = useState<Record<string, StockDetail | null>>({});
  const r = displayRanking.value;
  const demo = demoActive.value;
  const key = r ? `${demo}:${r.ranking.map((e) => e.ticker).join(",")}` : "";

  useEffect(() => {
    if (!r) return;
    if (demo) {
      const out: Record<string, StockDetail | null> = {};
      for (const e of r.ranking) out[e.ticker] = demoDetail(e.ticker);
      setDetails(out);
      return;
    }
    let alive = true;
    void Promise.all(
      r.ranking.map((e) =>
        api.stock(e.ticker).then(
          (d) => [e.ticker, d] as const,
          () => [e.ticker, null] as const,
        ),
      ),
    ).then((pairs) => {
      if (alive) setDetails(Object.fromEntries(pairs));
    });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return details;
}

export function Home() {
  const r = displayRanking.value;
  const details = useCardDetails();
  const loading = ranking.value === null && !demoActive.value;
  const showWizardHint = !hasRealData.value && sessionHealth.value?.state !== "valid";

  return (
    <>
      <Banners />
      <ProgressBar />
      <main>
        {showWizardHint && !demoActive.value ? (
          <section class="panel hero">
            <h2>{S.wizardTitle}</h2>
            <p class="muted">{S.wizardWelcome}</p>
            <div class="hero-actions">
              <a class="btn btn-primary" href="#/wizard">{S.wizardOpen}</a>
              <button class="btn" onClick={() => (demoOverride.value = true)}>{S.demoEnter}</button>
            </div>
          </section>
        ) : null}

        <Panel
          title={S.rankingTitle}
          subtitle={S.rankingSubtitle}
          right={
            <span class="legend-inline">
              <i class="dot dot-bull" />{S.legendBull}
              <i class="dot dot-bear" />{S.legendBear}
              <i class="dot dot-neut" />{S.legendNeutral}
            </span>
          }
        >
          {loading ? (
            <Skeleton lines={5} />
          ) : !r || r.ranking.filter((e) => e.mentions > 0).length === 0 ? (
            <Empty>{S.emptyRanking}</Empty>
          ) : (
            <RankingChart entries={r.ranking} />
          )}
        </Panel>

        {r ? <RadarStrip radar={r.radar} /> : null}

        <section class="cards">
          {(r?.ranking ?? []).map((e) => (
            <StockCard key={e.ticker} e={e} d={details[e.ticker] ?? null} />
          ))}
        </section>

        <SessionCenter />
        <TrackedPanel />
        <ScoreboardPanel />
        <SuggestionsPanel />
        <RunsPanel runs={runs.value} />
      </main>
      <footer class="muted small">
        {S.footer} · <b>{S.disclaimer}</b>
      </footer>
    </>
  );
}
