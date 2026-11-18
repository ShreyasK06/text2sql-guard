import type { QueryRequest, QueryResponse } from "./types";

export type FailKind = "rate_limited" | "bad_key" | "bad_model" | "no_model" | "network" | "server_error" | "provider_error" | "request_failed";
export type DoneKind = "allowed" | "empty" | "blocked" | "db_error";

export type ConsoleState =
  | { phase: "idle" }
  | { phase: "running"; req: QueryRequest; startedAt: number }
  | { phase: "done"; kind: DoneKind; resp: QueryResponse; req: QueryRequest; recorded: boolean }
  | { phase: "failed"; kind: FailKind; message: string; req: QueryRequest; retryAt: number | null };

export type ConsoleAction =
  | { type: "run"; req: QueryRequest; now: number }
  | { type: "resolved"; resp: QueryResponse; req: QueryRequest; recorded: boolean; now: number }
  | { type: "rejected"; error: unknown; req: QueryRequest; now: number }
  | { type: "reset" };

export const initialConsole: ConsoleState = { phase: "idle" };

const PROVIDER_CODES: string[] = ["rate_limited", "bad_key", "bad_model", "no_model", "network", "server_error"];

/** Provider failures arrive as decision "error" with rule_id "llm.<code>" and a structured llm_error. */
export function providerFailure(
  resp: QueryResponse, now: number,
): { kind: FailKind; message: string; retryAt: number | null } | null {
  const e = resp.llm_error;
  const fromRule = resp.rule_id?.startsWith("llm.") ? resp.rule_id.slice(4) : null;
  const code = e?.code ?? fromRule;
  if (!code) return null;
  const kind = (PROVIDER_CODES.includes(code) ? code : "provider_error") as FailKind;
  const wait = typeof e?.retry_after_s === "number" && e.retry_after_s > 0 ? e.retry_after_s : null;
  return { kind, message: e?.message || resp.reason, retryAt: kind === "rate_limited" ? now + (wait ?? 30) * 1000 : null };
}

export function classify(resp: QueryResponse): DoneKind {
  if (resp.decision === "allow") return resp.rows.length === 0 ? "empty" : "allowed";
  if (resp.decision === "block") return "blocked";
  return "db_error";
}

export class ApiError extends Error {
  readonly status: number;
  readonly retryAfterS: number | null;
  constructor(message: string, status: number, retryAfterS: number | null = null) {
    super(message);
    this.status = status;
    this.retryAfterS = retryAfterS;
  }
}

export function consoleReducer(s: ConsoleState, a: ConsoleAction): ConsoleState {
  switch (a.type) {
    case "run":
      return s.phase === "running" ? s : { phase: "running", req: a.req, startedAt: a.now };
    case "resolved": {
      const pf = providerFailure(a.resp, a.now);
      if (pf) return { phase: "failed", kind: pf.kind, message: pf.message, req: a.req, retryAt: pf.retryAt };
      return { phase: "done", kind: classify(a.resp), resp: a.resp, req: a.req, recorded: a.recorded };
    }
    case "rejected": {
      const e = a.error;
      if (e instanceof ApiError) {
        if (e.status === 429) {
          return { phase: "failed", kind: "rate_limited", message: e.message, req: a.req, retryAt: a.now + (e.retryAfterS ?? 30) * 1000 };
        }
        return { phase: "failed", kind: "request_failed", message: e.message, req: a.req, retryAt: null };
      }
      return { phase: "failed", kind: "network", message: e instanceof Error ? e.message : String(e), req: a.req, retryAt: null };
    }
    case "reset":
      return initialConsole;
  }
}

/** Whole seconds left until `retryAt`, never negative. */
export function secondsLeft(retryAt: number | null, now: number): number {
  return retryAt == null ? 0 : Math.max(0, Math.ceil((retryAt - now) / 1000));
}

export const FAIL_COPY: Record<FailKind, { title: string; body: string; fix: string }> = {
  rate_limited: {
    title: "The model provider is rate limiting requests",
    body: "Nothing was run and no data was returned. The guards were never reached.",
    fix: "Wait for the countdown, then run it again. Free hosted models allow only a few requests a minute.",
  },
  bad_key: {
    title: "The provider rejected the API key",
    body: "The key set on the server was refused, so the model could not be asked. The key is never shown here.",
    fix: "Check the key in the server's .env (for example NVIDIA_API_KEY) and restart the API. Settings shows how.",
  },
  bad_model: {
    title: "That model is not available",
    body: "The provider does not know the configured model name.",
    fix: "Pick a name from the live list in Settings, set it in .env (NVIDIA_MODEL) and restart the API.",
  },
  no_model: {
    title: "No model is configured",
    body: "A provider is selected but no model name is set.",
    fix: "Set the model in .env and restart the API. Settings lists what the provider offers.",
  },
  network: {
    title: "Could not reach the service or the model",
    body: "The request did not complete. Nothing was run.",
    fix: "Check that the API is running and your connection is up, then try again.",
  },
  server_error: {
    title: "The model provider had an error",
    body: "The provider answered with a server error. Nothing was run.",
    fix: "This is usually brief. Try again in a moment.",
  },
  provider_error: {
    title: "The model provider returned an error",
    body: "The model could not produce SQL. Nothing was run.",
    fix: "Try again, or supply SQL directly to skip the model.",
  },
  request_failed: {
    title: "The service refused the request",
    body: "The request was rejected before the pipeline ran.",
    fix: "Check the question and role, then try again.",
  },
};
