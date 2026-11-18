import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import demo from "../../public/data/demo.json";
import results from "../../public/data/results.json";
import { App } from "../App";
import { AppProvider } from "../context/AppContext";
import { historyStore, savedStore } from "../lib/history";

const json = (body: unknown, ok = true, status = 200) =>
  Promise.resolve({ ok, status, statusText: ok ? "OK" : "err", json: () => Promise.resolve(body) } as Response);

function mockFetch() {
  vi.stubGlobal("fetch", vi.fn((url: string) => {
    const u = String(url);
    if (u.endsWith("data/demo.json")) return json(demo);
    if (u.endsWith("data/results.json")) return json(results);
    return Promise.reject(new TypeError("network down"));
  }));
}

function renderApp(path: string, mode?: "auto" | "demo" | "live") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AppProvider forceMode={mode}><App /></AppProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
  historyStore.set([]);
  savedStore.set([]);
  window.matchMedia = window.matchMedia || ((() => ({ matches: false })) as unknown as typeof window.matchMedia);
  window.HTMLElement.prototype.scrollIntoView = () => {};
  window.scrollTo = () => {};
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("demo mode keeps history, saving and export working", () => {
  it("records a picked recorded request, offers CSV and JSON export, and lists it on the History page", async () => {
    mockFetch();
    renderApp("/console", "demo");
    fireEvent.click(await screen.findByRole("button", { name: /Customer contact details.*manager, west/ }));
    expect(await screen.findByRole("button", { name: /CSV/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /JSON/ })).toBeTruthy();
    expect(screen.getAllByText(/masked/).length).toBeGreaterThan(0);
    await waitFor(() => expect(historyStore.get().some((e) => e.text === "list customers with contact details" && e.recorded)).toBe(true));
    // The stored entry never contains rows.
    expect(JSON.stringify(historyStore.get())).not.toMatch(/\*\*\*/);
  });

  it("explains a block with its rule id, and can save the query", async () => {
    mockFetch();
    renderApp("/console", "demo");
    fireEvent.click(await screen.findByRole("button", { name: /Ask to remove all orders/ }));
    const panel = (await screen.findByText("Explain this block")).closest("details")!;
    expect(within(panel).getByText("input.destructive_intent")).toBeTruthy();
    expect(within(panel).getByText(/What to try/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Save query/ }));
    expect(savedStore.get()).toHaveLength(1);
    expect(screen.getByRole("button", { name: /Saved/ }).getAttribute("aria-pressed")).toBe("true");
  });
});

describe("live mode", () => {
  const live: Record<string, () => Promise<Response>> = {
    health: () => json({ status: "ok", db: true, provider: "nvidia" }),
    "llm/status": () => json({ provider: "nvidia", model: "acme/coder", configured: true, mode: "live", privacy_note: "note" }),
    catalog: () => json(demo.catalog),
    "policy/analyst": () => json(demo.policies.analyst),
  };
  const stubLive = (queryBody: unknown) =>
    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
      const u = String(url);
      if (init?.method === "POST") return json(queryBody);
      for (const [k, h] of Object.entries(live)) if (u.endsWith(k)) return h();
      return Promise.reject(new TypeError("nope"));
    }));

  it("shows a rate-limit panel with a countdown instead of a result, and does not record it", async () => {
    stubLive({
      ...demo.cases[0].response, decision: "error", blocked_by: "internal", rule_id: "llm.rate_limited",
      reason: "slow down", rows: [], columns: [], row_count: 0,
      llm_error: { code: "rate_limited", message: "slow down", retry_after_s: 9 },
    });
    renderApp("/console", "live");
    fireEvent.change(await screen.findByLabelText("Question"), { target: { value: "total revenue by region" } });
    fireEvent.click(screen.getByRole("button", { name: /Run through the guards/ }));
    expect(await screen.findByText(/rate limiting requests/)).toBeTruthy();
    const timer = screen.getByRole("timer");
    expect(Number(timer.textContent!.replace("s", ""))).toBeGreaterThan(5);
    expect((screen.getByRole("button", { name: /Run again in/ }) as HTMLButtonElement).disabled).toBe(true);
    expect(historyStore.get()).toHaveLength(0);
  });

  it("runs on Ctrl+Enter and shows the allowed verdict", async () => {
    stubLive(demo.cases[0].response);
    renderApp("/console", "live");
    const q = await screen.findByLabelText("Question");
    fireEvent.change(q, { target: { value: "total revenue by region" } });
    fireEvent.keyDown(q, { key: "Enter", ctrlKey: true });
    expect(await screen.findByRole("heading", { name: "Allowed" })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: /Copy SQL/ }).length).toBeGreaterThan(0);
    expect(historyStore.get()[0].text).toBe("total revenue by region");
  });
});

describe("history page", () => {
  it("lists entries, filters by search, deletes one and clears all after confirmation", async () => {
    historyStore.set([
      { id: "1", ts: 2, role: "analyst", kind: "question", text: "total revenue by region", sql: "SELECT 1", decision: "allow", rule_id: null },
      { id: "2", ts: 1, role: "admin", kind: "question", text: "delete all orders", sql: null, decision: "block", rule_id: "input.destructive_intent" },
    ]);
    mockFetch();
    renderApp("/history", "demo");
    expect(await screen.findByText("total revenue by region")).toBeTruthy();
    expect(screen.getByText(/never stores result rows/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Search"), { target: { value: "delete" } });
    expect(screen.queryByText("total revenue by region")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Delete\s*: delete all orders/ }));
    expect(historyStore.get()).toHaveLength(1);
    fireEvent.change(screen.getByLabelText("Search"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: /Clear all history/ }));
    fireEvent.click(screen.getByRole("button", { name: /Yes, clear all/ }));
    expect(historyStore.get()).toHaveLength(0);
    expect(await screen.findByText("No history yet")).toBeTruthy();
  });
});

describe("audit filters live in the URL", () => {
  it("reads the filter from the query string", async () => {
    mockFetch();
    renderApp("/audit?decision=block", "demo");
    const sel = (await screen.findByLabelText("Decision")) as HTMLSelectElement;
    expect(sel.value).toBe("block");
    expect(screen.getAllByRole("row").length).toBeGreaterThan(1);
  });
});
