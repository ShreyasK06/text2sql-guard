import type { CSSProperties } from "react";
import { tokenizeSQL } from "../lib/sql";

export function SqlBlock({ sql, style }: { sql: string; style?: CSSProperties }) {
  return (
    <div className="sqlbox" style={style}>
      <pre>
        <code>
          {tokenizeSQL(sql).map((t, i) =>
            t.kind === "plain" ? t.text : <span key={i} className={`tk-${t.kind}`}>{t.text}</span>,
          )}
        </code>
      </pre>
    </div>
  );
}
