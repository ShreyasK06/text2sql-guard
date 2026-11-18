import { createStore, useStore } from "./store";

export type Theme = "system" | "light" | "dark";
export type Density = "comfortable" | "compact";

export const THEME_KEY = "t2sg.theme.v1";
export const DENSITY_KEY = "t2sg.density.v1";

export const parseTheme = (raw: unknown): Theme => (raw === "light" || raw === "dark" ? raw : "system");
export const parseDensity = (raw: unknown): Density => (raw === "compact" ? "compact" : "comfortable");

export const themeStore = createStore<Theme>(THEME_KEY, parseTheme);
export const densityStore = createStore<Density>(DENSITY_KEY, parseDensity);
export const useTheme = () => useStore(themeStore);
export const useDensity = () => useStore(densityStore);

/** "system" removes the attribute so the prefers-color-scheme media query decides. */
export function applyPrefs(theme: Theme, density: Density, root: HTMLElement = document.documentElement): void {
  if (theme === "system") root.removeAttribute("data-theme"); else root.setAttribute("data-theme", theme);
  if (density === "compact") root.setAttribute("data-density", "compact"); else root.removeAttribute("data-density");
}

/** Apply stored preferences and keep the document in sync. Call once at startup. */
export function initPrefs(): void {
  const sync = () => applyPrefs(themeStore.get(), densityStore.get());
  sync();
  themeStore.subscribe(sync);
  densityStore.subscribe(sync);
}

/** The theme a "system" choice currently resolves to, for labels. */
export function effectiveTheme(theme: Theme, prefersDark: boolean): "light" | "dark" {
  return theme === "system" ? (prefersDark ? "dark" : "light") : theme;
}
