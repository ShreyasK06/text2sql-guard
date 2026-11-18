import { Fragment, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useApp } from "../context/AppContext";
import { useLoad } from "../hooks";
import { fmtMs } from "../lib/format";
import { ruleTitle } from "../lib/trace";
import type { AuditEntry } from "../lib/types";
import { Empty, ErrBox, PageHead, Skeleton } from "../components/States";
import { SqlBlock } from "../components/SqlBlock";

function AuditRows({ rows }: { rows: AuditEntry[] }) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  const toggle = (id: string) =>
    setOpen((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  return (
    <div className="audit-wrap" tabIndex={0} role="region" aria-label="Audit entries">
      <table className="audit">
        <thead>
          <tr>
            <th scope="col">Time</th><th scope="col">Role</th><th scope="col">Decision</th>
            <th scope="col" className="hide-sm">Stopped by</th><th scope="col">Request</th>
            <th scope="col"><span className="sr-only">Details</span></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((e) => {
            const t = new Date(e.ts);
            const isOpen = open.has(e.request_id);
            const did = `d-${e.request_id}`;
            return (
              <Fragment key={e.request_id}>
                <tr className="main">
                  <td className="num">{isNaN(t.getTime()) ? e.ts : t.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "medium" })}</td>
                  <td>{e.role}{e.region ? <span className="muted">, {e.region}</span> : null}</td>
                  <td><span className={`chip ${e.decision}`}>{e.decision}</span></td>
                  <td className="hide-sm">
                    {e.blocked_by ? <>{e.blocked_by}<br /><code className="muted">{e.rule_id || ""}</code></> : <span className="muted">none</span>}
                  </td>
                  <td className="q">{e.question || e.sql_generated || ""}</td>
                  <td>
                    <button className="exp" type="button" aria-expanded={isOpen} aria-controls={did} onClick={() => toggle(e.request_id)}>
                      {isOpen ? "Hide" : "Details"}
                    </button>
                  </td>
                </tr>
                <tr className="detail" id={did} hidden={!isOpen}>
                  <td colSpan={6}>
                    <dl className="kv">
                      <dt>Request id</dt><dd className="code">{e.request_id}</dd>
                      <dt>SQL</dt>
                      <dd>{e.sql_generated ? <SqlBlock sql={e.sql_generated} /> : <span className="muted">none produced</span>}</dd>
                      {e.reason ? <><dt>Reason</dt><dd>{ruleTitle(e.rule_id)}. {e.reason}</dd></> : null}
                      <dt>Stages</dt>
                      <dd>
                        <ul className="list-clean">
                          {(e.guard_results || []).length ? e.guard_results.map((g) => (
                            <li key={g.layer}>
                              {g.layer}: {g.decision}, {fmtMs(g.latency_ms)}
                              {g.rule_id ? <> (<code>{g.rule_id}</code>)</> : null}
                            </li>
                          )) : <li>none ran</li>}
                        </ul>
                      </dd>
                    </dl>
                  </td>
                </tr>
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function AuditPage() {
  const { mode, backend } = useApp();
  const { data, error, loading, reload } = useLoad(() => backend.audit(), [backend]);
  // Filters live in the URL so a filtered view can be shared, bookmarked and survives a reload.
  const [params, setParams] = useSearchParams();
  const dec = params.get("decision") ?? "";
  const rule = params.get("rule") ?? "";
  const role = params.get("role") ?? "";
  const q = params.get("q") ?? "";
  const setParam = (k: string, v: string) =>
    setParams((p) => { const n = new URLSearchParams(p); if (v) n.set(k, v); else n.delete(k); return n; }, { replace: true });
  const setDec = (v: string) => setParam("decision", v);
  const setRule = (v: string) => setParam("rule", v);
  const setRole = (v: string) => setParam("role", v);
  const setQ = (v: string) => setParam("q", v);

  const entries = useMemo(() => (data ? data.slice().reverse() : []), [data]);
  const rules = useMemo(() => [...new Set(entries.map((e) => e.rule_id).filter((x): x is string => !!x))].sort(), [entries]);
  const rows = entries.filter((e) =>
    (!dec || e.decision === dec) && (!rule || e.rule_id === rule) && (!role || e.role === role) &&
    (!q || `${e.question || ""} ${e.sql_generated || ""}`.toLowerCase().includes(q.toLowerCase())));
  const clear = () => setParams({}, { replace: true });

  return (
    <>
      <PageHead title="Audit log">
        {mode === "demo"
          ? "The audit entries the service wrote for the recorded requests."
          : "Every request the service has handled, newest first, with the guard that decided it."}
      </PageHead>
      {error ? <ErrBox title="The audit log could not be loaded." onRetry={reload}>{error}</ErrBox> : null}
      {loading ? <Skeleton /> : null}
      {data ? (
        <>
          <form className="filters" aria-label="Filter the audit log" onSubmit={(e) => e.preventDefault()}>
            <div>
              <label className="field-label" htmlFor="f-dec">Decision</label>
              <select id="f-dec" className="input" value={dec} onChange={(e) => setDec(e.target.value)}>
                <option value="">All</option><option>allow</option><option>block</option><option>error</option>
              </select>
            </div>
            <div>
              <label className="field-label" htmlFor="f-rule">Rule</label>
              <select id="f-rule" className="input" value={rule} onChange={(e) => setRule(e.target.value)}>
                <option value="">All</option>
                {rules.map((x) => <option key={x}>{x}</option>)}
              </select>
            </div>
            <div>
              <label className="field-label" htmlFor="f-role">Role</label>
              <select id="f-role" className="input" value={role} onChange={(e) => setRole(e.target.value)}>
                <option value="">All</option><option>analyst</option><option>manager</option><option>admin</option>
              </select>
            </div>
            <div>
              <label className="field-label" htmlFor="f-q">Search text</label>
              <input id="f-q" className="input" type="search" placeholder="question or SQL" value={q}
                onChange={(e) => setQ(e.target.value)} />
            </div>
            <button type="button" className="btn ghost" onClick={clear}>Clear</button>
          </form>
          <p className="small muted" role="status">{rows.length} of {entries.length} entries</p>
          {!entries.length ? (
            <Empty title="No requests yet" action={<Link className="btn" to="/console">Open the console</Link>}>
              Run something in the console and it will be logged here.
            </Empty>
          ) : !rows.length ? (
            <Empty title="No entries match these filters" action={<button type="button" className="btn ghost" onClick={clear}>Clear filters</button>}>
              Clear a filter to see more.
            </Empty>
          ) : (
            <AuditRows rows={rows} />
          )}
        </>
      ) : null}
    </>
  );
}
