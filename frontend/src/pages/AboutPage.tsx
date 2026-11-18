import { Link } from "react-router-dom";
import { useApp } from "../context/AppContext";
import { useLoad } from "../hooks";
import { staticApi } from "../lib/api";
import { pct } from "../lib/format";
import { BASE } from "../lib/url";
import { Trace } from "../components/Trace";

const PIPELINE: { name: string; text: string; kind: string; model?: boolean }[] = [
  { name: "Input guard", text: "Screens the question for destructive intent, override phrasing and requests for data your role cannot read.", kind: "Can refuse" },
  { name: "Schema retrieval", text: "The model is shown only the tables and columns your role may see.", kind: "Narrows the prompt", model: true },
  { name: "SQL generation", text: "The model writes SQL. Here that is a deterministic mock, not a live LLM.", kind: "Never trusted", model: true },
  { name: "Allowlist", text: "Every table, column and function must be listed for the role. Default deny.", kind: "Can refuse" },
  { name: "AST rules", text: "SELECT only, one statement, forced LIMIT, caps on joins and nesting.", kind: "Can refuse" },
  { name: "EXPLAIN cost gate", text: "Postgres plans the query without running it. Expensive plans are refused.", kind: "Can refuse" },
  { name: "Sandboxed execution", text: "A read-only database user with column grants, row-level security and a timeout.", kind: "Can refuse" },
  { name: "Output guard", text: "Masks sensitive columns, scrubs PII patterns, caps cell size.", kind: "Rewrites results" },
];

function Stats() {
  const { data: R } = useLoad(() => staticApi.results(), []);
  const h = R?.splits.heldout, d = R?.splits.dev;
  return (
    <div className="stat-row">
      <div className="stat">
        <span className="big">{h ? pct(h.false_block.rate) : " "}</span>
        <p>{h ? `Held-out benign queries wrongly refused: ${h.false_blocks.length} of ${h.n_benign}, 95% interval ${pct(h.false_block.ci_lo)} to ${pct(h.false_block.ci_hi)}.` : "Held-out benign queries wrongly refused. See the Safety page."}</p>
      </div>
      <div className="stat">
        <span className="big">{d ? pct(d.false_block.rate) : " "}</span>
        <p>{d ? `Dev benign queries wrongly refused: ${d.false_blocks.length} of ${d.n_benign}. Used while tuning, so optimistic.` : "Dev benign queries wrongly refused."}</p>
      </div>
      <div className="stat">
        <span className="big">{h ? `${h.end_to_end.median_overhead_ms} ms` : " "}</span>
        <p>{h ? `Median time the guards add per query on the held-out split, against ${h.end_to_end.none_median_ms.toFixed(1)} ms unguarded. No model latency included.` : "Median time the guards add per query."}</p>
      </div>
    </div>
  );
}

export function AboutPage() {
  const { mode } = useApp();
  const { data: demo } = useLoad(() => staticApi.demo(), []);
  const c = demo?.cases.find((x) => x.id === "delete");
  return (
    <>
      <section className="hero">
        <div className="wrap">
          <h1 tabIndex={-1}>Ask the database anything your role may see.</h1>
          <p className="lead">
            text2sql-guard turns a question into SQL, then runs it through eight stages before a read-only database user returns masked, role-scoped rows. Six of those stages can refuse it, and each refusal says why.
          </p>
          <div className="actions">
            <Link className="btn" to="/console">Open the console</Link>
            {mode === "demo" ? null : <a className="btn ghost" href={`${BASE}?demo=1#/console`}>Watch the recorded demo</a>}
          </div>
          <div className="hero-trace" aria-live="polite">
            <p className="cap">A real response from the recorded demo: an analyst asks the system to delete all orders.</p>
            {c ? (
              <div className="reveal">
                <p style={{ marginBottom: 12 }}><b>Request:</b> &quot;{c.request.question}&quot; as <b>{c.request.role}</b></p>
                <Trace resp={c.response} req={c.request} />
              </div>
            ) : null}
          </div>
        </div>
      </section>

      <section className="band dark" aria-labelledby="h-stages">
        <div className="wrap">
          <h2 id="h-stages">Eight stages. Six can say no.</h2>
          <p className="intro">
            Defense in depth means no single check carries the whole load. The input guard is pattern based and can be paraphrased around, so the allowlist, the AST rules, the cost gate and the database itself each have to hold on their own.
          </p>
          <ol className="pipeline">
            {PIPELINE.map((s) => (
              <li key={s.name} className={s.model ? "model" : undefined}>
                <strong>{s.name}</strong>
                <span>{s.text}</span>
                <em className="kind" style={{ fontStyle: "normal" }}>{s.kind}</em>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section className="band" aria-labelledby="h-closed">
        <div className="wrap">
          <h2 id="h-closed">When a stage cannot decide, the answer is no.</h2>
          <div className="split">
            <div>
              <h3>Fail closed</h3>
              <p>SQL that cannot be parsed is refused, not guessed at. If the EXPLAIN step fails, that is a block. A role limited to one region that arrives without a region is refused.</p>
              <p>Every block carries a rule id and a plain-language reason, so a refusal can be read, audited and argued with.</p>
            </div>
            <div>
              <h3>Allowlist over blocklist</h3>
              <p>Nothing is permitted because it was not forbidden. A table, column or function that is not listed for a role does not exist for it.</p>
              <p>One file, <code>policies/policy.yaml</code>, drives the validator, the output guard and the database grants, so the rules the code enforces and the rules the database enforces cannot drift apart. Enforcement lives in code and in database permissions, never in the prompt.</p>
            </div>
          </div>
        </div>
      </section>

      <section className="band sunk" aria-labelledby="h-truth">
        <div className="wrap">
          <h2 id="h-truth">What it does, and what it does not.</h2>
          <div className="truth">
            <div className="yes">
              <h3>What it does</h3>
              <ul>
                <li>Applies the role&apos;s allowlist and masks sensitive columns before anything is displayed.</li>
                <li>Restricts a regional manager to one region with row-level security in the database.</li>
                <li>Refuses anything that is not a single, bounded SELECT, and runs the rest as a read-only user.</li>
                <li>Logs every request with the stage that decided it.</li>
                <li>Is covered by an offline test suite that includes tests against a real PostgreSQL 16.</li>
              </ul>
            </div>
            <div className="no">
              <h3>What it does not</h3>
              <ul>
                <li>It has no measured attack block rate. The evaluation holds no attack items.</li>
                <li>It has never been run against a live language model. Only the mock provider was used.</li>
                <li>It has no authentication. Role and region are asserted by the caller in this demo.</li>
                <li>Its input guard is pattern based, so paraphrased intent can get past that stage.</li>
                <li>Masking is column and pattern based, so sensitive values inside free text are only partly covered.</li>
                <li>It sometimes refuses a legitimate query. See the numbers below.</li>
              </ul>
            </div>
          </div>
        </div>
      </section>

      <section className="band" aria-labelledby="h-num">
        <div className="wrap">
          <h2 id="h-num">The numbers we have, and the one we do not.</h2>
          <p className="intro">
            These are read from the files in <code>eval/results</code>. They measure the cost of the guards on legitimate questions. They say nothing yet about how well the guards stop an attacker.
          </p>
          <Stats />
          <p className="intro"><Link to="/safety">Read the full results, including the three queries that were wrongly refused.</Link></p>
        </div>
      </section>

      <section className="band dark" aria-labelledby="h-run">
        <div className="wrap">
          <h2 id="h-run">Run it on your own machine.</h2>
          <p className="intro">
            With Docker, one command brings up PostgreSQL, the API and this app on one port. The default provider is a deterministic mock, so no API key is needed.
          </p>
          <div className="codeblock" style={{ background: "color-mix(in srgb, var(--field-ink) 9%, var(--field))", color: "var(--field-ink)" }}>
            <pre><code>{`docker compose up --build
# then open http://localhost:8000`}</code></pre>
          </div>
          <p className="intro">
            Without Docker, use Python 3.11 or newer for the API and Node 20 or newer for the front end. The steps are in the README. The recorded demo works from any static host.
          </p>
        </div>
      </section>
    </>
  );
}
