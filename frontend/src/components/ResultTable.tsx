import { useState } from "react";
import type { QueryRequest, QueryResponse } from "../lib/types";
import { plural } from "../lib/format";
import { downloadText, exportName, toCSV, toJSONExport } from "../lib/exportData";
import { Empty } from "./States";
import { Icon } from "./Icon";

export type ColKind = "num" | "date" | "text";
const ISO = /^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}.*)?$/;
const TRUNC = 48;

/** Numbers right-align, dates and times stay on one line in tabular figures, everything else is text. */
export function columnKind(rows: unknown[][], i: number): ColKind {
  let seen = 0, nums = 0, dates = 0;
  for (const row of rows) {
    const v = row[i];
    if (v === null || v === undefined) continue;
    seen++;
    if (typeof v === "number") nums++;
    else if (typeof v === "string" && ISO.test(v)) dates++;
  }
  if (!seen) return "text";
  if (nums === seen) return "num";
  if (dates === seen) return "date";
  return "text";
}

const fmtNum = (v: number) => (Number.isInteger(v) ? String(v) : v.toLocaleString(undefined, { maximumFractionDigits: 2 }));

function Cell({ v, kind, masked, open, onToggle }: { v: unknown; kind: ColKind; masked: boolean; open: boolean; onToggle: () => void }) {
  const cls = [masked ? "masked" : "", kind === "num" ? "n" : kind === "date" ? "d" : ""].filter(Boolean).join(" ");
  let body;
  if (v === null || v === undefined) body = <span className="muted">null</span>;
  else if (typeof v === "number") body = fmtNum(v);
  else {
    const s = typeof v === "object" ? JSON.stringify(v) : String(v);
    body = s.length > TRUNC ? (
      <>
        <span className={open ? "long open" : "long"}>{open ? s : `${s.slice(0, TRUNC)}…`}</span>{" "}
        <button type="button" className="more" aria-expanded={open} onClick={onToggle}>{open ? "less" : "more"}</button>
      </>
    ) : s;
  }
  return <td className={cls || undefined}>{body}</td>;
}

export function ExportBar({ r, req }: { r: QueryResponse; req: QueryRequest }) {
  const masked = r.masked_columns?.length ?? 0;
  return (
    <div className="exportbar">
      <button type="button" className="btn ghost sm" onClick={() => downloadText(exportName("csv"), toCSV(r), "text/csv")}>
        <Icon name="download" /> CSV
      </button>
      <button type="button" className="btn ghost sm" onClick={() => downloadText(exportName("json"), toJSONExport(r, req), "application/json")}>
        <Icon name="download" /> JSON
      </button>
      <span className="small muted">
        {masked ? "Exports carry the masked values and a notice saying so." : "Exports carry exactly the rows shown here."}
      </span>
    </div>
  );
}

export function ResultTable({ r, req, recorded, maxRows }: { r: QueryResponse; req: QueryRequest; recorded: boolean; maxRows?: number }) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  if (!r.columns.length) return <Empty title="No columns" />;
  const masked = new Set(r.masked_columns || []);
  const shown = r.rows.length;
  const capped = !!maxRows && r.row_count >= maxRows;
  const kinds = r.columns.map((_, i) => columnKind(r.rows, i));
  const toggle = (k: string) => setOpen((s) => { const n = new Set(s); if (n.has(k)) n.delete(k); else n.add(k); return n; });
  return (
    <>
      <p className="rowcount">
        <b className="num">{shown < r.row_count ? `${shown} of ${r.row_count}` : plural(r.row_count, "row")}</b>
        {shown < r.row_count ? ` rows shown${recorded ? " (the recording keeps 12)" : ""}` : ""}
        {capped ? (
          <span className="capnote"> Capped at {maxRows} rows by the guard: there may be more rows than this.</span>
        ) : null}
      </p>
      <div className="tablewrap" tabIndex={0} role="region" aria-label="Query result rows">
        <table className="data">
          <thead>
            <tr>
              {r.columns.map((c, i) => (
                <th key={c} scope="col" className={kinds[i] === "num" ? "n" : undefined}>
                  {c}
                  {masked.has(c) ? (
                    <button type="button" className="tag" aria-label={`${c} is a masked column. Masked by the output guard; the real values never left the server.`}>
                      masked
                      <span className="tip" aria-hidden="true">Masked by the output guard. The real values never left the server.</span>
                    </button>
                  ) : null}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {r.rows.map((row, ri) => (
              <tr key={ri}>
                {row.map((v, i) => {
                  const k = `${ri}:${i}`;
                  return <Cell key={i} v={v} kind={kinds[i]} masked={masked.has(r.columns[i])} open={open.has(k)} onToggle={() => toggle(k)} />;
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ExportBar r={r} req={req} />
    </>
  );
}
