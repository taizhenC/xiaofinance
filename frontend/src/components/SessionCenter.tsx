/** DC-01 UI: session state at a glance, one-click re-login (QR in a visible
 * browser on this machine), guided diagnosis of the three failure classes
 * with their distinct one-click fixes, and the cookie-paste fallback that
 * replaces hand-editing .env. */

import { useState } from "preact/hooks";
import { api, ApiError } from "../api";
import { loadSession, sessionHealth, status, toast } from "../state";
import { S } from "../strings";
import { Panel } from "./bits";

function StateDot({ state }: { state: string }) {
  return <i class={`dot session-dot session-${state}`} />;
}

function LoginButton() {
  const h = sessionHealth.value;
  const running = h?.login_job.running ?? false;
  const fetchRunning = status.value?.running ?? false;
  const start = async () => {
    try {
      await api.sessionLogin();
      toast("info", S.loginRunning);
      poll();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        toast("error", fetchRunning ? S.loginBusyFetch : String(err.detail));
      } else {
        toast("error", S.requestFailed("发起登录"));
      }
    }
  };
  const poll = () => {
    const timer = setInterval(() => {
      void loadSession().then(() => {
        const job = sessionHealth.value?.login_job;
        if (job && !job.running) {
          clearInterval(timer);
          if (job.outcome?.ok) toast("success", S.loginOk(job.outcome.detail));
          else if (job.outcome) toast("error", S.loginFail(job.outcome.detail));
        }
      });
    }, 2000);
  };
  if (running) {
    return (
      <button class="btn" disabled>
        <span class="spinner" /> {S.loginRunning}
      </button>
    );
  }
  return (
    <button class="btn btn-primary" disabled={fetchRunning} onClick={() => void start()}>
      {S.loginButton}
    </button>
  );
}

function IntlToggle() {
  const h = sessionHealth.value;
  if (!h) return null;
  const flip = async () => {
    try {
      await api.sessionConfig(!h.xhs_international);
      await loadSession();
      toast("success", S.intlCurrent(!h.xhs_international));
    } catch {
      toast("error", S.requestFailed("切换后端"));
    }
  };
  return (
    <div class="session-row">
      <span class="muted small">{S.intlCurrent(h.xhs_international)}</span>
      <button class="btn btn-mini" onClick={() => void flip()}>
        {h.xhs_international ? S.intlToggleOff : S.intlToggleOn}
      </button>
    </div>
  );
}

function CookieForm() {
  const h = sessionHealth.value;
  const [value, setValue] = useState("");
  const save = async (ev: Event) => {
    ev.preventDefault();
    try {
      await api.sessionCookies(value);
      setValue("");
      await loadSession();
      toast("success", S.cookieSaved);
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) toast("error", S.cookieBadFormat);
      else toast("error", S.requestFailed("保存 Cookie"));
    }
  };
  const clear = async () => {
    try {
      await api.sessionCookiesClear();
      await loadSession();
      toast("info", S.cookieCleared);
    } catch {
      toast("error", S.requestFailed("清除 Cookie"));
    }
  };
  return (
    <details class="cookie-form" open={h?.diagnosis === "try_cookie"}>
      <summary>{S.cookiePasteLabel}</summary>
      <p class="muted small">{S.cookiePasteHint}</p>
      {h?.cookie.configured ? (
        <p class="small">
          ✓ {S.cookieConfigured}
          {h.cookie.format_ok === false ? <b class="warn-text"> — {S.cookieBadFormat}</b> : null}{" "}
          <button class="btn btn-mini" onClick={() => void clear()}>{S.cookieClear}</button>
        </p>
      ) : null}
      <form onSubmit={save} class="cookie-input-row">
        <input
          type="password"
          value={value}
          onInput={(e) => setValue((e.target as HTMLInputElement).value)}
          placeholder="a1=…; web_session=…"
          autocomplete="off"
        />
        <button class="btn" type="submit" disabled={!value.trim()}>{S.cookieSave}</button>
      </form>
    </details>
  );
}

export function SessionCenter({ embedded = false }: { embedded?: boolean }) {
  const h = sessionHealth.value;
  if (!h) return null;
  const body = (
    <>
      <div class="session-row">
        <StateDot state={h.state} />
        <b>{S.sessionState[h.state]}</b>
        <span class="badge">{S.sessionSource[h.source]}</span>
        <LoginButton />
      </div>
      {h.diagnosis !== "none" && S.diagnosis[h.diagnosis] ? (
        <p class={`diagnosis diagnosis-${h.diagnosis}`}>{S.diagnosis[h.diagnosis]}</p>
      ) : null}
      {h.diagnosis === "backend_mismatch" || h.xhs_international ? <IntlToggle /> : null}
      <CookieForm />
    </>
  );
  if (embedded) return <div class="session-embedded">{body}</div>;
  return (
    <Panel title={S.sessionTitle} id="session-center">
      {body}
    </Panel>
  );
}
