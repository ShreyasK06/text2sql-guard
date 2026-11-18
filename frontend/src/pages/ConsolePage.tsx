import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { useApp } from "../context/AppContext";
import { EXAMPLES } from "../lib/examples";
import type { QueryRequest, QueryResponse, Region, Role } from "../lib/types";
import { PageHead, ErrBox, Skeleton } from "../components/States";
import { ResultView } from "../components/ResultView";

const ROLES: Role[] = ["analyst", "manager", "admin"];
const REGIONS: Region[] = ["north", "south", "east", "west"];

interface Shown { resp: QueryResponse; req: QueryRequest; recorded: boolean }

export function ConsolePage() {
  const { mode, demo, backend } = useApp();
  const isDemo = mode === "demo";
  const [role, setRole] = useState<Role>("analyst");
  const [region, setRegion] = useState<Region>("west");
  const [sqlMode, setSqlMode] = useState(false);
  const [question, setQuestion] = useState("");
  const [sql, setSql] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [shown, setShown] = useState<Shown | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const outRef = useRef<HTMLDivElement>(null);
  const scrollOnShow = useRef(false);

  const fill = (req: QueryRequest) => {
    setRole(req.role);
    if (req.region) setRegion(req.region);
    setSqlMode(!!(req.sql && !req.question));
    setQuestion(req.question || "");
    setSql(req.sql || "");
  };

  async function run(body: QueryRequest) {
    if (!(body.sql || body.question)) { setError("Write a question, or supply some SQL, first."); setShown(null); return; }
    setError("");
    setRunning(true);
    try {
      const resp = await backend.query(body);
      scrollOnShow.current = true;
      setShown({ resp, req: body, recorded: false });
    } catch (e) {
      setShown(null);
      setError(`The request failed. ${(e as Error).message}`);
    } finally {
      setRunning(false);
    }
  }

  const current = (): QueryRequest => {
    const body: QueryRequest = { role };
    if (role === "manager") body.region = region;
    if (sqlMode) body.sql = sql.trim(); else body.question = question.trim();
    return body;
  };

  function pick(id: string, userInitiated: boolean) {
    const ex = EXAMPLES.find((x) => x.id === id);
    if (!ex) return;
    setSelected(id);
    if (isDemo) {
      const c = demo?.cases.find((x) => x.id === id);
      if (!c) { setShown(null); setError("That request was not recorded."); return; }
      setError("");
      fill(c.request);
      scrollOnShow.current = userInitiated;
      setShown({ resp: c.response, req: c.request, recorded: true });
    } else {
      fill(ex.req);
      void run(ex.req);
    }
  }

  // In demo mode, show the first recorded request straight away.
  useEffect(() => {
    if (isDemo && demo) pick(EXAMPLES[0].id, false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDemo, demo]);

  useEffect(() => {
    if (shown && scrollOnShow.current && window.matchMedia("(max-width: 960px)").matches) {
      outRef.current?.scrollIntoView({ block: "start", behavior: "auto" });
    }
    scrollOnShow.current = false;
  }, [shown]);

  const submit = (e: FormEvent) => { e.preventDefault(); if (!isDemo) void run(current()); };
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && !isDemo) { e.preventDefault(); void run(current()); }
  };

  return (
    <>
      <PageHead title="Ask a question, watch the guards decide">
        {isDemo
          ? "Pick a recorded request. Each one is a real response from the service, shown stage by stage."
          : "Write a question as a given role. It is turned into SQL, then passes eight stages before any row is shown."}
      </PageHead>
      <div className="console">
        <div className="stack">
          <form className="card stack" noValidate onSubmit={submit} onKeyDown={onKey} aria-label="Query">
            <fieldset className="field-set">
              <legend className="field-label">Role</legend>
              <div className="seg">
                {ROLES.map((r) => (
                  <span key={r} style={{ display: "contents" }}>
                    <input type="radio" name="role" id={`role-${r}`} value={r} checked={role === r} disabled={isDemo}
                      onChange={() => setRole(r)} />
                    <label htmlFor={`role-${r}`}>{r}</label>
                  </span>
                ))}
              </div>
            </fieldset>
            {role === "manager" ? (
              <div>
                <label className="field-label" htmlFor="region">Region (this role sees one region only)</label>
                <select id="region" className="input" value={region} disabled={isDemo}
                  onChange={(e) => setRegion(e.target.value as Region)}>
                  {REGIONS.map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
              </div>
            ) : null}
            {!sqlMode ? (
              <div>
                <label className="field-label" htmlFor="question">Question</label>
                <textarea id="question" className="input" rows={3} maxLength={2000} value={question} disabled={isDemo}
                  placeholder="For example: total revenue by region" onChange={(e) => setQuestion(e.target.value)} />
              </div>
            ) : null}
            <label className="check">
              <input type="checkbox" checked={sqlMode} disabled={isDemo} onChange={(e) => setSqlMode(e.target.checked)} />
              Supply SQL directly, skip the model
            </label>
            {sqlMode ? (
              <div>
                <label className="field-label" htmlFor="sql">SQL</label>
                <textarea id="sql" className="input sql" rows={3} maxLength={20000} spellCheck={false} value={sql}
                  disabled={isDemo} placeholder="SELECT region, count(*) FROM orders GROUP BY region"
                  onChange={(e) => setSql(e.target.value)} />
              </div>
            ) : null}
            <div className="row">
              <button className="btn" type="submit" disabled={isDemo || running}>
                {isDemo ? "Recorded demo, cannot run" : "Run through the guards"}
              </button>
              {isDemo ? null : <span className="small muted"><kbd>Ctrl</kbd> + <kbd>Enter</kbd></span>}
            </div>
            <p className="small muted">
              {isDemo
                ? "The form shows the recorded request. Pick another below."
                : "Role and region are asserted by this form, as in the demo API. There is no authentication."}
            </p>
          </form>
          <section className="card" aria-labelledby="exh">
            <h2 id="exh">{isDemo ? "Recorded requests" : "Try one"}</h2>
            <ul className="examples">
              {EXAMPLES.map((e) => (
                <li key={e.id}>
                  <button type="button" aria-pressed={selected === e.id} onClick={() => pick(e.id, true)}>
                    <span>{e.title}</span>
                    <span className="who">{e.who}</span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        </div>
        <div ref={outRef} aria-live="polite">
          {running ? (
            <Skeleton label="Running the pipeline" />
          ) : error ? (
            <ErrBox>{error}</ErrBox>
          ) : shown ? (
            <ResultView r={shown.resp} req={shown.req} recorded={shown.recorded} />
          ) : (
            <div className="empty">
              <h2>Nothing run yet</h2>
              <p>Choose a request on the left. The result, the SQL, and what each of the eight stages decided will appear here.</p>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
