import type { CSSProperties, ReactNode } from "react";
import { tokenizeSQL } from "../lib/sql";
import { CopyButton } from "./CopyButton";

export function SqlBlock({ sql, style, copy = false, tools }: { sql: string; style?: CSSProperties; copy?: boolean; tools?: ReactNode }) {
  return (
    <div className="sqlbox" style={style}>
      <pre tabIndex={0} aria-label="SQL">
        <code>
          {tokenizeSQL(sql).map((t, i) =>
            t.kind === "plain" ? t.text : <span key={i} className={`tk-${t.kind}`}>{t.text}</span>,
          )}
        </code>
      </pre>
      {copy || tools ? (
        <div className="sqltools">
          {copy ? <CopyButton text={sql} /> : null}
          {tools}
        </div>
      ) : null}
    </div>
  );
}
