/** Tiny hash router: #/ home, #/stock/NVDA detail, #/wizard onboarding.
 * Hash-based so FastAPI needs no catch-all route and per-stock URLs are
 * shareable locally. */

import { signal } from "@preact/signals";

export type Route =
  | { name: "home" }
  | { name: "stock"; ticker: string }
  | { name: "wizard" };

function parse(hash: string): Route {
  const h = hash.replace(/^#\/?/, "");
  const stock = /^stock\/([A-Za-z]{1,5})$/.exec(h);
  if (stock) return { name: "stock", ticker: stock[1].toUpperCase() };
  if (h === "wizard") return { name: "wizard" };
  return { name: "home" };
}

export const route = signal<Route>(parse(location.hash));

window.addEventListener("hashchange", () => {
  route.value = parse(location.hash);
  window.scrollTo(0, 0);
});

export function navigate(to: Route): void {
  if (to.name === "stock") location.hash = `#/stock/${to.ticker}`;
  else if (to.name === "wizard") location.hash = "#/wizard";
  else location.hash = "#/";
}
