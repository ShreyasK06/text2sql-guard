import type { QueryResponse } from "../lib/types";
import { plural } from "../lib/format";
import { Empty } from "./States";

export function ResultTable({ r, recorded }: { r: QueryResponse; recorded: boolean }) {
  if (!r.columns.length) return <Empty title="No columns" />;
  if (!r.rows.length) return <Empty title="The query ran and matched no rows" />;
  const masked = new Set(r.masked_columns || []);
  const shown = r.rows.length;
  return (
    <>
      <p className="small muted" style={{ marginBottom: 8 }}>
        {shown < r.row_count
          ? `Showing the first ${shown} of ${r.row_count} rows${recorded ? " (the recording keeps 12)" : ""}.`
          : `${plural(r.row_count, "row")}.`}
        {masked.size ? " Masked values were replaced by the output guard before they reached this page." : ""}
      </p>
      <div className="tablewrap" tabIndex={0} role="region" aria-label="Query result rows">
        <table className="data">
          <thead>
            <tr>
              {r.columns.map((c) => (
                <th key={c} scope="col">
                  {c}
                  {masked.has(c) ? <span className="tag">masked</span> : null}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {r.rows.map((row, ri) => (
              <tr key={ri}>
                {row.map((v, i) => {
                  const isNum = typeof v === "number";
                  const cls = masked.has(r.columns[i]) ? "masked" : isNum ? "n" : "";
                  return (
                    <td key={i} className={cls}>
                      {v === null ? (
                        <span className="muted">null</span>
                      ) : isNum && !Number.isInteger(v) ? (
                        (v as number).toLocaleString(undefined, { maximumFractionDigits: 2 })
                      ) : (
                        String(v)
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
