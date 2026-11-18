import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { liveApi, staticApi } from "../lib/api";
import type {
  AuditEntry, CatalogResponse, DemoData, HealthResponse, PolicyResponse, QueryRequest, QueryResponse,
} from "../lib/types";

export type Mode = "loading" | "live" | "demo" | "offline";

export interface Backend {
  catalog: () => Promise<CatalogResponse>;
  policy: (role: string) => Promise<PolicyResponse>;
  audit: () => Promise<AuditEntry[]>;
  query: (body: QueryRequest) => Promise<QueryResponse>;
}

export interface AppState {
  mode: Mode;
  health: HealthResponse | null;
  demo: DemoData | null;
  /** Why the app is in demo mode when it was not asked for. */
  reason: string;
  error: string;
  backend: Backend;
  switchToDemo: () => void;
}

const demoBackend = (d: DemoData): Backend => ({
  catalog: () => Promise.resolve(d.catalog),
  policy: (role) => Promise.resolve(d.policies[role]),
  audit: () => Promise.resolve(d.audit),
  query: () => Promise.reject(new Error("This is a recorded demo: nothing is executed.")),
});

const Ctx = createContext<AppState | null>(null);

export function useApp(): AppState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useApp must be used inside <AppProvider>");
  return v;
}

/** `?demo=1` (before or inside the hash) forces the recorded demo. */
export function demoRequested(search: string, hash: string): boolean {
  const q = hash.includes("?") ? hash.slice(hash.indexOf("?")) : "";
  return new URLSearchParams(search).has("demo") || new URLSearchParams(q).has("demo");
}

export function AppProvider({ children, forceMode }: { children: ReactNode; forceMode?: "auto" | "demo" | "live" }) {
  const configured = forceMode ?? import.meta.env.VITE_MODE ?? "auto";
  const [mode, setMode] = useState<Mode>("loading");
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [demo, setDemo] = useState<DemoData | null>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");

  const startDemo = useCallback(async (why: string) => {
    try {
      setDemo(await staticApi.demo());
      setReason(why);
      setMode("demo");
    } catch (e) {
      setError(`Could not reach the API or load the recorded demo (${(e as Error).message}).`);
      setMode("offline");
    }
  }, []);

  useEffect(() => {
    let alive = true;
    (async () => {
      const forced = configured === "demo" || demoRequested(window.location.search, window.location.hash);
      if (forced) { if (alive) await startDemo(""); return; }
      try {
        const h = await liveApi.health();
        if (!alive) return;
        setHealth(h);
        setMode("live");
      } catch {
        if (!alive) return;
        if (configured === "live") {
          setError("The API did not answer, and this build is configured for live mode only.");
          setMode("offline");
        } else await startDemo("No API answered at this address.");
      }
    })();
    return () => { alive = false; };
  }, [configured, startDemo]);

  const backend = useMemo<Backend>(() => (demo && mode === "demo" ? demoBackend(demo) : liveApi), [demo, mode]);
  const switchToDemo = useCallback(() => { void startDemo("Switched to the recorded demo."); }, [startDemo]);

  const value = useMemo<AppState>(
    () => ({ mode, health, demo, reason, error, backend, switchToDemo }),
    [mode, health, demo, reason, error, backend, switchToDemo],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
