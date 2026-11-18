import { useCallback, useEffect, useState, type DependencyList } from "react";

export interface Loaded<T> { data: T | null; error: string; loading: boolean; reload: () => void }

/** Run an async loader and expose { data, error, loading }. Re-runs when deps change. */
export function useLoad<T>(fn: () => Promise<T>, deps: DependencyList): Loaded<T> {
  const [state, setState] = useState<Omit<Loaded<T>, "reload">>({ data: null, error: "", loading: true });
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  useEffect(() => {
    let alive = true;
    setState((s) => ({ ...s, loading: true, error: "" }));
    fn().then(
      (data) => { if (alive) setState({ data, error: "", loading: false }); },
      (e: Error) => { if (alive) setState({ data: null, error: e.message, loading: false }); },
    );
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { ...state, reload };
}

/** Current time in ms, re-read every `everyMs` while `active`. Used for countdowns. */
export function useNow(active: boolean, everyMs = 250): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), everyMs);
    return () => clearInterval(id);
  }, [active, everyMs]);
  return now;
}
