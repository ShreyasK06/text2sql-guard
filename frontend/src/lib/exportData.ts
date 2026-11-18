import type { QueryRequest, QueryResponse } from "./types";

/** A cell that a spreadsheet would run as a formula starts with one of these. */
const FORMULA = /^[=+\-@\t\r]/;

export function csvCell(v: unknown): string {
  if (v === null || v === undefined) return "";
  let s = typeof v === "object" ? JSON.stringify(v) : String(v);
  // Formula-injection guard: only text can be a formula. Real numbers (e.g. -5) are left alone.
  if (typeof v !== "number" && typeof v !== "boolean" && FORMULA.test(s)) s = `'${s}`;
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function maskNotice(masked: string[]): string {
  return masked.length
    ? `Columns masked by the output guard before export: ${masked.join(", ")}. The real values never left the server.`
    : "No columns needed masking for this role. Values are exactly what the output guard released.";
}

export function toCSV(r: Pick<QueryResponse, "columns" | "rows" | "masked_columns">, now: Date = new Date()): string {
  const head = [
    `# text2sql-guard export, ${now.toISOString()}. All data is synthetic.`,
    `# ${maskNotice(r.masked_columns ?? [])}`,
    "# Lines starting with # are notices; skip them when importing (for example pandas comment='#').",
    "# Text cells that start with = + - or @ are prefixed with ' so spreadsheets do not run them as formulas.",
  ];
  const lines = [r.columns.map(csvCell).join(","), ...r.rows.map((row) => row.map(csvCell).join(","))];
  return [...head, ...lines].join("\r\n") + "\r\n";
}

export function toJSONExport(
  r: Pick<QueryResponse, "columns" | "rows" | "masked_columns" | "row_count" | "sql_executed" | "sql_generated">,
  req: Pick<QueryRequest, "role" | "region" | "question">,
  now: Date = new Date(),
): string {
  return JSON.stringify({
    notice: maskNotice(r.masked_columns ?? []),
    exported_at: now.toISOString(),
    role: req.role,
    region: req.region ?? null,
    question: req.question ?? null,
    sql: r.sql_executed || r.sql_generated || null,
    masked_columns: r.masked_columns ?? [],
    row_count: r.row_count,
    columns: r.columns,
    rows: r.rows,
  }, null, 2) + "\n";
}

export function downloadText(filename: string, text: string, mime: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: `${mime};charset=utf-8` }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export const exportName = (ext: string, now: Date = new Date()): string =>
  `text2sql-guard-result-${now.toISOString().slice(0, 19).replace(/[:T]/g, "-")}.${ext}`;
