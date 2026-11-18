import { useEffect, useState, type DependencyList } from "react";

export interface Loaded<T> { data: T | null; error: string; loading: boolean }

/** Run an async loader and expose { data, error, loading }. Re-runs when deps change. */
export function useLoad<T>(fn: () => Promise<T>, deps: DependencyList): Loaded<T> {
  const [state, setState] = useState<Loaded<T>>({ data: null, error: "", loading: true });
  useEffect(() => {
    let alive = true;
    setState((s) => ({ ...s, loading: true, error: "" }));
    fn().then(
      (data) => { if (alive) setState({ data, error: "", loading: false }); },
      (e: Error) => { if (alive) setState({ data: null, error: e.message, loading: false }); },
    );
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}
