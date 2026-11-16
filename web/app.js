/* text2sql-guard app. Hash-routed, no build step. Talks to the FastAPI service, or replays
   web/data/demo.json (real recorded responses) when there is no backend. */
(function () {
  "use strict";
  const { esc, highlightSQL, traceHTML, stripHTML, buildTrace, ruleTitle, fmtMs, STAGES } = window.T2S;
  const $ = (s, r = document) => r.querySelector(s);
  const main = $("#view");

  const state = { demo: false, demoData: null, health: null };
  const params = new URLSearchParams(location.search);

  // The same requests are recorded in scripts/capture_demo.py; in demo mode they are looked up by id.
  const EXAMPLES = [
    { id: "revenue", title: "Revenue by region", who: "analyst", req: { question: "total revenue by region", role: "analyst" } },
    { id: "masked", title: "Customer contact details", who: "manager, west", req: { question: "list customers with contact details", role: "manager", region: "west" } },
    { id: "admin", title: "Customer contact details", who: "admin", req: { question: "list customers with contact details", role: "admin" } },
    { id: "tickets", title: "Support tickets by status", who: "analyst", req: { question: "count of support tickets by status", role: "analyst" } },
    { id: "pii-intent", title: "Customer contact details", who: "analyst", req: { question: "list customers with contact details", role: "analyst" } },
    { id: "delete", title: "Ask to remove all orders", who: "analyst", req: { question: "delete all orders", role: "analyst" } },
    { id: "passwords", title: "Password column, typed as SQL", who: "analyst", req: { sql: "SELECT id, password_hash FROM users", role: "analyst" } },
    { id: "cartesian", title: "Two tables, no join condition", who: "analyst", req: { sql: "SELECT o.id, p.name FROM orders o, products p", role: "analyst" } },
  ];

  // ---------------------------------------------------------------- data access
  async function getJSON(path, opts) {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), (opts && opts.timeout) || 20000);
    try {
      const r = await fetch(path, { ...(opts || {}), signal: ctl.signal });
      if (!r.ok) {
        let detail = "";
        try { const j = await r.json(); detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch (e) { /* not json */ }
        throw new Error(detail || `${r.status} ${r.statusText}`);
      }
      return await r.json();
    } finally { clearTimeout(t); }
  }
  const api = {
    catalog: () => (state.demo ? Promise.resolve(state.demoData.catalog) : getJSON("catalog")),
    policy: (role) => (state.demo ? Promise.resolve(state.demoData.policies[role]) : getJSON(`policy/${encodeURIComponent(role)}`)),
    audit: () => (state.demo ? Promise.resolve(state.demoData.audit) : getJSON("audit?n=500")),
    results: () => getJSON("data/results.json"),
    query: (body) => getJSON("query", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) }),
  };

  // ---------------------------------------------------------------- mode
  function setMode(kind, text) {
    const el = $("#mode");
    el.className = "mode" + (kind === "demo" ? " demo" : kind === "off" ? " off" : "");
    el.innerHTML = `<i aria-hidden="true"></i><span>${esc(text)}</span>`;
  }
  async function init() {
    const forced = params.has("demo") || window.T2S_DEMO === true;
    let reason = forced ? "" : null;
    if (!forced) {
      try { state.health = await getJSON("health", { timeout: 3000 }); }
      catch (e) { reason = "No API answered at this address."; }
    }
    if (forced || reason !== null) {
      try { state.demoData = await getJSON("data/demo.json"); }
      catch (e) {
        setMode("off", "Offline");
        main.innerHTML = `<div class="errbox" role="alert">Could not reach the API or load the recorded demo (${esc(e.message)}).</div>`;
        return;
      }
      state.demo = true;
      const d = new Date(state.demoData.recorded_at);
      $("#banner").innerHTML = `<div class="demo-banner" role="note">Recorded demo, not live. <span>These are real responses captured from the running service on ${esc(d.toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" }))} (mock provider, local Postgres). Nothing you click here is executed. ${esc(reason || "")}</span></div>`;
      setMode("demo", "Recorded demo, not live");
    } else {
      const h = state.health;
      setMode(h.db ? "live" : "off", `Live, ${h.provider} provider, database ${h.db ? "up" : "DOWN"}`);
    }
    window.addEventListener("hashchange", route);
    route();
  }

  // ---------------------------------------------------------------- router
  const ROUTES = {
    console: { title: "Query console", fn: viewConsole },
    audit: { title: "Audit log", fn: viewAudit },
    policy: { title: "Policy explorer", fn: viewPolicy },
    safety: { title: "Safety and results", fn: viewSafety },
  };
  let firstRoute = true;
  function route() {
    const name = (location.hash.replace(/^#\/?/, "") || "console").split("?")[0];
    const key = ROUTES[name] ? name : "console";
    const r = ROUTES[key];
    document.querySelectorAll(".nav a[data-route]").forEach((a) => {
      if (a.dataset.route === key) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    document.title = `${r.title} - text2sql-guard`;
    main.innerHTML = `<div class="skeleton" aria-busy="true" role="status"><span class="sr-only">Loading</span></div>`;
    Promise.resolve(r.fn()).then(() => {
      const h = main.querySelector("h1");
      if (h) { h.tabIndex = -1; if (!firstRoute) h.focus({ preventScroll: true }); }
      firstRoute = false;
    }).catch((e) => {
      main.innerHTML = `<div class="errbox" role="alert"><b>Something went wrong.</b> ${esc(e.message)}</div>`;
    });
  }

  const pageHead = (h, p) => `<header class="page-head"><h1>${esc(h)}</h1><p>${p}</p></header>`;

  // ---------------------------------------------------------------- console
  function viewConsole() {
    const demo = state.demo;
    main.innerHTML = pageHead("Ask a question, watch the guards decide",
      demo ? "Pick a recorded request. Each one is a real response from the service, shown stage by stage."
        : "Write a question as a given role. It is turned into SQL, then passes eight stages before any row is shown.") + `
    <div class="console">
      <div class="stack">
        <form id="qform" class="card stack" novalidate>
          <fieldset class="field-set" id="roleseg"><legend class="field-label">Role</legend><div class="seg">
            ${["analyst", "manager", "admin"].map((r, i) => `<input type="radio" name="role" id="role-${r}" value="${r}"${i === 0 ? " checked" : ""}><label for="role-${r}">${r}</label>`).join("")}
          </div></fieldset>
          <div id="regionrow" hidden>
            <label class="field-label" for="region">Region (this role sees one region only)</label>
            <select id="region" class="input">${["north", "south", "east", "west"].map((r) => `<option value="${r}"${r === "west" ? " selected" : ""}>${r}</option>`).join("")}</select>
          </div>
          <div id="qrow">
            <label class="field-label" for="question">Question</label>
            <textarea id="question" class="input" rows="3" maxlength="2000" placeholder="For example: total revenue by region"></textarea>
          </div>
          <label class="check"><input type="checkbox" id="sqlmode"> Supply SQL directly, skip the model</label>
          <div id="sqlrow" hidden>
            <label class="field-label" for="sql">SQL</label>
            <textarea id="sql" class="input sql" rows="3" maxlength="20000" spellcheck="false" placeholder="SELECT region, count(*) FROM orders GROUP BY region"></textarea>
          </div>
          <div class="row">
            <button class="btn" type="submit" id="runbtn">${demo ? "Recorded demo, cannot run" : "Run through the guards"}</button>
            ${demo ? "" : `<span class="small muted"><kbd>Ctrl</kbd> + <kbd>Enter</kbd></span>`}
          </div>
          <p class="small muted" id="formnote">${demo ? "The form shows the recorded request. Pick another below." : "Role and region are asserted by this form, as in the demo API. There is no authentication."}</p>
        </form>
        <section class="card" aria-labelledby="exh"><h2 id="exh">${demo ? "Recorded requests" : "Try one"}</h2>
          <ul class="examples">${EXAMPLES.map((e) => `<li><button type="button" data-ex="${e.id}" aria-pressed="false"><span>${esc(e.title)}</span><span class="who">${esc(e.who)}</span></button></li>`).join("")}</ul>
        </section>
      </div>
      <div id="out" aria-live="polite">${emptyOut()}</div>
    </div>`;

    const f = $("#qform");
    const role = () => f.elements.role.value;
    const syncRole = () => { $("#regionrow").hidden = role() !== "manager"; };
    const syncMode = () => { const on = $("#sqlmode").checked; $("#sqlrow").hidden = !on; $("#qrow").hidden = on; };
    f.addEventListener("change", (e) => { if (e.target.name === "role") syncRole(); if (e.target.id === "sqlmode") syncMode(); });
    syncRole(); syncMode();

    function fill(req) {
      document.getElementById("role-" + req.role).checked = true;
      if (req.region) $("#region").value = req.region;
      $("#sqlmode").checked = !!(req.sql && !req.question);
      $("#question").value = req.question || "";
      $("#sql").value = req.sql || "";
      syncRole(); syncMode();
    }
    if (demo) { f.querySelectorAll("input, textarea, select").forEach((el) => { el.disabled = true; }); $("#runbtn").disabled = true; }

    async function runLive() {
      const body = { role: role() };
      if (role() === "manager") body.region = $("#region").value;
      if ($("#sqlmode").checked) body.sql = $("#sql").value.trim(); else body.question = $("#question").value.trim();
      if (!(body.sql || body.question)) { $("#out").innerHTML = `<div class="errbox" role="alert">Write a question, or supply some SQL, first.</div>`; return; }
      $("#out").innerHTML = `<div class="skeleton" role="status" aria-busy="true"><span class="sr-only">Running the pipeline</span></div>`;
      $("#runbtn").disabled = true;
      try { renderResult(await api.query(body), body); }
      catch (e) { $("#out").innerHTML = `<div class="errbox" role="alert"><b>The request failed.</b> ${esc(e.message)}</div>`; }
      finally { $("#runbtn").disabled = false; }
    }
    f.addEventListener("submit", (e) => { e.preventDefault(); if (!demo) runLive(); });
    f.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && !demo) { e.preventDefault(); runLive(); } });

    document.querySelectorAll("[data-ex]").forEach((b) => b.addEventListener("click", () => {
      const ex = EXAMPLES.find((x) => x.id === b.dataset.ex);
      document.querySelectorAll("[data-ex]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      if (demo) {
        const c = state.demoData.cases.find((x) => x.id === ex.id);
        if (!c) { $("#out").innerHTML = `<div class="errbox" role="alert">That request was not recorded.</div>`; return; }
        fill(c.request); renderResult(c.response, c.request, true);
      } else { fill(ex.req); runLive(); }
    }));
    if (demo) document.querySelector("[data-ex]").click();
  }

  function emptyOut() {
    return `<div class="empty"><h2>Nothing run yet</h2><p>Choose a request on the left. The result, the SQL, and what each of the eight stages decided will appear here.</p></div>`;
  }

  function renderResult(r, req, recorded) {
    const blockedStage = (STAGES.find((s) => s.key === r.blocked_by) || {}).name || r.blocked_by;
    let verdict;
    const strip = `<span class="strip" role="img" aria-label="Eight stage outcomes, in order">${stripHTML(buildTrace(r, req))}</span>`;
    if (r.decision === "allow") {
      const m = r.masked_columns && r.masked_columns.length;
      verdict = `<div class="verdict allow"><h2>Allowed</h2><span>${r.row_count} row${r.row_count === 1 ? "" : "s"}${m ? `, ${r.masked_columns.length} column${r.masked_columns.length === 1 ? "" : "s"} masked` : ""}, ${fmtMs(r.latency_ms)} end to end</span>${strip}</div>`;
    } else if (r.decision === "block") {
      verdict = `<div class="verdict block"><h2>Blocked at ${esc(blockedStage)}</h2><span class="rule">${esc(r.rule_id)}</span>${strip}<p class="why"><b>${esc(ruleTitle(r.rule_id))}.</b> ${esc(r.reason)}</p></div>`;
    } else {
      verdict = `<div class="verdict error"><h2>Database error</h2><span class="rule">${esc(r.rule_id || "")}</span>${strip}<p class="why">${esc(r.reason)}</p></div>`;
    }
    const sqlMode = !!(req.sql && !req.question);
    const sqlShown = r.sql_generated || r.sql_executed || req.sql;
    const sqlHTML = sqlShown
      ? `<div class="sqlbox"><pre><code>${highlightSQL(sqlShown)}</code></pre></div>` +
        (r.sql_executed && r.sql_executed !== sqlShown ? `<p class="small muted" style="margin-top:12px">Executed after the guards rewrote it (forced limit):</p><div class="sqlbox" style="margin-top:6px"><pre><code>${highlightSQL(r.sql_executed)}</code></pre></div>` : "")
      : `<p class="muted">No SQL was produced: the question was stopped before the model was asked.</p>`;
    const executed = r.decision === "allow";
    const sc = r.self_check;
    const scHTML = !executed ? `<p class="muted">Not run: there was no answer to check.</p>`
      : !sc ? `<p class="muted">Not run (SQL was supplied directly).</p>`
        : `<p><span class="chip ${sc.verdict === "yes" ? "allow" : sc.verdict === "no" ? "block" : "error"}">${esc(sc.verdict === "unverified" ? "Not verified" : sc.verdict === "yes" ? "Answers the question" : "Does not answer the question")}</span></p><p class="small muted" style="margin-top:8px">${esc(sc.explanation)}. The self-check is advisory and does not block anything.</p>`;
    $("#out").innerHTML = `<div class="stack reveal">
      ${recorded ? `<p class="small muted">Recorded response (real output of the service, not computed in your browser).</p>` : ""}
      ${verdict}
      <section class="card" aria-labelledby="th"><h2 id="th">What each stage decided</h2>${traceHTML(r, req)}</section>
      <section class="card" aria-labelledby="sh"><h2 id="sh">${sqlMode ? "SQL you supplied" : "Generated SQL"}</h2>${sqlHTML}</section>
      <section class="card" aria-labelledby="rh"><h2 id="rh">Result</h2>${executed ? tableHTML(r) : blockedBody(r)}</section>
      <div class="cols-2">
        <section class="card" aria-labelledby="vh"><h2 id="vh">Self-check</h2>${scHTML}</section>
        <section class="card" aria-labelledby="mh"><h2 id="mh">Request</h2><dl class="kv">
          <dt>Request id</dt><dd class="code">${esc(r.request_id)}</dd>
          <dt>Role</dt><dd>${esc(req.role)}${req.region ? ", region " + esc(req.region) : ""}</dd>
          <dt>Total time</dt><dd class="num">${fmtMs(r.latency_ms)}</dd>
          <dt>Retries</dt><dd>${r.retries}</dd><dt>Guards on</dt><dd>${esc(r.config)}</dd></dl></section>
      </div></div>`;
    if (window.matchMedia("(max-width: 960px)").matches) $("#out").scrollIntoView({ block: "start", behavior: "auto" });
  }

  function blockedBody(r) {
    const ran = ["sandbox", "output_guard", "execution"].includes(r.blocked_by);
    return `<div class="empty"><h2>No rows returned</h2><p>${ran ? "The query reached the database, which refused or failed it." : "Nothing was executed against the database."}</p></div>`;
  }

  function tableHTML(r) {
    if (!r.columns.length) return `<div class="empty"><h2>No columns</h2></div>`;
    if (!r.rows.length) return `<div class="empty"><h2>The query ran and matched no rows</h2></div>`;
    const masked = new Set(r.masked_columns || []);
    const head = r.columns.map((c) => `<th scope="col">${esc(c)}${masked.has(c) ? '<span class="tag">masked</span>' : ""}</th>`).join("");
    const body = r.rows.map((row) => `<tr>${row.map((v, i) => {
      const isNum = typeof v === "number";
      const cls = masked.has(r.columns[i]) ? "masked" : isNum ? "n" : "";
      return `<td class="${cls}">${v === null ? '<span class="muted">null</span>' : esc(isNum && !Number.isInteger(v) ? v.toLocaleString(undefined, { maximumFractionDigits: 2 }) : v)}</td>`;
    }).join("")}</tr>`).join("");
    const shown = r.rows.length;
    return `<p class="small muted" style="margin-bottom:8px">${shown < r.row_count ? `Showing the first ${shown} of ${r.row_count} rows${state.demo ? " (the recording keeps 12)" : ""}.` : `${r.row_count} row${r.row_count === 1 ? "" : "s"}.`}${masked.size ? " Masked values were replaced by the output guard before they reached this page." : ""}</p>
      <div class="tablewrap" tabindex="0" role="region" aria-label="Query result rows"><table class="data"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }

  // ---------------------------------------------------------------- audit
  async function viewAudit() {
    const entries = (await api.audit()).slice().reverse();
    main.innerHTML = pageHead("Audit log",
      state.demo ? "The audit entries the service wrote for the recorded requests." : "Every request the service has handled, newest first, with the guard that decided it.") + `
    <form class="filters" id="af" aria-label="Filter the audit log" onsubmit="return false">
      <div><label class="field-label" for="f-dec">Decision</label><select id="f-dec" class="input"><option value="">All</option><option>allow</option><option>block</option><option>error</option></select></div>
      <div><label class="field-label" for="f-rule">Rule</label><select id="f-rule" class="input"><option value="">All</option>${[...new Set(entries.map((e) => e.rule_id).filter(Boolean))].sort().map((x) => `<option>${esc(x)}</option>`).join("")}</select></div>
      <div><label class="field-label" for="f-role">Role</label><select id="f-role" class="input"><option value="">All</option><option>analyst</option><option>manager</option><option>admin</option></select></div>
      <div><label class="field-label" for="f-q">Search text</label><input id="f-q" class="input" type="search" placeholder="question or SQL"></div>
      <button type="button" class="btn ghost" id="f-clear">Clear</button>
    </form>
    <p class="small muted" id="acount" role="status"></p>
    <div id="alist"></div>`;
    const draw = () => {
      const d = $("#f-dec").value, ru = $("#f-rule").value, ro = $("#f-role").value, q = $("#f-q").value.toLowerCase();
      const rows = entries.filter((e) => (!d || e.decision === d) && (!ru || e.rule_id === ru) && (!ro || e.role === ro) &&
        (!q || `${e.question || ""} ${e.sql_generated || ""}`.toLowerCase().includes(q)));
      $("#acount").textContent = `${rows.length} of ${entries.length} entries`;
      if (!entries.length) { $("#alist").innerHTML = `<div class="empty"><h2>No requests yet</h2><p>Run something in the console and it will be logged here.</p></div>`; return; }
      if (!rows.length) { $("#alist").innerHTML = `<div class="empty"><h2>No entries match these filters</h2><p>Clear a filter to see more.</p></div>`; return; }
      $("#alist").innerHTML = `<div class="audit-wrap" tabindex="0" role="region" aria-label="Audit entries"><table class="audit"><thead><tr>
        <th scope="col">Time</th><th scope="col">Role</th><th scope="col">Decision</th><th scope="col" class="hide-sm">Stopped by</th><th scope="col">Request</th><th scope="col"><span class="sr-only">Details</span></th></tr></thead><tbody>` +
        rows.map((e, i) => {
          const t = new Date(e.ts);
          const lat = (e.guard_results || []).map((g) => `<li>${esc(g.layer)}: ${esc(g.decision)}, ${fmtMs(g.latency_ms)}${g.rule_id ? ` (<code>${esc(g.rule_id)}</code>)` : ""}</li>`).join("");
          return `<tr class="main"><td class="num">${esc(isNaN(t) ? e.ts : t.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "medium" }))}</td>
            <td>${esc(e.role)}${e.region ? `<span class="muted">, ${esc(e.region)}</span>` : ""}</td>
            <td><span class="chip ${esc(e.decision)}">${esc(e.decision)}</span></td>
            <td class="hide-sm">${e.blocked_by ? `${esc(e.blocked_by)}<br><code class="muted">${esc(e.rule_id || "")}</code>` : '<span class="muted">none</span>'}</td>
            <td class="q">${esc(e.question || e.sql_generated || "")}</td>
            <td><button class="exp" type="button" aria-expanded="false" aria-controls="d${i}">Details</button></td></tr>
            <tr class="detail" id="d${i}" hidden><td colspan="6"><dl class="kv"><dt>Request id</dt><dd class="code">${esc(e.request_id)}</dd>
            <dt>SQL</dt><dd>${e.sql_generated ? `<div class="sqlbox"><pre><code>${highlightSQL(e.sql_generated)}</code></pre></div>` : '<span class="muted">none produced</span>'}</dd>
            ${e.reason ? `<dt>Reason</dt><dd>${esc(ruleTitle(e.rule_id))}. ${esc(e.reason)}</dd>` : ""}
            <dt>Stages</dt><dd><ul class="list-clean">${lat || "<li>none ran</li>"}</ul></dd></dl></td></tr>`;
        }).join("") + `</tbody></table></div>`;
      $("#alist").querySelectorAll("button.exp").forEach((b) => b.addEventListener("click", () => {
        const row = document.getElementById(b.getAttribute("aria-controls"));
        const open = row.hidden; row.hidden = !open; b.setAttribute("aria-expanded", String(open)); b.textContent = open ? "Hide" : "Details";
      }));
    };
    $("#af").addEventListener("input", draw);
    $("#f-clear").addEventListener("click", () => { $("#af").reset(); draw(); $("#f-dec").focus(); });
    draw();
  }

  // ---------------------------------------------------------------- policy
  async function viewPolicy() {
    const cat = await api.catalog();
    main.innerHTML = pageHead("What each role may see",
      "Default deny: a column that is not listed for a role does not exist for it. Masked columns can be shown, but only as masked values.") + `
    <fieldset class="roles" id="rolepick"><legend class="sr-only">Role</legend>${cat.roles.map((r, i) => `
      <input type="radio" name="prole" id="pr-${esc(r.name)}" value="${esc(r.name)}"${i === 0 ? " checked" : ""}>
      <label for="pr-${esc(r.name)}"><strong>${esc(r.name)}</strong><span class="muted small">${esc(r.description)}</span></label>`).join("")}</fieldset>
    <div id="pbody" aria-live="polite"></div>`;
    const draw = async () => {
      const name = document.querySelector('input[name="prole"]:checked').value;
      $("#pbody").innerHTML = `<div class="skeleton" role="status"><span class="sr-only">Loading</span></div>`;
      let p;
      try { p = await api.policy(name); } catch (e) { $("#pbody").innerHTML = `<div class="errbox" role="alert">${esc(e.message)}</div>`; return; }
      let vis = 0, tot = 0, mk = 0;
      const cards = Object.entries(cat.schema).map(([t, cols]) => {
        const plain = new Set(p.plain_columns[t] || []);
        const masked = (p.masked_columns && p.masked_columns[t]) || {};
        const n = cols.filter((c) => plain.has(c) || masked[c]).length;
        vis += n; tot += cols.length; mk += Object.keys(masked).length;
        const items = cols.map((c) => {
          const k = masked[c] ? "masked" : plain.has(c) ? "plain" : "hidden";
          const sr = k === "masked" ? ` <span class="sr-only">(masked as ${esc(masked[c])})</span>` : k === "hidden" ? ' <span class="sr-only">(hidden)</span>' : "";
          return `<li class="col ${k}" ${k === "masked" ? `title="shown masked as ${esc(masked[c])}"` : ""}>${esc(c)}${sr}</li>`;
        }).join("");
        return `<section class="tcard${n === 0 ? " gone" : ""}"><h3>${esc(t)}<span>${n === 0 ? "no access" : `${n} of ${cols.length} columns`}</span></h3><ul class="cols">${items}</ul></section>`;
      }).join("");
      $("#pbody").innerHTML = `<div class="reveal">
        <p class="section-gap"><b>${esc(p.role)}</b>: ${esc(p.row_scope === "region" ? "rows are limited to one region by row-level security." : "all rows within the visible columns.")} Sees ${vis} of ${tot} columns across ${p.visible_tables.length} of ${Object.keys(cat.schema).length} tables${mk ? `, ${mk} of them masked` : ""}.</p>
        <div class="legend" aria-label="Legend"><span><span class="col">column</span> visible</span><span><span class="col masked">column</span> shown masked</span><span><span class="col hidden">column</span> hidden</span></div>
        <div class="tables">${cards}</div>
        <section class="card" style="margin-top:24px"><h2>Limits that apply to every role</h2><dl class="kv">${Object.entries(cat.limits).map(([k, v]) => `<dt>${esc(k.replace(/_/g, " "))}</dt><dd class="num">${esc(Number(v).toLocaleString())}</dd>`).join("")}</dl></section></div>`;
    };
    $("#rolepick").addEventListener("change", draw);
    await draw();
  }

  // ---------------------------------------------------------------- safety
  const pct = (x, d = 1) => (x == null ? "n/a" : (x * 100).toFixed(d) + "%");
  const cfgName = (c) => (c === "none" ? "No guards" : c === "full" ? "All six layers" : c.startsWith("only:") ? "Only " + c.slice(5).replace(/_/g, " ") : c.startsWith("without:") ? "All but " + c.slice(8).replace(/_/g, " ") : c);
  async function viewSafety() {
    const R = await api.results();
    const sp = R.splits, ho = sp.heldout, dv = sp.dev;
    const ci = (s) => {
      const f = s.false_block, max = 0.15;
      const l = (f.ci_lo / max) * 100, w = Math.max(0.8, ((f.ci_hi - f.ci_lo) / max) * 100), x = Math.min(99, (f.rate / max) * 100);
      return `<div class="ci" role="img" aria-label="False-block rate ${pct(f.rate)}, 95 percent interval ${pct(f.ci_lo)} to ${pct(f.ci_hi)}"><span class="range" style="left:${l}%;width:${w}%"></span><span class="pt" style="left:calc(${x}% - 2px)"></span></div><div class="ci-scale" aria-hidden="true"><span>0%</span><span>5%</span><span>10%</span><span>15%</span></div>`;
    };
    const card = (name, s, note) => `<section class="card"><h2>${name}: benign queries wrongly refused</h2><span class="big">${pct(s.false_block.rate)}</span>
      <p class="muted">${s.false_blocks.length} of ${s.n_benign} benign queries, 95% interval [${pct(s.false_block.ci_lo)}, ${pct(s.false_block.ci_hi)}]</p>${ci(s)}<p class="small muted">${note}</p></section>`;
    const maxLat = Math.max(...ho.latency_per_layer.map((l) => l.median_ms || 0));
    const lat = (s) => s.latency_per_layer.filter((l) => l.n > 0 && l.median_ms != null).map((l) => `<tr><th scope="row">${esc(l.scope.replace(/_/g, " "))}</th><td class="n"><span class="hbar" style="width:${Math.max(2, (l.median_ms / maxLat) * 80)}px"></span>${l.median_ms.toFixed(2)}</td><td class="n">${l.p95_ms.toFixed(2)}</td><td class="n">${l.n}</td></tr>`).join("");
    const e = (s) => s.end_to_end;
    main.innerHTML = pageHead("Safety and results", `Every number on this page is read from <code>eval/results</code> (collected by <code>scripts/build_web_data.py</code>). Nothing is typed by hand.`) + `
    <section class="notice" aria-labelledby="nm"><h2 id="nm">The attack block rate was not measured</h2>
      <p>The evaluation contains <b>${dv.n_attacks + ho.n_attacks} attack items</b> in either split. So there is no block rate, no per-category rate and no per-layer attack ablation for this project, and none is claimed. What was measured is the opposite error, how often a legitimate query is refused, and how much time the guards add.</p></section>
    <div class="grid2">${card("Held-out", ho, `${ho.n_benign} benign queries. Lightly held-out, not strictly blind: the plan-cost ceiling was tuned on the dev split, and I cannot prove the held-out items were never looked at.`)}
      ${card("Dev", dv, `${dv.n_benign} benign queries, used while tuning, so this number is optimistic. A result of 0 out of ${dv.n_benign} has a degenerate interval.`)}</div>

    <section class="card section-gap"><h2>The ${ho.false_blocks.length} held-out queries that were wrongly refused</h2><div class="scroll-x"><table class="plain"><thead><tr><th scope="col">Item</th><th scope="col">Layer</th><th scope="col">Rule</th><th scope="col">Reason</th></tr></thead><tbody>
      ${ho.false_blocks.map((b) => `<tr><td>${esc(b.id)}</td><td>${esc(b.layer)}</td><td><code>${esc(b.rule_id)}</code></td><td>${esc(b.reason)}</td></tr>`).join("")}</tbody></table></div>
      <p class="small muted" style="margin-top:12px">heldout-benign-035 also fails in Postgres with every guard off, so it is partly a labelling problem in the item rather than a pure guard decision. The other two are genuine over-blocking by the policy.</p></section>

    <div class="grid2">
      <section class="card"><h2>Time added by the guards</h2>
        <p class="big" style="font-size:44px">${e(ho).median_overhead_ms} ms</p>
        <p class="muted">median overhead on the held-out split: ${e(ho).full_median_ms.toFixed(2)} ms with all guards, ${e(ho).none_median_ms.toFixed(2)} ms without (p95 ${e(ho).full_p95_ms.toFixed(1)} and ${e(ho).none_p95_ms.toFixed(1)} ms). On dev the overhead is ${e(dv).median_overhead_ms} ms. Benign queries, cache bypassed, local Postgres, mock provider, so no model latency is included.</p></section>
      <section class="card"><h2>Per-stage latency, held-out (ms)</h2><div class="scroll-x"><table class="plain"><thead><tr><th scope="col">Stage</th><th scope="col" class="n">Median</th><th scope="col" class="n">p95</th><th scope="col" class="n">n</th></tr></thead><tbody>${lat(ho)}</tbody></table></div></section></div>

    <section class="card section-gap"><h2>Benign false blocks under each layer configuration</h2>
      <p class="small muted" style="margin-bottom:12px">Each layer alone, and the full pipeline minus each layer. The attack rows of this ablation are empty because there are no attack items.</p>
      <div class="scroll-x"><table class="plain"><thead><tr><th scope="col">Configuration</th><th scope="col" class="n">Dev (n=${dv.n_benign})</th><th scope="col" class="n">Held-out (n=${ho.n_benign})</th></tr></thead><tbody>
      ${dv.ablation_benign.map((a, i) => `<tr><th scope="row">${esc(cfgName(a.config))}</th><td class="n">${a.false_blocks}</td><td class="n">${ho.ablation_benign[i].false_blocks}</td></tr>`).join("")}</tbody></table></div></section>

    <section class="card"><h2>What this page does not show</h2><ul class="list-clean">
      <li><b>No live LLM was used.</b> Only the deterministic mock provider ran; the Anthropic and Gemini providers are unit-tested against fake HTTP clients.</li>
      <li><b>No attack evaluation.</b> No block rate, interval, or per-layer attack ablation exists.</li>
      <li><b>Spider and BIRD were not run,</b> so the accuracy cost of the guards is unmeasured.</li>
      <li><b>The verifier and self-correction loop were not evaluated.</b></li>
      <li>One database engine (PostgreSQL 16) and one synthetic schema.</li>
      <li>Test suite when these results were generated: ${R.tests ? `${R.tests.passed} passed, ${R.tests.failed} failed, ${R.tests.skipped} skipped` : "unknown"}. Tests check rule behaviour on fixed examples. They are not a block rate.</li></ul></section>`;
  }

  init();
})();
