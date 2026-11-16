/* Shared helpers: escaping, SQL highlighting, and the 8-stage trace. Classic script, no build step. */
(function () {
  "use strict";

  const esc = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ---- SQL highlighting (display only; never used for decisions) ----
  const KW = new Set(("select from where group by order limit offset join left right inner outer full cross on as and or not in is null " +
    "like ilike between case when then else end distinct having union all intersect except with asc desc over partition exists " +
    "true false insert update delete drop alter create truncate into values set").split(" "));
  function highlightSQL(sql) {
    if (!sql) return "";
    const re = /(--[^\n]*)|('(?:[^']|'')*')|("(?:[^"]|"")*")|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][A-Za-z0-9_]*)(\s*\()?|([(),.;*=<>+\-\/%]+)|(\s+)|(.)/g;
    let out = "", m;
    while ((m = re.exec(sql))) {
      if (m[1]) out += `<span class="tk-com">${esc(m[1])}</span>`;
      else if (m[2]) out += `<span class="tk-str">${esc(m[2])}</span>`;
      else if (m[3]) out += esc(m[3]);
      else if (m[4]) out += `<span class="tk-num">${esc(m[4])}</span>`;
      else if (m[5]) {
        const w = m[5];
        if (KW.has(w.toLowerCase())) out += `<span class="tk-kw">${esc(w)}</span>${esc(m[6] || "")}`;
        else if (m[6]) out += `<span class="tk-fn">${esc(w)}</span>${esc(m[6])}`;
        else out += esc(w);
      } else if (m[7]) out += `<span class="tk-pun">${esc(m[7])}</span>`;
      else out += esc(m[0]);
    }
    return out;
  }

  // ---- the eight stages ----
  const STAGES = [
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

  const RULES = {
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
  const ruleTitle = (id) => RULES[id] || (id && id.startsWith("sandbox.") ? "The database refused the query" : id || "");

  const fmtMs = (v) => (v == null ? "" : v < 10 ? v.toFixed(2) + " ms" : v < 100 ? v.toFixed(1) + " ms" : Math.round(v) + " ms");

  /** Turn one /query response into eight stage records. */
  function buildTrace(resp, req) {
    const byLayer = {};
    (resp.guard_results || []).forEach((g) => { byLayer[g.layer] = g; });
    const lat = resp.layer_latency_ms || {};
    const sqlMode = !!(req && req.sql && !req.question);
    const stopped = resp.decision !== "allow";       // something ended the run early
    const gen = resp.sql_generated != null || "generation" in lat;
    return STAGES.map((st) => {
      const o = { ...st, status: "idle", label: "Not reached", ms: null, g: null, note: "" };
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
          const bits = [];
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

  function stripHTML(trace) {
    return trace.map((t) => `<i class="${t.status}" title="${esc(t.name)}: ${esc(t.label)}"></i>`).join("");
  }

  function traceHTML(resp, req) {
    const tr = buildTrace(resp, req);
    const max = Math.max(1, ...tr.map((t) => t.ms || 0));
    return `<ol class="trace" aria-label="Guardrail pipeline, eight stages">` + tr.map((t) => {
      const because = t.status === "block"
        ? `<div class="because"><b>${esc(ruleTitle(t.g.rule_id))}</b><code>${esc(t.g.rule_id)}</code> <span>${esc(t.g.reason)}</span></div>` : "";
      const note = t.note && t.status !== "block" ? `<div class="what">${esc(t.note)}</div>` : "";
      const lat = t.ms != null
        ? `<div class="lat num">${fmtMs(t.ms)}<div class="bar" aria-hidden="true"><i style="width:${Math.max(3, (t.ms / max) * 100)}%"></i></div></div>`
        : `<div class="lat"></div>`;
      return `<li class="stage ${t.status}"><div class="status">${esc(t.label)}</div>` +
        `<div><div class="name">${esc(t.name)}${t.guard ? "" : ' <span class="muted small">(not a gate)</span>'}</div>` +
        `<div class="what">${esc(t.what)}</div>${note}${because}</div>${lat}</li>`;
    }).join("") + `</ol>`;
  }

  window.T2S = { esc, highlightSQL, STAGES, ruleTitle, fmtMs, buildTrace, traceHTML, stripHTML };
})();
