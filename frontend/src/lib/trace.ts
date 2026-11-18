import type { GuardResult, QueryRequest, QueryResponse } from "./types";

export interface StageDef { key: string; name: string; guard: boolean; what: string }

export const STAGES: StageDef[] = [
  { key: "input_guard", name: "Input guard", guard: true,
    what: "Screens the question for destructive intent, instruction-override phrasing and requests for data your role cannot read." },
  { key: "retrieval", name: "Schema retrieval", guard: false,
    what: "Shows the model only the tables and columns your role may see, so hidden columns never reach the prompt." },
  { key: "generation", name: "SQL generation", guard: false,
    what: "The model writes the SQL. In this build that is the deterministic mock provider, not a live LLM." },
  { key: "allowlist", name: "Allowlist", guard: true,
    what: "Every table, column and function must be on your role's list. Anything not listed is refused." },
  { key: "ast_rules", name: "AST rules", guard: true,
    what: "SELECT only, one statement, forced LIMIT, caps on joins and nesting. Unparseable SQL is refused." },
  { key: "cost_gate", name: "EXPLAIN cost gate", guard: true,
    what: "Asks Postgres to plan the query without running it, and refuses plans that are too expensive." },
  { key: "sandbox", name: "Sandboxed execution", guard: true,
    what: "Runs as a read-only database user with column grants, row-level security and a statement timeout." },
  { key: "output_guard", name: "Output guard", guard: true,
    what: "Masks sensitive columns, scrubs PII patterns and caps cell size before anything is shown." },
];

const RULES: Record<string, string> = {
  "input.destructive_intent": "The question asks to change or destroy data",
  "input.restricted_data_intent": "The question asks for data this role may not read",
  "input.injection_phrase": "The question tries to override the system's instructions",
  "input.role_override": "The question tries to change the role",
  "input.embedded_sql": "The question contains raw SQL",
  "input.obfuscation": "The question contains encoded or obfuscated text",
  "input.too_long": "The question is too long",
  "input.empty": "The question is empty",
  "allowlist.column_denied": "That column is not permitted for this role",
  "allowlist.table_denied": "That table is not permitted for this role",
  "allowlist.function_denied": "That function is not on the allowed list",
  "allowlist.star_denied": "SELECT * would include a column this role cannot read",
  "allowlist.masked_column_misuse": "A masked column may only be shown, never filtered, joined or computed on",
  "allowlist.unresolved_column": "A column could not be matched to a known table",
  "allowlist.parse_error": "The SQL could not be parsed, so it is refused",
  "allowlist.type_denied": "That type cast is not allowed",
  "ast.non_select": "Only SELECT statements are allowed",
  "ast.multi_statement": "Only one statement is allowed",
  "ast.cartesian_join": "Tables are joined with no condition relating them",
  "ast.system_catalog": "System catalogs are off limits",
  "ast.parse_error": "The SQL could not be parsed, so it is refused",
  "ast.select_into": "SELECT INTO writes data and is refused",
  "ast.locking_clause": "Row-locking clauses are refused",
  "ast.recursive_cte": "Recursive queries are refused",
  "ast.too_many_joins": "Too many joins",
  "ast.too_many_subqueries": "Too many subqueries",
  "ast.too_deep": "Nested too deeply",
  "ast.too_long": "The SQL is too long",
  "ast.too_many_set_ops": "Too many UNION/INTERSECT operations",
  "ast.empty": "The SQL is empty",
  "cost.too_expensive": "The plan is estimated to cost too much",
  "cost.too_many_rows": "The plan is estimated to touch too many rows",
  "cost.explain_failed": "The database could not plan the query, so it is refused",
  "internal.region_required": "This role is limited to one region; pick a region",
  "internal.error": "An internal error occurred, so the request is refused",
};

export const ruleTitle = (id: string | null | undefined): string =>
  (id && RULES[id]) || (id && id.startsWith("sandbox.") ? "The database refused the query" : id || "");

export type StageStatus = "idle" | "pass" | "block" | "skip";
export interface StageView extends StageDef {
  status: StageStatus;
  label: string;
  ms: number | null;
  g: GuardResult | null;
  note: string;
}

/** Turn one /query response into eight stage records. */
export function buildTrace(resp: QueryResponse, req?: Pick<QueryRequest, "sql" | "question">): StageView[] {
  const byLayer: Record<string, GuardResult> = {};
  (resp.guard_results || []).forEach((g) => { byLayer[g.layer] = g; });
  const lat = resp.layer_latency_ms || {};
  const sqlMode = !!(req && req.sql && !req.question);
  const stopped = resp.decision !== "allow";
  const gen = resp.sql_generated != null || "generation" in lat;
  return STAGES.map((st) => {
    const o: StageView = { ...st, status: "idle", label: "Not reached", ms: null, g: null, note: "" };
    const g = byLayer[st.key];
    if (st.key === "retrieval" || st.key === "generation") {
      if (sqlMode) { o.status = "skip"; o.label = "Skipped"; o.note = "SQL was supplied directly, so no model was asked."; }
      else if (gen) { o.status = "pass"; o.label = "Ran"; o.ms = st.key === "generation" ? lat.generation ?? null : null; }
      return o;
    }
    if (g) {
      o.g = g;
      o.ms = g.latency_ms;
      if (g.decision === "block") { o.status = "block"; o.label = "Blocked"; }
      else { o.status = "pass"; o.label = "Passed"; }
      if (st.key === "output_guard" && g.decision !== "block") {
        const bits: string[] = [];
        if (resp.masked_columns && resp.masked_columns.length) bits.push(`masked ${resp.masked_columns.join(", ")}`);
        if (resp.redactions) bits.push(`${resp.redactions} value${resp.redactions === 1 ? "" : "s"} scrubbed`);
        o.note = bits.length ? bits.join("; ") : "nothing needed masking";
      }
    } else if (st.key === "input_guard" && sqlMode) {
      o.status = "skip"; o.label = "Skipped"; o.note = "There was no question to screen.";
    } else if (!stopped) {
      o.status = "skip"; o.label = "Switched off";
    }
    return o;
  });
}

const ADVICE: [string, string][] = [
  ["input.destructive_intent", "Ask a read-only question, such as a total, a count or a list. This app never changes data."],
  ["input.restricted_data_intent", "Pick a different role in the Policy page that is allowed to read that data, or ask about columns your role can see."],
  ["input.injection_phrase", "Rephrase the question as a plain data question without instructions to the system."],
  ["input.role_override", "The role is chosen with the Role control, not in the question."],
  ["input.embedded_sql", "Tick Supply SQL directly to run SQL, or describe what you want in words."],
  ["input.", "Rewrite the question in plain words and keep it short."],
  ["allowlist.column_denied", "Open the Policy page to see which columns this role can read, then ask about those."],
  ["allowlist.table_denied", "Open the Policy page to see which tables this role can read, then ask about those."],
  ["allowlist.star_denied", "List the columns you want instead of SELECT *."],
  ["allowlist.masked_column_misuse", "A masked column can be shown, but not filtered, joined or computed on. Remove it from the WHERE, JOIN or expression."],
  ["allowlist.", "Use only tables, columns and functions on this role's list (Policy page)."],
  ["ast.cartesian_join", "Add a JOIN ... ON condition that relates the two tables."],
  ["ast.non_select", "Only SELECT is allowed. Rewrite it as a read-only query."],
  ["ast.multi_statement", "Send one statement without a trailing second statement."],
  ["ast.", "Simplify the query: one SELECT, fewer joins and subqueries, no system catalogs."],
  ["cost.", "Narrow the query with a WHERE filter or aggregate it, so the database expects less work."],
  ["sandbox.", "The database itself refused this under the role's permissions. The Policy page shows what the role may read."],
  ["internal.region_required", "Managers see one region. Pick a region in the form."],
  ["llm.", "This is a model problem, not a policy decision. Try again."],
];

/** What the person can do next, in plain words. */
export function ruleAdvice(id: string | null | undefined): string {
  if (!id) return "";
  return ADVICE.find(([p]) => id === p || (p.endsWith(".") && id.startsWith(p)))?.[1] ?? "Rephrase the request or choose a role that is allowed to see what you asked for.";
}
