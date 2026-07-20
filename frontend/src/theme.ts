import { signal } from "@preact/signals";

export type Theme = "dark" | "light";

const KEY = "infinance-theme";

function initial(): Theme {
  const saved = localStorage.getItem(KEY);
  if (saved === "dark" || saved === "light") return saved;
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export const theme = signal<Theme>(initial());

export function applyTheme(t: Theme): void {
  document.documentElement.dataset.theme = t;
}

export function toggleTheme(): void {
  theme.value = theme.value === "dark" ? "light" : "dark";
  localStorage.setItem(KEY, theme.value);
  applyTheme(theme.value);
}

applyTheme(theme.value);
