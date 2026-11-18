import { useSyncExternalStore } from "react";

/** Safe wrappers: localStorage can throw (private windows, blocked site data) or be absent. */
export function readStorage(key: string): string | null {
  try { return window.localStorage.getItem(key); } catch { return null; }
}
export function writeStorage(key: string, value: string | null): void {
  try {
    if (value === null) window.localStorage.removeItem(key); else window.localStorage.setItem(key, value);
  } catch { /* storage unavailable: the app keeps working in memory */ }
}

export interface Store<T> {
  get: () => T;
  set: (next: T | ((prev: T) => T)) => void;
  subscribe: (fn: () => void) => () => void;
}

/** A tiny persisted external store. `parse` must tolerate any stored junk and return a valid value. */
export function createStore<T>(key: string, parse: (raw: unknown) => T, serialize: (v: T) => unknown = (v) => v): Store<T> {
  const load = (): T => {
    const raw = readStorage(key);
    if (raw == null) return parse(undefined);
    try { return parse(JSON.parse(raw)); } catch { return parse(undefined); }
  };
  let value = load();
  const subs = new Set<() => void>();
  return {
    get: () => value,
    set: (next) => {
      value = typeof next === "function" ? (next as (p: T) => T)(value) : next;
      writeStorage(key, JSON.stringify(serialize(value)));
      subs.forEach((f) => f());
    },
    subscribe: (fn) => { subs.add(fn); return () => { subs.delete(fn); }; },
  };
}

export function useStore<T>(s: Store<T>): T {
  return useSyncExternalStore(s.subscribe, s.get, s.get);
}
