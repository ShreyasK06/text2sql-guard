import { buildTrace, ruleTitle, type StageView } from "../lib/trace";
import { fmtMs } from "../lib/format";
import type { QueryRequest, QueryResponse } from "../lib/types";

type Req = Pick<QueryRequest, "sql" | "question">;

export function StageStrip({ trace }: { trace: StageView[] }) {
  return (
    <span className="strip" role="img" aria-label="Eight stage outcomes, in order">
      {trace.map((t) => (
        <i key={t.key} className={t.status} title={`${t.name}: ${t.label}`} />
      ))}
    </span>
  );
}

/** The eight stages with the status, rule, reason and latency of each. */
export function Trace({ resp, req }: { resp: QueryResponse; req?: Req }) {
  const tr = buildTrace(resp, req);
  const max = Math.max(1, ...tr.map((t) => t.ms || 0));
  return (
    <ol className="trace" aria-label="Guardrail pipeline, eight stages">
      {tr.map((t) => (
        <li key={t.key} className={`stage ${t.status}`}>
          <div className="status">{t.label}</div>
          <div>
            <div className="name">
              {t.name}
              {t.guard ? "" : <> <span className="muted small">(not a gate)</span></>}
            </div>
            <div className="what">{t.what}</div>
            {t.note && t.status !== "block" ? <div className="what">{t.note}</div> : null}
            {t.status === "block" && t.g ? (
              <div className="because">
                <b>{ruleTitle(t.g.rule_id)}</b>
                <code>{t.g.rule_id}</code> <span>{t.g.reason}</span>
              </div>
            ) : null}
          </div>
          {t.ms != null ? (
            <div className="lat num">
              {fmtMs(t.ms)}
              <div className="bar" aria-hidden="true">
                <i style={{ width: `${Math.max(3, (t.ms / max) * 100)}%` }} />
              </div>
            </div>
          ) : (
            <div className="lat" />
          )}
        </li>
      ))}
    </ol>
  );
}
