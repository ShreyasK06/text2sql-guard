import { buildTrace, ruleAdvice, ruleTitle, STAGES } from "../lib/trace";
import { fmtMs, plural } from "../lib/format";
import type { DoneKind } from "../lib/consoleMachine";
import type { QueryRequest, QueryResponse } from "../lib/types";
import { SqlBlock } from "./SqlBlock";
import { StageStrip, Trace } from "./Trace";
import { ResultTable } from "./ResultTable";
import { Empty } from "./States";
import { Icon } from "./Icon";

export interface ViewProps {
  r: QueryResponse;
  req: QueryRequest;
  kind: DoneKind;
  recorded: boolean;
  maxRows?: number;
  /** How the model is described in the trace, e.g. "NVIDIA, some/model". */
  modelNote?: string;
  saved: boolean;
  onToggleSave: () => void;
}

const stageName = (key: string | null) => STAGES.find((s) => s.key === key)?.name ?? key ?? "";

function Verdict({ r, req, kind }: { r: QueryResponse; req: QueryRequest; kind: DoneKind }) {
  const strip = <StageStrip trace={buildTrace(r, req)} />;
  if (kind === "allowed" || kind === "empty") {
    const m = r.masked_columns?.length ?? 0;
    const empty = kind === "empty";
    return (
      <section className={`verdict ${empty ? "empty-ok" : "allow"}`} aria-labelledby="vh">
        <div className="vhead">
          <span className="vicon" aria-hidden="true"><Icon name="check" /></span>
          <h2 id="vh">{empty ? "Allowed, no rows" : "Allowed"}</h2>
          <span className="vmeta">
            {empty ? "Every guard passed and the query ran, but nothing matched" : plural(r.row_count, "row")}
            {m ? `, ${plural(m, "column")} masked` : ""}, {fmtMs(r.latency_ms)} end to end
          </span>
          {strip}
        </div>
      </section>
    );
  }
  if (kind === "blocked") {
    return (
      <section className="verdict block" aria-labelledby="vh">
        <div className="vhead">
          <span className="vicon" aria-hidden="true"><Icon name="cross" /></span>
          <h2 id="vh">Blocked at {stageName(r.blocked_by)}</h2>
          <span className="rule">{r.rule_id}</span>
          {strip}
        </div>
        <p className="why"><b>{ruleTitle(r.rule_id)}.</b> {r.reason}</p>
      </section>
    );
  }
  return (
    <section className="verdict error" aria-labelledby="vh">
      <div className="vhead">
        <span className="vicon" aria-hidden="true"><Icon name="alert" /></span>
        <h2 id="vh">Database error</h2>
        <span className="rule">{r.rule_id || ""}</span>
        {strip}
      </div>
      <p className="why">{r.reason}</p>
    </section>
  );
}

/** Plain-language account of a refusal: which rule, in which stage, what that means, what to try. */
export function ExplainBlock({ r }: { r: QueryResponse }) {
  const st = STAGES.find((s) => s.key === r.blocked_by);
  const reachedDb = r.blocked_by != null && ["sandbox", "output_guard", "execution"].includes(r.blocked_by);
  const advice = ruleAdvice(r.rule_id);
  return (
    <details className="card explain" open>
      <summary><h2>Explain this block</h2></summary>
      <dl className="kv explain-kv">
        <dt>Rule</dt><dd><code>{r.rule_id}</code></dd>
        <dt>In plain words</dt><dd>{ruleTitle(r.rule_id)}.</dd>
        <dt>Where</dt><dd>{st ? <><b>{st.name}.</b> {st.what}</> : stageName(r.blocked_by)}</dd>
        <dt>Exactly what it saw</dt><dd>{r.reason}</dd>
        <dt>Did anything run?</dt>
        <dd>{reachedDb ? "The query reached the database, which refused it. No rows came back." : "No. The request was stopped before it touched the database."}</dd>
        {advice ? <><dt>What to try</dt><dd>{advice}</dd></> : null}
      </dl>
    </details>
  );
}

function SelfCheck({ r }: { r: QueryResponse }) {
  const sc = r.self_check;
  if (r.decision !== "allow") return <p className="muted">Not run: there was no answer to check.</p>;
  if (!sc) return <p className="muted">Not run (SQL was supplied directly).</p>;
  const cls = sc.verdict === "yes" ? "allow" : sc.verdict === "no" ? "block" : "error";
  const text = sc.verdict === "unverified" ? "Not verified" : sc.verdict === "yes" ? "Answers the question" : "Does not answer the question";
  return (
    <>
      <p><span className={`chip ${cls}`}>{text}</span></p>
      <p className="small muted" style={{ marginTop: 8 }}>
        {sc.explanation}. The self-check is advisory and does not block anything.
      </p>
    </>
  );
}

export function ResultView({ r, req, kind, recorded, maxRows, modelNote, saved, onToggleSave }: ViewProps) {
  const sqlMode = !!(req.sql && !req.question);
  const sqlShown = r.sql_generated || r.sql_executed || req.sql;
  const ran = r.blocked_by != null && ["sandbox", "output_guard", "execution"].includes(r.blocked_by);
  const saveBtn = (
    <button type="button" className="btn ghost sm" aria-pressed={saved} onClick={onToggleSave}>
      <Icon name="star" filled={saved} /> {saved ? "Saved" : "Save query"}
    </button>
  );
  return (
    <div className="stack reveal">
      {recorded ? <p className="small muted">Recorded response (real output of the service, not computed in your browser).</p> : null}
      <Verdict r={r} req={req} kind={kind} />
      {kind === "blocked" ? <ExplainBlock r={r} /> : null}
      {sqlShown ? (
        <section className="card" aria-labelledby="sh">
          <h2 id="sh">{sqlMode ? "SQL you supplied" : "Generated SQL"}</h2>
          <SqlBlock sql={sqlShown} copy tools={saveBtn} />
          {r.sql_executed && r.sql_executed !== sqlShown ? (
            <>
              <p className="small muted" style={{ marginTop: 12 }}>Executed after the guards rewrote it (forced limit):</p>
              <SqlBlock sql={r.sql_executed} style={{ marginTop: 6 }} copy />
            </>
          ) : null}
        </section>
      ) : (
        <div className="sqltools nosql">
          {saveBtn}
          <span className="small muted">No SQL was produced: the question was stopped before the model was asked.</span>
        </div>
      )}
      {kind === "blocked" && !ran ? null : <section className="card" aria-labelledby="rh">
        <h2 id="rh">Result</h2>
        {kind === "allowed" ? (
          <ResultTable r={r} req={req} recorded={recorded} maxRows={maxRows} />
        ) : kind === "empty" ? (
          <Empty title="The query ran and matched no rows">
            {req.role === "manager" ? `A manager only sees rows of region ${req.region ?? "their region"}. Try another region or a broader question.` : "Try a broader question or remove a filter."}
          </Empty>
        ) : (
          <Empty title="No rows returned">
            {ran ? "The query reached the database, which refused or failed it." : "Nothing was executed against the database."}
          </Empty>
        )}
      </section>}
      <section className="card" aria-labelledby="th">
        <h2 id="th">What each stage decided</h2>
        <Trace resp={r} req={req} modelNote={modelNote} />
      </section>
      <div className="cols-2">
        <section className="card" aria-labelledby="vch">
          <h2 id="vch">Self-check</h2>
          <SelfCheck r={r} />
        </section>
        <section className="card" aria-labelledby="mh">
          <h2 id="mh">Request</h2>
          <dl className="kv">
            <dt>Request id</dt><dd className="code">{r.request_id}</dd>
            <dt>Role</dt><dd>{req.role}{req.region ? `, region ${req.region}` : ""}</dd>
            <dt>Total time</dt><dd className="num">{fmtMs(r.latency_ms)}</dd>
            <dt>Retries</dt><dd>{r.retries}</dd>
            <dt>Guards on</dt><dd>{r.config}</dd>
          </dl>
        </section>
      </div>
    </div>
  );
}
