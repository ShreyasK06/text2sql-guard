/** All runtime URLs are built from the Vite base, never from a hardcoded absolute path. */
export const BASE: string = import.meta.env.BASE_URL;

/** Static file shipped in public/ (e.g. "data/demo.json"). */
export const assetUrl = (path: string, base: string = BASE): string => `${base}${path.replace(/^\/+/, "")}`;

/** API endpoint. VITE_API_BASE wins; otherwise same origin, relative to the app base. */
export function apiUrl(
  path: string,
  apiBase: string | undefined = import.meta.env.VITE_API_BASE,
  base: string = BASE,
): string {
  const clean = path.replace(/^\/+/, "");
  if (apiBase) return `${apiBase.replace(/\/+$/, "")}/${clean}`;
  return `${base}${clean}`;
}
