import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import demo from "../../public/data/demo.json";
import type { DemoData, QueryRequest, QueryResponse } from "../lib/types";
import {
  ApiError, classify, consoleReducer, initialConsole, providerFailure, secondsLeft, type ConsoleState,
} from "../lib/consoleMachine";
import { FailPanel } from "../components/FailPanel";
import { suggestQuestions, refusalProbes } from "../lib/suggest";
import { applyPrefs, parseDensity, parseTheme } from "../lib/prefs";

const cases = (demo as unknown as DemoData).cases;
const byId = (id: string) => cases.find((c) => c.id === id)!;
const policies = (demo as unknown as DemoData).policies;
const req: QueryRequest = { role: "analyst", question: "total revenue by region" };
const T0 = 1_000_000;

const providerErr = (code: string, extra: Record<string, unknown> = {}): QueryResponse => ({
  ...byId("revenue").response,
  decision: "error", blocked_by: "internal", rule_id: `llm.${code}`, reason: `provider said ${code}`,
  rows: [], columns: [], row_count: 0,
  llm_error: { code, message: `provider said ${code}`, ...extra },
});

describe("console state machine", () => {
  it("goes idle -> running -> done(allowed) for a normal answer", () => {
    let s: ConsoleState = consoleReducer(initialConsole, { type: "run", req, now: T0 });
    expect(s.phase).toBe("running");
    s = consoleReducer(s, { type: "resolved", resp: byId("revenue").response, req, recorded: false, now: T0 + 10 });
    expect(s).toMatchObject({ phase: "done", kind: "allowed", recorded: false });
  });

  it("ignores a second run while one is in flight", () => {
    const a = consoleReducer(initialConsole, { type: "run", req, now: T0 });
    const b = consoleReducer(a, { type: "run", req: { role: "admin", question: "other" }, now: T0 + 5 });
    expect(b).toBe(a);
  });

  it("tells allowed, empty, blocked and database error apart", () => {
    expect(classify(byId("revenue").response)).toBe("allowed");
    expect(classify({ ...byId("revenue").response, rows: [], row_count: 0 })).toBe("empty");
    expect(classify(byId("delete").response)).toBe("blocked");
    expect(classify({ ...byId("revenue").response, decision: "error", rule_id: "sandbox.timeout" })).toBe("db_error");
  });

  it("treats a guardrail refusal as a result, not a failure", () => {
    const s = consoleReducer(initialConsole, { type: "resolved", resp: byId("delete").response, req, recorded: true, now: T0 });
    expect(s).toMatchObject({ phase: "done", kind: "blocked" });
  });

  it("maps a rate_limited provider error to a failure with a retry deadline from retry_after_s", () => {
    const s = consoleReducer(initialConsole, {
      type: "resolved", resp: providerErr("rate_limited", { retry_after_s: 12.4 }), req, recorded: false, now: T0,
    });
    expect(s).toMatchObject({ phase: "failed", kind: "rate_limited", retryAt: T0 + 12_400 });
  });

  it("falls back to 30 seconds when the provider gives no retry_after_s", () => {
    const pf = providerFailure(providerErr("rate_limited"), T0)!;
    expect(pf.retryAt).toBe(T0 + 30_000);
  });

  it.each([["bad_key"], ["bad_model"], ["network"], ["server_error"]])("maps %s to its own failure kind without a countdown", (code) => {
    const s = consoleReducer(initialConsole, { type: "resolved", resp: providerErr(code), req, recorded: false, now: T0 });
    expect(s).toMatchObject({ phase: "failed", kind: code, retryAt: null });
  });

  it("maps an unknown provider code to a generic provider error", () => {
    expect(providerFailure(providerErr("weird"), T0)?.kind).toBe("provider_error");
  });

  it("maps a thrown network error, an HTTP 429 and an HTTP 422 differently", () => {
    expect(consoleReducer(initialConsole, { type: "rejected", error: new TypeError("Failed to fetch"), req, now: T0 }))
      .toMatchObject({ phase: "failed", kind: "network" });
    expect(consoleReducer(initialConsole, { type: "rejected", error: new ApiError("slow down", 429, 7), req, now: T0 }))
      .toMatchObject({ phase: "failed", kind: "rate_limited", retryAt: T0 + 7000 });
    expect(consoleReducer(initialConsole, { type: "rejected", error: new ApiError("bad body", 422), req, now: T0 }))
      .toMatchObject({ phase: "failed", kind: "request_failed", retryAt: null });
  });

  it("can start a new run after a failure and resets to idle", () => {
    const failed = consoleReducer(initialConsole, { type: "rejected", error: new Error("x"), req, now: T0 });
    expect(consoleReducer(failed, { type: "run", req, now: T0 + 1 }).phase).toBe("running");
    expect(consoleReducer(failed, { type: "reset" })).toEqual(initialConsole);
  });
});

describe("countdown", () => {
  it("rounds up to whole seconds and never goes negative", () => {
    expect(secondsLeft(T0 + 12_400, T0)).toBe(13);
    expect(secondsLeft(T0 + 1000, T0)).toBe(1);
    expect(secondsLeft(T0 + 1000, T0 + 5000)).toBe(0);
    expect(secondsLeft(null, T0)).toBe(0);
  });
});

describe("FailPanel countdown", () => {
  beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(T0); });
  afterEach(() => { cleanup(); vi.useRealTimers(); });

  const failed = (retryAt: number | null) =>
    ({ phase: "failed", kind: "rate_limited", message: "provider said rate_limited", req, retryAt }) as Extract<ConsoleState, { phase: "failed" }>;

  it("locks the button with a live countdown, ticks down, then unlocks", () => {
    const onRetry = vi.fn();
    render(<FailPanel f={failed(T0 + 3000)} onRetry={onRetry} canSql />);
    expect(screen.getByText(/rate limiting/)).toBeTruthy();
    const btn = () => screen.getByRole("button", { name: /Run again/ }) as HTMLButtonElement;
    expect(btn().disabled).toBe(true);
    expect(screen.getByRole("timer").textContent).toBe("3s");
    act(() => { vi.advanceTimersByTime(1100); });
    expect(screen.getByRole("timer").textContent).toBe("2s");
    act(() => { vi.advanceTimersByTime(2500); });
    expect(btn().disabled).toBe(false);
    expect(btn().textContent).toBe("Run again");
    btn().click();
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("offers an immediate retry and no timer for failures without a deadline", () => {
    const f = { ...failed(null), kind: "bad_key" } as Extract<ConsoleState, { phase: "failed" }>;
    render(<FailPanel f={f} onRetry={() => {}} canSql={false} />);
    expect(screen.getByText(/rejected the API key/)).toBeTruthy();
    expect(screen.queryByRole("timer")).toBeNull();
    expect((screen.getByRole("button", { name: "Run again" }) as HTMLButtonElement).disabled).toBe(false);
  });
});

describe("suggested questions", () => {
  it("offers only questions the role can answer from visible columns", () => {
    const analyst = suggestQuestions(policies.analyst).map((s) => s.question);
    expect(analyst).toContain("total revenue by region");
    expect(analyst).not.toContain("list customers with contact details");
    expect(analyst).not.toContain("average salary by department");
  });

  it("offers the contact-details question to a manager and warns that it comes back masked", () => {
    const m = suggestQuestions(policies.manager, 10).find((s) => s.question === "list customers with contact details");
    expect(m).toBeTruthy();
    expect(m!.hint).toMatch(/masked/);
  });

  it("builds refusal probes that depend on what is hidden", () => {
    const qs = refusalProbes(policies.analyst).map((s) => s.question);
    expect(qs).toContain("delete all orders");
    expect(qs.some((q) => /password/.test(q))).toBe(true);
  });
});

describe("theme preferences", () => {
  it("falls back to system and comfortable for junk, and applies the attributes", () => {
    expect(parseTheme("purple")).toBe("system");
    expect(parseTheme("dark")).toBe("dark");
    expect(parseDensity(null)).toBe("comfortable");
    const el = document.createElement("html");
    applyPrefs("dark", "compact", el);
    expect(el.getAttribute("data-theme")).toBe("dark");
    expect(el.getAttribute("data-density")).toBe("compact");
    applyPrefs("system", "comfortable", el);
    expect(el.hasAttribute("data-theme")).toBe(false);
    expect(el.hasAttribute("data-density")).toBe(false);
  });
});
