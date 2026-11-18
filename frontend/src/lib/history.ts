import type { QueryRequest, QueryResponse, Region, Role } from "./types";
import { createStore, useStore } from "./store";

/**
 * Query history and saved queries live in this browser's localStorage only.
 * Only what the user typed (question or SQL), the role/region, the SQL the service produced and the decision
 * are stored. Result rows are never stored.
 */
export const HISTORY_LIMIT = 100;
const SQL_CAP = 4000;
const TEXT_CAP = 2000;

export interface HistoryEntry {
  id: string;
  ts: number;
  role: Role;
  region?: Region;
  kind: "question" | "sql";
  /** The question, or the SQL the user supplied. */
  text: string;
  /** SQL the service generated (or ran), for display. */
  sql: string | null;
  decision: string;
  rule_id: string | null;
  /** True when the entry came from the recorded demo rather than a live run. */
  recorded?: boolean;
}

export interface SavedEntry {
  id: string;
  ts: number;
  role: Role;
  region?: Region;
  kind: "question" | "sql";
  text: string;
}

const ROLES = ["analyst", "manager", "admin"];
const REGIONS = ["north", "south", "east", "west"];

const str = (v: unknown, cap: number): string => (typeof v === "string" ? v.slice(0, cap) : "");

function cleanBase(o: Record<string, unknown>) {
  if (typeof o.id !== "string" || !ROLES.includes(o.role as string)) return null;
  const text = str(o.text, TEXT_CAP);
  if (!text) return null;
  return {
    id: o.id.slice(0, 64),
    ts: typeof o.ts === "number" && Number.isFinite(o.ts) ? o.ts : 0,
    role: o.role as Role,
    region: REGIONS.includes(o.region as string) ? (o.region as Region) : undefined,
    kind: o.kind === "sql" ? ("sql" as const) : ("question" as const),
    text,
  };
}

export function parseHistory(raw: unknown): HistoryEntry[] {
  if (!Array.isArray(raw)) return [];
  const out: HistoryEntry[] = [];
  for (const x of raw) {
    if (!x || typeof x !== "object") continue;
    const o = x as Record<string, unknown>;
    const b = cleanBase(o);
    if (!b) continue;
    out.push({
      ...b,
      sql: typeof o.sql === "string" ? o.sql.slice(0, SQL_CAP) : null,
      decision: str(o.decision, 16) || "error",
      rule_id: typeof o.rule_id === "string" ? o.rule_id.slice(0, 80) : null,
      recorded: o.recorded === true ? true : undefined,
    });
  }
  return out.slice(0, HISTORY_LIMIT);
}

export function parseSaved(raw: unknown): SavedEntry[] {
  if (!Array.isArray(raw)) return [];
  const out: SavedEntry[] = [];
  for (const x of raw) {
    if (!x || typeof x !== "object") continue;
    const b = cleanBase(x as Record<string, unknown>);
    if (b) out.push(b);
  }
  return out;
}

const newId = (): string =>
  globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;

export function entryFromRun(
  req: QueryRequest, resp: QueryResponse, recorded: boolean, now: number = Date.now(),
): HistoryEntry {
  const isSql = !!(req.sql && !req.question);
  return {
    id: newId(),
    ts: now,
    role: req.role,
    region: req.role === "manager" ? req.region : undefined,
    kind: isSql ? "sql" : "question",
    text: (isSql ? req.sql! : req.question ?? "").slice(0, TEXT_CAP),
    sql: (resp.sql_generated || resp.sql_executed || null)?.slice(0, SQL_CAP) ?? null,
    decision: resp.decision,
    rule_id: resp.rule_id,
    recorded: recorded || undefined,
  };
}

interface Keyed { role: string; kind: string; text: string; region?: string }
const sameQuery = (a: Keyed, b: Keyed) =>
  a.role === b.role && a.kind === b.kind && a.text.trim() === b.text.trim() && (a.region ?? "") === (b.region ?? "");

/** Newest first; an identical repeat moves to the top instead of piling up; capped at HISTORY_LIMIT. */
export function addEntry(list: HistoryEntry[], e: HistoryEntry): HistoryEntry[] {
  return [e, ...list.filter((x) => !sameQuery(x, e))].slice(0, HISTORY_LIMIT);
}
export const removeEntry = <T extends { id: string }>(list: T[], id: string): T[] => list.filter((x) => x.id !== id);

export function filterEntries<T extends { role: string; text: string; sql?: string | null }>(
  list: T[], opts: { role?: string; q?: string },
): T[] {
  const q = (opts.q ?? "").trim().toLowerCase();
  return list.filter((e) =>
    (!opts.role || e.role === opts.role) &&
    (!q || e.text.toLowerCase().includes(q) || (e.sql ?? "").toLowerCase().includes(q)));
}

type SavedDraft = Omit<SavedEntry, "id" | "ts">;
export function toggleSaved(list: SavedEntry[], e: SavedDraft, now: number = Date.now()): SavedEntry[] {
  if (list.some((x) => sameQuery(x, e))) return list.filter((x) => !sameQuery(x, e));
  return [{ ...e, id: newId(), ts: now }, ...list];
}
export const isSaved = (list: SavedEntry[], e: SavedDraft): boolean => list.some((x) => sameQuery(x, e));

export const historyStore = createStore<HistoryEntry[]>("t2sg.history.v1", parseHistory);
export const savedStore = createStore<SavedEntry[]>("t2sg.saved.v1", parseSaved);
export const useHistory = () => useStore(historyStore);
export const useSaved = () => useStore(savedStore);

export function recordRun(req: QueryRequest, resp: QueryResponse, recorded: boolean): void {
  if (!(req.sql || req.question)) return;
  historyStore.set((l) => addEntry(l, entryFromRun(req, resp, recorded)));
}

/** The request a history/saved entry stands for. */
export function requestOf(e: { role: Role; region?: Region; kind: string; text: string }): QueryRequest {
  const r: QueryRequest = { role: e.role };
  if (e.role === "manager") r.region = e.region ?? "west";
  if (e.kind === "sql") r.sql = e.text; else r.question = e.text;
  return r;
}
