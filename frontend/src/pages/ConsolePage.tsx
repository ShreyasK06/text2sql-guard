import { useEffect, useMemo, useReducer, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useApp } from "../context/AppContext";
import { useLoad } from "../hooks";
import { EXAMPLES } from "../lib/examples";
import { consoleReducer, initialConsole, providerFailure, type ConsoleState } from "../lib/consoleMachine";
import { recordRun, requestOf, savedStore, toggleSaved, useHistory, useSaved, isSaved } from "../lib/history";
import { refusalProbes, suggestQuestions } from "../lib/suggest";
import { STAGES } from "../lib/trace";
import type { QueryRequest, Region, Role } from "../lib/types";
import { PageHead, ErrBox, Skeleton } from "../components/States";
import { ResultView } from "../components/ResultView";
import { FailPanel } from "../components/FailPanel";
import { Icon } from "../components/Icon";
import { providerLabel } from "../components/Layout";

const ROLES: Role[] = ["analyst", "manager", "admin"];
const REGIONS: Region[] = ["north", "south", "east", "west"];

const sameReq = (a: QueryRequest, b: QueryRequest) =>
  a.role === b.role && (a.question ?? "") === (b.question ?? "") && (a.sql ?? "") === (b.sql ?? "") &&
  (a.role !== "manager" || (a.region ?? "") === (b.region ?? ""));

function announce(s: ConsoleState): string {
  if (s.phase === "running") return "Running the request through the guards";
  if (s.phase === "failed") return `Model or network problem: ${s.message || s.kind}`;
  if (s.phase === "done") {
    if (s.kind === "blocked") return `Blocked at ${STAGES.find((x) => x.key === s.resp.blocked_by)?.name ?? s.resp.blocked_by}, rule ${s.resp.rule_id}`;
    if (s.kind === "empty") return "Allowed, the query matched no rows";
    if (s.kind === "db_error") return `Database error: ${s.resp.reason}`;
    return `Allowed, ${s.resp.row_count} rows`;
  }
  return "";
}

export function ConsolePage() {
  const { mode, demo, backend, llm } = useApp();
  const isDemo = mode === "demo";
  const location = useLocation();
  const navigate = useNavigate();
  const [role, setRole] = useState<Role>("analyst");
  const [region, setRegion] = useState<Region>("west");
  const [sqlMode, setSqlMode] = useState(false);
  const [question, setQuestion] = useState("");
  const [sql, setSql] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [formError, setFormError] = useState("");
  const [state, dispatch] = useReducer(consoleReducer, initialConsole);
  const outRef = useRef<HTMLDivElement>(null);
  const qRef = useRef<HTMLTextAreaElement>(null);
  const scrollOnShow = useRef(false);

  const cat = useLoad(() => backend.catalog(), [backend]);
  const pol = useLoad(() => backend.policy(role), [backend, role]);
  const history = useHistory();
  const saved = useSaved();

  const fill = (req: QueryRequest) => {
    setRole(req.role);
    if (req.region) setRegion(req.region);
    setSqlMode(!!(req.sql && !req.question));
    setQuestion(req.question || "");
    setSql(req.sql || "");
  };

  async function run(body: QueryRequest) {
    if (!(body.sql || body.question)) { setFormError("Write a question, or supply some SQL, first."); return; }
    setFormError("");
    scrollOnShow.current = true;
    dispatch({ type: "run", req: body, now: Date.now() });
    try {
      const resp = await backend.query(body);
      dispatch({ type: "resolved", resp, req: body, recorded: false, now: Date.now() });
      if (!providerFailure(resp, 0)) recordRun(body, resp, false);
    } catch (e) {
      dispatch({ type: "rejected", error: e, req: body, now: Date.now() });
    }
  }

  const current = (): QueryRequest => {
    const body: QueryRequest = { role };
    if (role === "manager") body.region = region;
    if (sqlMode) body.sql = sql.trim(); else body.question = question.trim();
    return body;
  };

  /** Show a recorded response (demo mode). */
  function showRecorded(req: QueryRequest, userInitiated: boolean): boolean {
    const c = demo?.cases.find((x) => sameReq(x.request, req));
    if (!c) return false;
    setFormError("");
    fill(c.request);
    scrollOnShow.current = userInitiated;
    dispatch({ type: "resolved", resp: c.response, req: c.request, recorded: true, now: Date.now() });
    if (userInitiated) recordRun(c.request, c.response, true);
    return true;
  }

  function pickExample(id: string) {
    const ex = EXAMPLES.find((x) => x.id === id);
    if (!ex) return;
    setSelected(id);
    if (isDemo) {
      const c = demo?.cases.find((x) => x.id === id);
      if (!c) { setFormError("That request was not recorded."); return; }
      showRecorded(c.request, true);
    } else {
      fill(ex.req);
      void run(ex.req);
    }
  }

  /** Re-run a stored request: live runs it, the demo can only replay what was recorded. */
  function reuse(req: QueryRequest) {
    setSelected(null);
    if (isDemo) {
      if (!showRecorded(req, true)) { fill(req); setFormError("This request is not part of the recording, so it cannot be replayed in the demo."); }
    } else {
      fill(req);
      void run(req);
    }
  }

  // In demo mode, show the first recorded request straight away.
  useEffect(() => {
    if (isDemo && demo && !(location.state as { rerun?: QueryRequest } | null)?.rerun) {
      setSelected(EXAMPLES[0].id);
      const c = demo.cases.find((x) => x.id === EXAMPLES[0].id);
      if (c) showRecorded(c.request, false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDemo, demo]);

  // A re-run requested from the History page.
  useEffect(() => {
    const rerun = (location.state as { rerun?: QueryRequest } | null)?.rerun;
    if (!rerun) return;
    navigate(location.pathname, { replace: true, state: null });
    reuse(rerun);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.state]);

  useEffect(() => {
    if (state.phase !== "idle" && scrollOnShow.current && window.matchMedia("(max-width: 960px)").matches) {
      outRef.current?.scrollIntoView({ block: "start", behavior: "auto" });
    }
    if (state.phase !== "running") scrollOnShow.current = false;
  }, [state]);

  const submit = (e: FormEvent) => { e.preventDefault(); if (!isDemo && state.phase !== "running") void run(current()); };
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && !isDemo) {
      e.preventDefault();
      if (state.phase !== "running") void run(current());
    }
  };

  const suggestions = useMemo(() => (pol.data ? suggestQuestions(pol.data) : []), [pol.data]);
  const probes = useMemo(() => (pol.data ? refusalProbes(pol.data) : []), [pol.data]);
  const useQuestion = (q: string) => {
    setSqlMode(false);
    setQuestion(q);
    setTimeout(() => qRef.current?.focus(), 0);
  };

  const roleSaved = saved.filter((s) => s.role === role).slice(0, 5);
  const roleRecent = history.filter((h) => h.role === role).slice(0, 5);
  const modelNote = llm?.mode === "live"
    ? `The model (${providerLabel(llm)}) writes the SQL from your question and only the schema your role may see. Its output is untrusted: every later stage checks it.`
    : undefined;
  const running = state.phase === "running";
  const draft = state.phase === "done" || state.phase === "failed" ? state.req : null;

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
                <textarea id="question" ref={qRef} className="input" rows={3} maxLength={2000} value={question} disabled={isDemo}
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
            {formError ? <p className="field-error" role="alert">{formError}</p> : null}
            <div className="row">
              <button className="btn" type="submit" disabled={isDemo || running}>
                <Icon name="play" filled />
                {isDemo ? "Recorded demo, cannot run" : running ? "Running…" : "Run through the guards"}
              </button>
              {isDemo ? null : <span className="small muted"><kbd>Ctrl</kbd> or <kbd>Cmd</kbd> + <kbd>Enter</kbd> also runs</span>}
            </div>
            <p className="small muted">
              {isDemo
                ? "The form shows the recorded request. Pick another below."
                : "Role and region are asserted by this form, as in the demo API. There is no authentication."}
            </p>
          </form>

          {isDemo ? (
            <section className="card" aria-labelledby="exh">
              <h2 id="exh">Recorded requests</h2>
              <ul className="examples">
                {EXAMPLES.map((e) => (
                  <li key={e.id}>
                    <button type="button" aria-pressed={selected === e.id} onClick={() => pickExample(e.id)}>
                      <span>{e.title}</span>
                      <span className="who">{e.who}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          ) : (
            <section className="card" aria-labelledby="exh">
              <h2 id="exh">Ask about what {role} can see</h2>
              {pol.loading ? <Skeleton label="Loading suggestions" /> : pol.error ? (
                <p className="small muted">Suggestions need the role's policy, which could not be loaded.</p>
              ) : (
                <>
                  <ul className="chips" aria-label={`Example questions for ${role}`}>
                    {suggestions.map((s) => (
                      <li key={s.question}>
                        <button type="button" className="qchip" onClick={() => useQuestion(s.question)}>
                          <span>{s.question}</span><small>{s.hint}</small>
                        </button>
                      </li>
                    ))}
                  </ul>
                  <h3 className="subhead">Things this role should be refused</h3>
                  <ul className="chips" aria-label={`Questions ${role} should be refused`}>
                    {probes.map((s) => (
                      <li key={s.question}>
                        <button type="button" className="qchip probe" onClick={() => useQuestion(s.question)}>
                          <span>{s.question}</span><small>{s.hint}</small>
                        </button>
                      </li>
                    ))}
                  </ul>
                  <p className="small muted">Picking one fills the box. Press Run to send it.</p>
                </>
              )}
            </section>
          )}

          <section className="card" aria-labelledby="rch">
            <h2 id="rch">Saved and recent for {role}</h2>
            {roleSaved.length === 0 && roleRecent.length === 0 ? (
              <p className="small muted">Nothing yet. Queries you run appear here and on the History page. They are kept in this browser only.</p>
            ) : (
              <>
                {roleSaved.length ? (
                  <>
                    <h3 className="subhead">Saved</h3>
                    <ul className="qlist">
                      {roleSaved.map((s) => (
                        <li key={s.id}>
                          <button type="button" onClick={() => reuse(requestOf(s))} title={isDemo ? "Replay if recorded" : "Run again"}>
                            <Icon name="star" filled /><span className={s.kind === "sql" ? "code" : undefined}>{s.text}</span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  </>
                ) : null}
                {roleRecent.length ? (
                  <>
                    <h3 className="subhead">Recent</h3>
                    <ul className="qlist">
                      {roleRecent.map((h) => (
                        <li key={h.id}>
                          <button type="button" onClick={() => reuse(requestOf(h))} title={isDemo ? "Replay if recorded" : "Run again"}>
                            <span className={`dot ${h.decision}`} aria-hidden="true" />
                            <span className={h.kind === "sql" ? "code" : undefined}>{h.text}</span>
                            <span className="sr-only"> ({h.decision})</span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  </>
                ) : null}
              </>
            )}
          </section>
        </div>

        <div ref={outRef} className="out">
          <p className="sr-only" role="status">{announce(state)}</p>
          {running ? (
            <>
              <p className="small muted" aria-hidden="true">Asking the model, then passing the eight stages. This usually takes a few seconds.</p>
              <Skeleton label="Running the pipeline" variant="result" />
            </>
          ) : state.phase === "failed" ? (
            <FailPanel f={state} canSql={!sqlMode} onRetry={() => void run(state.req)} />
          ) : state.phase === "done" ? (
            <ResultView
              r={state.resp} req={state.req} kind={state.kind} recorded={state.recorded}
              maxRows={cat.data?.limits?.max_rows} modelNote={modelNote}
              saved={isSaved(saved, { role: state.req.role, region: state.req.role === "manager" ? state.req.region : undefined, kind: state.req.sql && !state.req.question ? "sql" : "question", text: (state.req.sql && !state.req.question ? state.req.sql : state.req.question) ?? "" })}
              onToggleSave={() => {
                const q = state.req;
                const isSql = !!(q.sql && !q.question);
                savedStore.set((l) => toggleSaved(l, {
                  role: q.role, region: q.role === "manager" ? q.region : undefined,
                  kind: isSql ? "sql" : "question", text: (isSql ? q.sql : q.question) ?? "",
                }));
              }}
            />
          ) : formError && !draft ? (
            <ErrBox>{formError}</ErrBox>
          ) : (
            <div className="empty">
              <h2>Nothing run yet</h2>
              <p>
                {isDemo
                  ? "Choose a recorded request on the left."
                  : "Write a question or pick a suggestion on the left, then press Run."}{" "}
                The verdict, the SQL, the result and what each of the eight stages decided will appear here.
              </p>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
