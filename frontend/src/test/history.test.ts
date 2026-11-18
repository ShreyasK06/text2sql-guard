import { beforeEach, describe, expect, it } from "vitest";
import demo from "../../public/data/demo.json";
import type { DemoData, QueryResponse } from "../lib/types";
import {
  HISTORY_LIMIT, addEntry, entryFromRun, filterEntries, historyStore, isSaved, parseHistory, parseSaved, recordRun,
  removeEntry, requestOf, toggleSaved, type HistoryEntry,
} from "../lib/history";

const cases = (demo as unknown as DemoData).cases;
const byId = (id: string) => cases.find((c) => c.id === id)!;

const mk = (text: string, over: Partial<HistoryEntry> = {}): HistoryEntry => ({
  id: `id-${text}`, ts: 1, role: "analyst", kind: "question", text, sql: null, decision: "allow", rule_id: null, ...over,
});

beforeEach(() => { window.localStorage.clear(); historyStore.set([]); });

describe("history list", () => {
  it("puts the newest first and moves an identical repeat to the top instead of duplicating it", () => {
    let l: HistoryEntry[] = [];
    l = addEntry(l, mk("a"));
    l = addEntry(l, mk("b"));
    l = addEntry(l, mk("a", { id: "again" }));
    expect(l.map((e) => e.text)).toEqual(["a", "b"]);
    expect(l[0].id).toBe("again");
  });

  it("keeps the same text under different roles apart", () => {
    let l: HistoryEntry[] = [];
    l = addEntry(l, mk("a"));
    l = addEntry(l, mk("a", { id: "m", role: "manager", region: "west" }));
    expect(l).toHaveLength(2);
  });

  it(`caps the list at ${HISTORY_LIMIT} entries, dropping the oldest`, () => {
    let l: HistoryEntry[] = [];
    for (let i = 0; i < HISTORY_LIMIT + 25; i++) l = addEntry(l, mk(`q${i}`));
    expect(l).toHaveLength(HISTORY_LIMIT);
    expect(l[0].text).toBe(`q${HISTORY_LIMIT + 24}`);
    expect(l.some((e) => e.text === "q0")).toBe(false);
  });

  it("deletes one entry by id", () => {
    const l = [mk("a"), mk("b")];
    expect(removeEntry(l, "id-a").map((e) => e.text)).toEqual(["b"]);
  });

  it("filters by role and by text or SQL, case-insensitively", () => {
    const l = [
      mk("total revenue", { sql: "SELECT region FROM orders" }),
      mk("tickets", { role: "admin" }),
    ];
    expect(filterEntries(l, { role: "admin" }).map((e) => e.text)).toEqual(["tickets"]);
    expect(filterEntries(l, { q: "REVENUE" })).toHaveLength(1);
    expect(filterEntries(l, { q: "from orders" })).toHaveLength(1);
    expect(filterEntries(l, { q: "nothing" })).toHaveLength(0);
  });
});

describe("what is stored", () => {
  it("never stores result rows, masked or otherwise", () => {
    const c = byId("masked");
    expect(JSON.stringify(c.response.rows)).toContain("***");
    recordRun(c.request, c.response, true);
    const raw = window.localStorage.getItem("t2sg.history.v1")!;
    expect(raw).not.toContain("***");
    expect(raw).not.toContain("Orozcoshire");
    expect(raw).not.toContain("\"rows\"");
    const e = JSON.parse(raw)[0];
    expect(Object.keys(e).sort()).toEqual(["decision", "id", "kind", "recorded", "region", "role", "rule_id", "sql", "text", "ts"]);
    expect(e.text).toBe("list customers with contact details");
    expect(e.decision).toBe("allow");
  });

  it("records a refusal with its rule id", () => {
    const c = byId("delete");
    const e = entryFromRun(c.request, c.response, false, 5);
    expect(e.decision).toBe("block");
    expect(e.rule_id).toBe("input.destructive_intent");
    expect(e.recorded).toBeUndefined();
  });

  it("records direct SQL as kind sql and drops the region for non-managers", () => {
    const c = byId("passwords");
    const e = entryFromRun({ ...c.request, region: "west" }, c.response, false);
    expect(e.kind).toBe("sql");
    expect(e.text).toMatch(/password_hash/);
    expect(e.region).toBeUndefined();
  });

  it("persists across a reload and survives corrupt storage", () => {
    recordRun({ role: "analyst", question: "q1" }, { decision: "allow", sql_generated: "SELECT 1", rule_id: null } as QueryResponse, false);
    expect(JSON.parse(window.localStorage.getItem("t2sg.history.v1")!)).toHaveLength(1);
    expect(parseHistory(JSON.parse(window.localStorage.getItem("t2sg.history.v1")!))[0].text).toBe("q1");
    expect(parseHistory("not an array")).toEqual([]);
    expect(parseHistory([null, 3, { id: 1 }, { id: "x", role: "root", text: "t" }, { id: "ok", role: "admin", text: "t" }])).toHaveLength(1);
  });
});

describe("saved queries", () => {
  it("toggles on and off and treats the same query as the same entry", () => {
    const d = { role: "analyst" as const, kind: "question" as const, text: "total revenue by region" };
    let s = toggleSaved([], d, 1);
    expect(isSaved(s, d)).toBe(true);
    s = toggleSaved(s, { ...d, text: "  total revenue by region " });
    expect(s).toHaveLength(0);
  });

  it("rebuilds the request, forcing a region for managers", () => {
    expect(requestOf({ role: "manager", kind: "question", text: "x" })).toEqual({ role: "manager", region: "west", question: "x" });
    expect(requestOf({ role: "admin", kind: "sql", text: "SELECT 1" })).toEqual({ role: "admin", sql: "SELECT 1" });
    expect(parseSaved([{ id: "s", role: "admin", text: "t", kind: "sql", ts: 2 }])[0].kind).toBe("sql");
  });
});
