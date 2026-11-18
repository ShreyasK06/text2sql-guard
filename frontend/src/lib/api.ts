import { apiUrl, assetUrl } from "./url";
import type {
  AuditEntry, CatalogResponse, DemoData, HealthResponse, PolicyResponse, QueryRequest, QueryResponse, Results,
} from "./types";

export async function getJSON<T>(url: string, init?: RequestInit & { timeout?: number }): Promise<T> {
  const { timeout = 20000, ...rest } = init ?? {};
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeout);
  try {
    const r = await fetch(url, { ...rest, signal: ctl.signal });
    if (!r.ok) {
      let detail = "";
      try {
        const j = await r.json();
        detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
      } catch { /* body was not JSON */ }
      throw new Error(detail || `${r.status} ${r.statusText}`);
    }
    return (await r.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

/** Typed client for the FastAPI service (app/api/main.py). */
export const liveApi = {
  health: () => getJSON<HealthResponse>(apiUrl("health"), { timeout: 3000 }),
  catalog: () => getJSON<CatalogResponse>(apiUrl("catalog")),
  policy: (role: string) => getJSON<PolicyResponse>(apiUrl(`policy/${encodeURIComponent(role)}`)),
  audit: () => getJSON<AuditEntry[]>(apiUrl("audit?n=500")),
  query: (body: QueryRequest) =>
    getJSON<QueryResponse>(apiUrl("query"), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }),
};

/** Static files shipped with the site. */
export const staticApi = {
  demo: () => getJSON<DemoData>(assetUrl("data/demo.json")),
  results: () => getJSON<Results>(assetUrl("data/results.json")),
};
