import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import demo from "../../public/data/demo.json";
import results from "../../public/data/results.json";
import { App } from "../App";
import { AppProvider } from "../context/AppContext";

const json = (body: unknown, ok = true) =>
  Promise.resolve({ ok, status: ok ? 200 : 500, statusText: ok ? "OK" : "err", json: () => Promise.resolve(body) } as Response);

function mockFetch(apiUp: boolean) {
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn((url: string) => {
    calls.push(String(url));
    if (String(url).endsWith("data/demo.json")) return json(demo);
    if (String(url).endsWith("data/results.json")) return json(results);
    if (String(url).endsWith("health") && apiUp) return json({ status: "ok", db: true, provider: "mock" });
    return Promise.reject(new TypeError("network down"));
  }));
  return calls;
}

function renderApp(path: string, mode?: "auto" | "demo" | "live") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AppProvider forceMode={mode}>
        <App />
      </AppProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  window.matchMedia = window.matchMedia || ((() => ({ matches: false })) as unknown as typeof window.matchMedia);
  window.HTMLElement.prototype.scrollIntoView = () => {};
  window.scrollTo = () => {};
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("recorded demo", () => {
  it("falls back to the demo with a banner when no API answers", async () => {
    mockFetch(false);
    renderApp("/console");
    expect(await screen.findAllByText(/Recorded demo, not live/)).not.toHaveLength(0);
    expect(await screen.findByText("Allowed")).toBeTruthy();
    expect(screen.getByText(/No API answered at this address/)).toBeTruthy();
    // The recorded form cannot run anything.
    expect((screen.getByRole("button", { name: /cannot run/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows a recorded refusal with its rule and reason", async () => {
    mockFetch(false);
    renderApp("/console", "demo");
    fireEvent.click(await screen.findByRole("button", { name: /Ask to remove all orders/ }));
    expect(await screen.findByText(/Blocked at Input guard/)).toBeTruthy();
    expect(screen.getAllByText("input.destructive_intent").length).toBeGreaterThan(0);
  });

  it("builds data URLs from the base and never asks for the API in demo-only mode", async () => {
    const calls = mockFetch(true);
    renderApp("/safety", "demo");
    expect(await screen.findByText(/The attack block rate was not measured/)).toBeTruthy();
    expect(calls.some((c) => c.endsWith("health"))).toBe(false);
    expect(calls.every((c) => c.startsWith(import.meta.env.BASE_URL))).toBe(true);
  });
});

describe("live mode", () => {
  it("uses the API when health answers", async () => {
    mockFetch(true);
    renderApp("/console");
    await waitFor(() => expect(screen.getByText(/Live, mock provider, database up/)).toBeTruthy());
    expect(screen.queryByText(/Recorded demo, not live/)).toBeNull();
    expect(screen.getByRole("button", { name: /Run through the guards/ })).toBeTruthy();
  });
});
