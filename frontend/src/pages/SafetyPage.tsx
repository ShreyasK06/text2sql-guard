import { useLoad } from "../hooks";
import { staticApi } from "../lib/api";
import { cfgName, pct } from "../lib/format";
import type { ResultsSplit } from "../lib/types";
import { ErrBox, PageHead, Skeleton } from "../components/States";

function Ci({ s }: { s: ResultsSplit }) {
  const f = s.false_block, max = 0.15;
  const l = (f.ci_lo / max) * 100;
  const w = Math.max(0.8, ((f.ci_hi - f.ci_lo) / max) * 100);
  const x = Math.min(99, (f.rate / max) * 100);
  return (
    <>
      <div className="ci" role="img"
        aria-label={`False-block rate ${pct(f.rate)}, 95 percent interval ${pct(f.ci_lo)} to ${pct(f.ci_hi)}`}>
        <span className="range" style={{ left: `${l}%`, width: `${w}%` }} />
        <span className="pt" style={{ left: `calc(${x}% - 2px)` }} />
      </div>
      <div className="ci-scale" aria-hidden="true"><span>0%</span><span>5%</span><span>10%</span><span>15%</span></div>
    </>
  );
}

function SplitCard({ name, s, note }: { name: string; s: ResultsSplit; note: string }) {
  return (
    <section className="card">
      <h2>{name}: benign queries wrongly refused</h2>
      <span className="big">{pct(s.false_block.rate)}</span>
      <p className="muted">
        {s.false_blocks.length} of {s.n_benign} benign queries, 95% interval [{pct(s.false_block.ci_lo)}, {pct(s.false_block.ci_hi)}]
      </p>
      <Ci s={s} />
      <p className="small muted">{note}</p>
    </section>
  );
}

export function SafetyPage() {
  const { data: R, error, loading } = useLoad(() => staticApi.results(), []);
  const head = (
    <PageHead title="Safety and results">
      Every number on this page is read from <code>eval/results</code> (collected by <code>scripts/build_web_data.py</code>). Nothing is typed by hand.
    </PageHead>
  );
  if (loading) return <>{head}<Skeleton /></>;
  if (error || !R) return <>{head}<ErrBox title="Could not load the results.">{error}</ErrBox></>;

  const ho = R.splits.heldout, dv = R.splits.dev;
  const maxLat = Math.max(...ho.latency_per_layer.map((l) => l.median_ms || 0));
  const e = ho.end_to_end;
  return (
    <>
      {head}
      <section className="notice" aria-labelledby="nm">
        <h2 id="nm">The attack block rate was not measured</h2>
        <p>
          The evaluation contains <b>{dv.n_attacks + ho.n_attacks} attack items</b> in either split. So there is no block rate, no per-category rate and no per-layer attack ablation for this project, and none is claimed. What was measured is the opposite error, how often a legitimate query is refused, and how much time the guards add.
        </p>
      </section>
      <div className="grid2">
        <SplitCard name="Held-out" s={ho}
          note={`${ho.n_benign} benign queries. Lightly held-out, not strictly blind: the plan-cost ceiling was tuned on the dev split, and I cannot prove the held-out items were never looked at.`} />
        <SplitCard name="Dev" s={dv}
          note={`${dv.n_benign} benign queries, used while tuning, so this number is optimistic. A result of 0 out of ${dv.n_benign} has a degenerate interval.`} />
      </div>

      <section className="card section-gap">
        <h2>The {ho.false_blocks.length} held-out queries that were wrongly refused</h2>
        <div className="scroll-x">
          <table className="plain">
            <thead><tr><th scope="col">Item</th><th scope="col">Layer</th><th scope="col">Rule</th><th scope="col">Reason</th></tr></thead>
            <tbody>
              {ho.false_blocks.map((b) => (
                <tr key={b.id}><td>{b.id}</td><td>{b.layer}</td><td><code>{b.rule_id}</code></td><td>{b.reason}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="small muted" style={{ marginTop: 12 }}>
          heldout-benign-035 also fails in Postgres with every guard off, so it is partly a labelling problem in the item rather than a pure guard decision. The other two are genuine over-blocking by the policy.
        </p>
      </section>

      <div className="grid2">
        <section className="card">
          <h2>Time added by the guards</h2>
          <p className="big" style={{ fontSize: 44 }}>{e.median_overhead_ms} ms</p>
          <p className="muted">
            median overhead on the held-out split: {e.full_median_ms.toFixed(2)} ms with all guards, {e.none_median_ms.toFixed(2)} ms without (p95 {e.full_p95_ms.toFixed(1)} and {e.none_p95_ms.toFixed(1)} ms). On dev the overhead is {dv.end_to_end.median_overhead_ms} ms. Benign queries, cache bypassed, local Postgres, mock provider, so no model latency is included.
          </p>
        </section>
        <section className="card">
          <h2>Per-stage latency, held-out (ms)</h2>
          <div className="scroll-x">
            <table className="plain">
              <thead>
                <tr><th scope="col">Stage</th><th scope="col" className="n">Median</th><th scope="col" className="n">p95</th><th scope="col" className="n">n</th></tr>
              </thead>
              <tbody>
                {ho.latency_per_layer.filter((l) => l.n > 0 && l.median_ms != null).map((l) => (
                  <tr key={l.scope}>
                    <th scope="row">{l.scope.replace(/_/g, " ")}</th>
                    <td className="n"><span className="hbar" style={{ width: Math.max(2, ((l.median_ms as number) / maxLat) * 80) }} />{(l.median_ms as number).toFixed(2)}</td>
                    <td className="n">{(l.p95_ms as number).toFixed(2)}</td>
                    <td className="n">{l.n}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      <section className="card section-gap">
        <h2>Benign false blocks under each layer configuration</h2>
        <p className="small muted" style={{ marginBottom: 12 }}>
          Each layer alone, and the full pipeline minus each layer. The attack rows of this ablation are empty because there are no attack items.
        </p>
        <div className="scroll-x">
          <table className="plain">
            <thead>
              <tr><th scope="col">Configuration</th><th scope="col" className="n">Dev (n={dv.n_benign})</th><th scope="col" className="n">Held-out (n={ho.n_benign})</th></tr>
            </thead>
            <tbody>
              {dv.ablation_benign.map((a, i) => (
                <tr key={a.config}>
                  <th scope="row">{cfgName(a.config)}</th><td className="n">{a.false_blocks}</td><td className="n">{ho.ablation_benign[i].false_blocks}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="card">
        <h2>What this page does not show</h2>
        <ul className="list-clean">
          <li><b>No live LLM was used.</b> Only the deterministic mock provider ran; the Anthropic and Gemini providers are unit-tested against fake HTTP clients.</li>
          <li><b>No attack evaluation.</b> No block rate, interval, or per-layer attack ablation exists.</li>
          <li><b>Spider and BIRD were not run,</b> so the accuracy cost of the guards is unmeasured.</li>
          <li><b>The verifier and self-correction loop were not evaluated.</b></li>
          <li>One database engine (PostgreSQL 16) and one synthetic schema.</li>
          <li>
            Test suite when these results were generated: {R.tests ? `${R.tests.passed} passed, ${R.tests.failed} failed, ${R.tests.skipped} skipped` : "unknown"}. Tests check rule behaviour on fixed examples. They are not a block rate.
          </li>
        </ul>
      </section>
    </>
  );
}
