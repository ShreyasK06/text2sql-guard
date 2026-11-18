import { useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  filterEntries, historyStore, isSaved, removeEntry, requestOf, savedStore, toggleSaved, useHistory, useSaved,
  type HistoryEntry, type SavedEntry,
} from "../lib/history";
import { Empty, PageHead } from "../components/States";
import { Icon } from "../components/Icon";
import { SqlBlock } from "../components/SqlBlock";

const ago = (ts: number): string =>
  ts ? new Date(ts).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "";

function Who({ e }: { e: { role: string; region?: string } }) {
  return <span className="muted">{e.role}{e.region ? `, ${e.region}` : ""}</span>;
}

export function HistoryPage() {
  const history = useHistory();
  const saved = useSaved();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [confirmClear, setConfirmClear] = useState(false);
  const role = params.get("role") ?? "";
  const q = params.get("q") ?? "";

  const setParam = (k: string, v: string) =>
    setParams((p) => { const n = new URLSearchParams(p); if (v) n.set(k, v); else n.delete(k); return n; }, { replace: true });

  const rows = useMemo(() => filterEntries(history, { role, q }), [history, role, q]);
  const savedRows = useMemo(() => filterEntries(saved, { role, q }), [saved, role, q]);
  const rerun = (e: HistoryEntry | SavedEntry) => navigate("/console", { state: { rerun: requestOf(e) } });
  const star = (e: HistoryEntry) => {
    const d = { role: e.role, region: e.region, kind: e.kind, text: e.text };
    savedStore.set((l) => toggleSaved(l, d));
  };

  return (
    <>
      <PageHead title="History and saved queries">
        What you asked, as which role, what the SQL was and how the guards decided. The last 100 are kept.
      </PageHead>
      <p className="privacy" role="note">
        <b>Kept in this browser only.</b> History stores the question or SQL you typed, the role, the SQL the service produced and the
        decision. It never stores result rows, so nothing that was masked can leak through it. Nothing is sent anywhere.
      </p>
      <form className="filters hist-filters" aria-label="Filter history" onSubmit={(e) => e.preventDefault()}>
        <div>
          <label className="field-label" htmlFor="h-role">Role</label>
          <select id="h-role" className="input" value={role} onChange={(e) => setParam("role", e.target.value)}>
            <option value="">All roles</option><option>analyst</option><option>manager</option><option>admin</option>
          </select>
        </div>
        <div>
          <label className="field-label" htmlFor="h-q">Search</label>
          <input id="h-q" className="input" type="search" placeholder="question or SQL" value={q}
            onChange={(e) => setParam("q", e.target.value)} />
        </div>
        <button type="button" className="btn ghost" onClick={() => setParams({}, { replace: true })}>Clear filters</button>
      </form>

      <section className="card section-gap" aria-labelledby="sv">
        <h2 id="sv">Saved ({savedRows.length})</h2>
        {savedRows.length === 0 ? (
          <p className="muted small">
            {saved.length ? "No saved query matches these filters." : "Nothing saved yet. Use Save query on a result in the Console, or the star on a history row."}
          </p>
        ) : (
          <ul className="hlist">
            {savedRows.map((s) => (
              <li key={s.id} className="hrow">
                <div className="hmain">
                  <p className={s.kind === "sql" ? "code htext" : "htext"}>{s.text}</p>
                  <p className="small"><Who e={s} /> <span className="muted">· saved {ago(s.ts)}</span></p>
                </div>
                <div className="hact">
                  <button type="button" className="btn ghost sm" onClick={() => rerun(s)}>
                    <Icon name="play" filled /> Run<span className="sr-only">: {s.text}</span>
                  </button>
                  <button type="button" className="btn ghost sm" onClick={() => savedStore.set((l) => removeEntry(l, s.id))}>
                    <Icon name="trash" /> Remove<span className="sr-only"> saved query: {s.text}</span>
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="card" aria-labelledby="hs">
        <div className="row spread">
          <h2 id="hs">History ({rows.length}{rows.length !== history.length ? ` of ${history.length}` : ""})</h2>
          {history.length ? (
            confirmClear ? (
              <span className="row">
                <span className="small">Delete all {history.length} entries?</span>
                <button type="button" className="btn danger sm" onClick={() => { historyStore.set([]); setConfirmClear(false); }}>Yes, clear all</button>
                <button type="button" className="btn ghost sm" onClick={() => setConfirmClear(false)}>Cancel</button>
              </span>
            ) : (
              <button type="button" className="btn ghost sm" onClick={() => setConfirmClear(true)}><Icon name="trash" /> Clear all history</button>
            )
          ) : null}
        </div>
        {history.length === 0 ? (
          <Empty title="No history yet" action={<Link className="btn" to="/console">Open the console</Link>}>
            Run a query and it will be listed here so you can find it, run it again or save it.
          </Empty>
        ) : rows.length === 0 ? (
          <Empty title="Nothing matches these filters">Clear a filter or change the search.</Empty>
        ) : (
          <ul className="hlist">
            {rows.map((e) => (
              <li key={e.id} className="hrow">
                <div className="hmain">
                  <p className={e.kind === "sql" ? "code htext" : "htext"}>{e.text}</p>
                  <p className="small">
                    <span className={`chip ${e.decision}`}>{e.decision}</span>{" "}
                    {e.rule_id ? <code className="muted">{e.rule_id}</code> : null}{" "}
                    <Who e={e} /> <span className="muted">· {ago(e.ts)}{e.recorded ? " · recorded demo" : ""}</span>
                  </p>
                  {e.sql && e.kind === "question" ? (
                    <details className="hsql">
                      <summary>Generated SQL</summary>
                      <SqlBlock sql={e.sql} copy />
                    </details>
                  ) : null}
                </div>
                <div className="hact">
                  <button type="button" className="btn ghost sm" onClick={() => rerun(e)}>
                    <Icon name="play" filled /> Run again<span className="sr-only">: {e.text}</span>
                  </button>
                  <button type="button" className="btn ghost sm" aria-pressed={isSaved(saved, e)} onClick={() => star(e)}>
                    <Icon name="star" filled={isSaved(saved, e)} /> {isSaved(saved, e) ? "Saved" : "Save"}
                    <span className="sr-only">: {e.text}</span>
                  </button>
                  <button type="button" className="btn ghost sm" onClick={() => historyStore.set((l) => removeEntry(l, e.id))}>
                    <Icon name="trash" /> Delete<span className="sr-only">: {e.text}</span>
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
