import { buildTrace, ruleTitle, STAGES } from "../lib/trace";
import { fmtMs, plural } from "../lib/format";
import type { QueryRequest, QueryResponse } from "../lib/types";
import { SqlBlock } from "./SqlBlock";
import { StageStrip, Trace } from "./Trace";
import { ResultTable } from "./ResultTable";
import { Empty } from "./States";

function Verdict({ r, req }: { r: QueryResponse; req: QueryRequest }) {
  const strip = <StageStrip trace={buildTrace(r, req)} />;
  if (r.decision === "allow") {
    const m = r.masked_columns?.length ?? 0;
    return (
      <div className="verdict allow">
        <h2>Allowed</h2>
        <span>
          {plural(r.row_count, "row")}
          {m ? `, ${plural(m, "column")} masked` : ""}, {fmtMs(r.latency_ms)} end to end
        </span>
        {strip}
      </div>
    );
  }
  if (r.decision === "block") {
    const stage = STAGES.find((s) => s.key === r.blocked_by)?.name ?? r.blocked_by;
    return (
      <div className="verdict block">
        <h2>Blocked at {stage}</h2>
        <span className="rule">{r.rule_id}</span>
        {strip}
        <p className="why"><b>{ruleTitle(r.rule_id)}.</b> {r.reason}</p>
      </div>
    );
  }
  return (
    <div className="verdict error">
      <h2>Database error</h2>
      <span className="rule">{r.rule_id || ""}</span>
      {strip}
      <p className="why">{r.reason}</p>
    </div>
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

export function ResultView({ r, req, recorded }: { r: QueryResponse; req: QueryRequest; recorded: boolean }) {
  const sqlMode = !!(req.sql && !req.question);
  const sqlShown = r.sql_generated || r.sql_executed || req.sql;
  const executed = r.decision === "allow";
  const ran = r.blocked_by != null && ["sandbox", "output_guard", "execution"].includes(r.blocked_by);
  return (
    <div className="stack reveal">
      {recorded ? <p className="small muted">Recorded response (real output of the service, not computed in your browser).</p> : null}
      <Verdict r={r} req={req} />
      <section className="card" aria-labelledby="th">
        <h2 id="th">What each stage decided</h2>
        <Trace resp={r} req={req} />
      </section>
      <section className="card" aria-labelledby="sh">
        <h2 id="sh">{sqlMode ? "SQL you supplied" : "Generated SQL"}</h2>
        {sqlShown ? (
          <>
            <SqlBlock sql={sqlShown} />
            {r.sql_executed && r.sql_executed !== sqlShown ? (
              <>
                <p className="small muted" style={{ marginTop: 12 }}>Executed after the guards rewrote it (forced limit):</p>
                <SqlBlock sql={r.sql_executed} style={{ marginTop: 6 }} />
              </>
            ) : null}
          </>
        ) : (
          <p className="muted">No SQL was produced: the question was stopped before the model was asked.</p>
        )}
      </section>
      <section className="card" aria-labelledby="rh">
        <h2 id="rh">Result</h2>
        {executed ? (
          <ResultTable r={r} recorded={recorded} />
        ) : (
          <Empty title="No rows returned">
            {ran ? "The query reached the database, which refused or failed it." : "Nothing was executed against the database."}
          </Empty>
        )}
      </section>
      <div className="cols-2">
        <section className="card" aria-labelledby="vh">
          <h2 id="vh">Self-check</h2>
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
