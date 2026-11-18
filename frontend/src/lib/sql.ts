/** SQL tokenizer for display only. It never influences a decision. */
export interface Token { kind: "kw" | "str" | "num" | "fn" | "pun" | "com" | "plain"; text: string }

const KW = new Set(
  ("select from where group by order limit offset join left right inner outer full cross on as and or not in is null " +
    "like ilike between case when then else end distinct having union all intersect except with asc desc over partition exists " +
    "true false insert update delete drop alter create truncate into values set").split(" "),
);

export function tokenizeSQL(sql: string): Token[] {
  const re = /(--[^\n]*)|('(?:[^']|'')*')|("(?:[^"]|"")*")|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][A-Za-z0-9_]*)(\s*\()?|([(),.;*=<>+\-/%]+)|(\s+)|(.)/g;
  const out: Token[] = [];
  let m: RegExpExecArray | null;
  while ((m = re.exec(sql))) {
    if (m[1]) out.push({ kind: "com", text: m[1] });
    else if (m[2]) out.push({ kind: "str", text: m[2] });
    else if (m[3]) out.push({ kind: "plain", text: m[3] });
    else if (m[4]) out.push({ kind: "num", text: m[4] });
    else if (m[5]) {
      const w = m[5];
      if (KW.has(w.toLowerCase())) {
        out.push({ kind: "kw", text: w });
        if (m[6]) out.push({ kind: "plain", text: m[6] });
      } else if (m[6]) {
        out.push({ kind: "fn", text: w });
        out.push({ kind: "plain", text: m[6] });
      } else out.push({ kind: "plain", text: w });
    } else if (m[7]) out.push({ kind: "pun", text: m[7] });
    else out.push({ kind: "plain", text: m[0] });
  }
  return out;
}
