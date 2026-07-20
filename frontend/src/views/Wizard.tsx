/** UX-01 onboarding wizard: acknowledgment (TR-01) → environment checklist
 * with fix-it hints (backed by /api/doctor) → guided login (DC-01) → first
 * crawl with live progress (DC-02). */

import { useEffect, useState } from "preact/hooks";
import { api } from "../api";
import { ProgressBar } from "../components/TopBar";
import { SessionCenter } from "../components/SessionCenter";
import { loadSession, sessionHealth, startFetch, status } from "../state";
import { S } from "../strings";
import type { DoctorCheck } from "../types";

const ACK_KEY = "infinance-ack";

export function wizardAcknowledged(): boolean {
  return localStorage.getItem(ACK_KEY) === "1";
}

function StepDots({ step }: { step: number }) {
  return (
    <div class="wizard-dots">
      {S.wizardSteps.map((label, i) => (
        <span class={`wizard-dot ${i === step ? "active" : ""} ${i < step ? "done" : ""}`} key={label}>
          {label}
        </span>
      ))}
    </div>
  );
}

function AckStep({ next }: { next: () => void }) {
  return (
    <>
      <p>{S.wizardWelcome}</p>
      <h3>{S.wizardAckTitle}</h3>
      <ul class="ack-list">
        {S.wizardAckPoints.map((p) => <li key={p}>{p}</li>)}
      </ul>
      <button
        class="btn btn-primary"
        onClick={() => {
          localStorage.setItem(ACK_KEY, "1");
          next();
        }}
      >
        {S.wizardAckButton}
      </button>
    </>
  );
}

function EnvStep({ next }: { next: () => void }) {
  const [checks, setChecks] = useState<DoctorCheck[] | null>(null);
  const load = () => {
    setChecks(null);
    api.doctor().then((r) => setChecks(r.checks), () => setChecks([]));
  };
  useEffect(load, []);
  const blocking = (checks ?? []).filter((c) => c.required && !c.ok);
  return (
    <>
      <h3>{S.wizardEnvTitle}</h3>
      {checks === null ? (
        <p class="muted">{S.loading}</p>
      ) : (
        <ul class="check-list">
          {checks.map((c) => (
            <li key={c.key} class={c.ok ? "ok" : c.required ? "bad" : "info"}>
              <span class="check-mark">{c.ok ? "✓" : c.required ? "✗" : "•"}</span>
              <span>
                {c.label}
                {c.detail ? <span class="muted small">（{c.detail}）</span> : null}
                {!c.ok && c.fix ? <div class="muted small fix">{c.fix}</div> : null}
              </span>
            </li>
          ))}
        </ul>
      )}
      <p class={blocking.length ? "warn-text" : "ok-text"}>
        {checks === null ? "" : blocking.length ? S.wizardEnvProblem : S.wizardEnvAllGood}
      </p>
      <div class="wizard-actions">
        <button class="btn" onClick={load}>{S.wizardEnvRefresh}</button>
        <button class="btn btn-primary" disabled={checks === null || blocking.length > 0} onClick={next}>
          →
        </button>
      </div>
    </>
  );
}

function LoginStep({ next }: { next: () => void }) {
  useEffect(() => {
    void loadSession();
  }, []);
  const valid = sessionHealth.value?.state === "valid";
  return (
    <>
      <h3>{S.wizardLoginTitle}</h3>
      <SessionCenter embedded />
      {valid ? <p class="ok-text">✓ {S.wizardLoginDone}</p> : null}
      <div class="wizard-actions">
        <button class="btn btn-primary" disabled={!valid} onClick={next}>→</button>
      </div>
    </>
  );
}

function FetchStep() {
  const s = status.value;
  const running = s?.running ?? false;
  const hasRun = (s?.last_run ?? null) !== null;
  return (
    <>
      <h3>{S.wizardFetchTitle}</h3>
      <p class="muted">{S.wizardFetchIntro}</p>
      {running ? (
        <>
          <ProgressBar />
          <p class="muted small">{S.wizardFetchRunning}</p>
        </>
      ) : (
        <button class="btn btn-primary" onClick={() => void startFetch()}>
          {S.wizardFetchStart}
        </button>
      )}
      <div class="wizard-actions">
        <a class="btn" href="#/">{hasRun || running ? S.wizardDone : S.wizardSkip}</a>
      </div>
    </>
  );
}

export function Wizard() {
  const [step, setStep] = useState(wizardAcknowledged() ? 1 : 0);
  return (
    <main class="wizard">
      <section class="panel">
        <h2>{S.wizardTitle}</h2>
        <StepDots step={step} />
        {step === 0 ? <AckStep next={() => setStep(1)} /> : null}
        {step === 1 ? <EnvStep next={() => setStep(2)} /> : null}
        {step === 2 ? <LoginStep next={() => setStep(3)} /> : null}
        {step === 3 ? <FetchStep /> : null}
      </section>
      <footer class="muted small">{S.footer}</footer>
    </main>
  );
}
