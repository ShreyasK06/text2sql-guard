import type { CSSProperties } from "react";
import { buildTrace, ruleTitle, type StageStatus, type StageView } from "../lib/trace";
import { fmtMs } from "../lib/format";
import type { QueryRequest, QueryResponse } from "../lib/types";
import { Icon } from "./Icon";

type Req = Pick<QueryRequest, "sql" | "question">;

const GLYPH: Record<StageStatus, "check" | "cross" | "minus" | "dot"> = { pass: "check", block: "cross", skip: "minus", idle: "dot" };

export function StageStrip({ trace }: { trace: StageView[] }) {
  const summary = trace.map((t) => `${t.name}: ${t.label}`).join("; ");
  return (
    <span className="strip" role="img" aria-label={`Eight stage outcomes. ${summary}`}>
      {trace.map((t) => (
        <i key={t.key} className={t.status} title={`${t.name}: ${t.label}`}>
          <Icon name={GLYPH[t.status]} />
        </i>
      ))}
    </span>
  );
}

/** The eight stages. Each is one scannable line; the stage that refused (if any) opens by itself. */
export function Trace({ resp, req, modelNote }: { resp: QueryResponse; req?: Req; modelNote?: string }) {
  const tr = buildTrace(resp, req);
  const max = Math.max(1, ...tr.map((t) => t.ms || 0));
  return (
    <ol className="trace" aria-label="Guardrail pipeline, eight stages">
      {tr.map((t, i) => {
        const what = t.key === "generation" && modelNote ? modelNote : t.what;
        const head = (
          <>
            <span className="glyph" aria-hidden="true"><Icon name={GLYPH[t.status]} /></span>
            <span className="sname">
              {t.name}
              {t.guard ? null : <span className="muted small"> (not a gate)</span>}
            </span>
            <span className="slabel">{t.label}</span>
            <span className="lat num">
              {t.ms != null ? fmtMs(t.ms) : ""}
              {t.ms != null ? <span className="bar" aria-hidden="true"><i style={{ width: `${Math.max(3, (t.ms / max) * 100)}%` }} /></span> : null}
            </span>
          </>
        );
        return (
          <li key={t.key} className={`stage ${t.status}`} style={{ "--i": i } as CSSProperties}>
            {t.status !== "idle" ? (
              <details open={t.status === "block"}>
                <summary>{head}</summary>
                <div className="sbody">
                  <p className="what">{what}</p>
                  {t.note && t.status !== "block" ? <p className="what">{t.note}</p> : null}
                  {t.status === "block" && t.g ? (
                    <p className="because">
                      <b>{ruleTitle(t.g.rule_id)}</b>
                      <code>{t.g.rule_id}</code> <span>{t.g.reason}</span>
                    </p>
                  ) : null}
                </div>
              </details>
            ) : (
              <div className="row-flat">{head}</div>
            )}
          </li>
        );
      })}
    </ol>
  );
}
