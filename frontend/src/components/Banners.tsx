import { countdown } from "../format";
import { connectionLost, demoActive, demoOverride, hasRealData, status } from "../state";
import { S } from "../strings";

/** Every systemic condition surfaces here instead of dying in console.error:
 * lost backend, expired login, guardrail cooldown, exhausted budget, missing
 * LLM key, demo mode. */
export function Banners() {
  const s = status.value;
  const out = [];

  if (connectionLost.value) {
    out.push(
      <div class="banner banner-error" key="conn">
        {S.requestFailed("连接服务")}
      </div>,
    );
  }
  if (demoActive.value) {
    out.push(
      <div class="banner banner-demo" key="demo">
        {S.demoBanner}
        {hasRealData.value ? (
          <button class="btn btn-mini" onClick={() => (demoOverride.value = false)}>{S.demoExit}</button>
        ) : null}
      </div>,
    );
  }
  if (s?.login_required) {
    out.push(
      <div class="banner banner-error" key="login">
        {S.loginRequiredBanner}{" "}
        <a href="#session-center">{S.sessionTitle} ↓</a>
      </div>,
    );
  }
  if (s?.guardrails.cooldown_until_ms) {
    out.push(
      <div class="banner banner-warn" key="cooldown">
        {S.cooldownBanner(countdown(s.guardrails.cooldown_until_ms, s.now_ms))}
      </div>,
    );
  }
  if (s?.guardrails.budget.exhausted) {
    out.push(
      <div class="banner banner-warn" key="budget">
        {S.budgetBanner(s.guardrails.budget.used_24h, s.guardrails.budget.limit)}
      </div>,
    );
  }
  if (s && !s.has_api_key && !demoActive.value) {
    out.push(
      <div class="banner banner-info" key="apikey">
        {S.noApiKeyBanner}
      </div>,
    );
  }
  return <>{out}</>;
}
