import { useState } from "react";
import { useApp } from "../context/AppContext";
import { useLoad } from "../hooks";
import type { CatalogResponse } from "../lib/types";
import { ErrBox, PageHead, Skeleton } from "../components/States";

function RoleBody({ cat, role }: { cat: CatalogResponse; role: string }) {
  const { backend } = useApp();
  const { data: p, error, loading } = useLoad(() => backend.policy(role), [backend, role]);
  if (loading) return <Skeleton />;
  if (error || !p) return <ErrBox>{error || "No policy returned."}</ErrBox>;
  let vis = 0, tot = 0, mk = 0;
  const cards = Object.entries(cat.schema).map(([t, cols]) => {
    const plain = new Set(p.plain_columns[t] || []);
    const masked = p.masked_columns?.[t] || {};
    const n = cols.filter((c) => plain.has(c) || masked[c]).length;
    vis += n; tot += cols.length; mk += Object.keys(masked).length;
    return (
      <section key={t} className={`tcard${n === 0 ? " gone" : ""}`}>
        <h3>{t}<span>{n === 0 ? "no access" : `${n} of ${cols.length} columns`}</span></h3>
        <ul className="cols">
          {cols.map((c) => {
            const k = masked[c] ? "masked" : plain.has(c) ? "plain" : "hidden";
            return (
              <li key={c} className={`col ${k}`} title={k === "masked" ? `shown masked as ${masked[c]}` : undefined}>
                {c}
                {k === "masked" ? <span className="sr-only"> (masked as {masked[c]})</span> : null}
                {k === "hidden" ? <span className="sr-only"> (hidden)</span> : null}
              </li>
            );
          })}
        </ul>
      </section>
    );
  });
  return (
    <div className="reveal">
      <p className="section-gap">
        <b>{p.role}</b>: {p.row_scope === "region" ? "rows are limited to one region by row-level security." : "all rows within the visible columns."}{" "}
        Sees {vis} of {tot} columns across {p.visible_tables.length} of {Object.keys(cat.schema).length} tables{mk ? `, ${mk} of them masked` : ""}.
      </p>
      <div className="legend" aria-label="Legend">
        <span><span className="col">column</span> visible</span>
        <span><span className="col masked">column</span> shown masked</span>
        <span><span className="col hidden">column</span> hidden</span>
      </div>
      <div className="tables">{cards}</div>
      <section className="card" style={{ marginTop: 24 }}>
        <h2>Limits that apply to every role</h2>
        <dl className="kv">
          {Object.entries(cat.limits).map(([k, v]) => (
            <span key={k} style={{ display: "contents" }}>
              <dt>{k.replace(/_/g, " ")}</dt><dd className="num">{Number(v).toLocaleString()}</dd>
            </span>
          ))}
        </dl>
      </section>
    </div>
  );
}

export function PolicyPage() {
  const { backend } = useApp();
  const { data: cat, error, loading } = useLoad(() => backend.catalog(), [backend]);
  const [role, setRole] = useState("");
  const active = role || cat?.roles[0]?.name || "";
  return (
    <>
      <PageHead title="What each role may see">
        Default deny: a column that is not listed for a role does not exist for it. Masked columns can be shown, but only as masked values.
      </PageHead>
      {error ? <ErrBox>{error}</ErrBox> : null}
      {loading ? <Skeleton /> : null}
      {cat ? (
        <>
          <fieldset className="roles">
            <legend className="sr-only">Role</legend>
            {cat.roles.map((r) => (
              <span key={r.name} style={{ display: "contents" }}>
                <input type="radio" name="prole" id={`pr-${r.name}`} value={r.name} checked={active === r.name}
                  onChange={() => setRole(r.name)} />
                <label htmlFor={`pr-${r.name}`}>
                  <strong>{r.name}</strong><span className="muted small">{r.description}</span>
                </label>
              </span>
            ))}
          </fieldset>
          <div aria-live="polite">{active ? <RoleBody cat={cat} role={active} /> : null}</div>
        </>
      ) : null}
    </>
  );
}
