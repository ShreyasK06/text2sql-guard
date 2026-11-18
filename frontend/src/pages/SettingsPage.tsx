import { useMemo, useState } from "react";
import { useApp } from "../context/AppContext";
import { useLoad } from "../hooks";
import { liveApi } from "../lib/api";
import { densityStore, themeStore, useDensity, useTheme, type Density, type Theme } from "../lib/prefs";
import { historyStore, savedStore, useHistory, useSaved } from "../lib/history";
import { ErrBox, PageHead, Skeleton } from "../components/States";
import { providerLabel } from "../components/Layout";

const THEMES: { v: Theme; label: string; note: string }[] = [
  { v: "system", label: "System", note: "follows your device" },
  { v: "light", label: "Light", note: "paper and ink" },
  { v: "dark", label: "Dark", note: "ink and verdigris" },
];
const DENSITIES: { v: Density; label: string; note: string }[] = [
  { v: "comfortable", label: "Comfortable", note: "more air" },
  { v: "compact", label: "Compact", note: "more rows on screen" },
];

function Choice<T extends string>({ name, legend, value, options, onChange }: {
  name: string; legend: string; value: T; options: { v: T; label: string; note: string }[]; onChange: (v: T) => void;
}) {
  return (
    <fieldset className="field-set">
      <legend className="field-label">{legend}</legend>
      <div className="roles choice">
        {options.map((o) => (
          <span key={o.v} style={{ display: "contents" }}>
            <input type="radio" name={name} id={`${name}-${o.v}`} value={o.v} checked={value === o.v} onChange={() => onChange(o.v)} />
            <label htmlFor={`${name}-${o.v}`}><strong>{o.label}</strong><span className="muted small">{o.note}</span></label>
          </span>
        ))}
      </div>
    </fieldset>
  );
}

function Models({ current }: { current: string | null }) {
  const { data, error, loading, reload } = useLoad(() => liveApi.llmModels(), []);
  const [q, setQ] = useState("");
  const list = useMemo(() => (data?.models ?? []).filter((m) => m.toLowerCase().includes(q.toLowerCase())), [data, q]);
  if (loading) return <Skeleton label="Loading the model list" />;
  if (error || !data) return <ErrBox onRetry={reload}>Could not load the model list. {error}</ErrBox>;
  if (!data.models.length) {
    return <p className="muted">{data.reason || "The provider did not return a model list."}</p>;
  }
  return (
    <>
      {data.reason ? <p className="small section-gap">{data.reason}</p> : null}
      <label className="field-label" htmlFor="m-q">Filter the {data.models.length} models</label>
      <input id="m-q" className="input" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="for example coder" />
      <ul className="models" tabIndex={0} aria-label="Models offered by the provider">
        {list.map((m) => (
          <li key={m} className={m === current ? "cur" : undefined}>
            <code>{m}</code>
            {m === current ? <span className="chip allow">in use</span> : null}
            {m === data.suggested && m !== current ? <span className="chip error">suggested</span> : null}
          </li>
        ))}
      </ul>
      {!list.length ? <p className="small muted">No model matches that filter.</p> : null}
    </>
  );
}

export function SettingsPage() {
  const { mode, llm, health } = useApp();
  const theme = useTheme();
  const density = useDensity();
  const history = useHistory();
  const saved = useSaved();
  const [done, setDone] = useState("");

  return (
    <>
      <PageHead title="Settings">
        How this app looks, which language model it is talking to, and what it keeps in your browser.
      </PageHead>

      <section className="card section-gap" aria-labelledby="ap">
        <h2 id="ap">Appearance</h2>
        <div className="stack">
          <Choice name="theme" legend="Theme" value={theme} options={THEMES} onChange={(v) => themeStore.set(v)} />
          <Choice name="density" legend="Density" value={density} options={DENSITIES} onChange={(v) => densityStore.set(v)} />
        </div>
        <p className="small muted" style={{ marginTop: 12 }}>Saved in this browser and applied before the page paints.</p>
      </section>

      <section className="card section-gap" aria-labelledby="lm">
        <h2 id="lm">Language model</h2>
        {mode === "demo" ? (
          <p>This is the recorded demo. No model is contacted and nothing you click is executed. Run the service locally to use a live model.</p>
        ) : !llm ? (
          <p className="muted">The service did not report provider details (an older API). It is running with the {health?.provider ?? "unknown"} provider.</p>
        ) : (
          <dl className="kv">
            <dt>Mode</dt><dd>{llm.mode === "live" ? <span className="chip allow">Live model</span> : <span className="chip error">Mock provider</span>}</dd>
            <dt>Provider</dt><dd>{providerLabel(llm)}</dd>
            <dt>Model</dt><dd className="code">{llm.model ?? "chosen from the live list"}</dd>
            <dt>Key</dt><dd>{llm.configured ? "A key is configured on the server. It is never sent to this page." : "No key configured."}</dd>
            {llm.detail ? <><dt>Detail</dt><dd>{llm.detail}</dd></> : null}
            <dt>Privacy</dt><dd>{llm.privacy_note}</dd>
          </dl>
        )}
      </section>

      {mode === "live" && llm && llm.mode === "live" ? (
        <section className="card section-gap" aria-labelledby="md">
          <h2 id="md">Models the provider offers</h2>
          <p className="small muted section-gap">
            Read-only. The model is chosen by the server when it starts, so it cannot be switched from this page.
          </p>
          <Models current={llm.model} />
        </section>
      ) : null}

      <section className="card section-gap" aria-labelledby="cf">
        <h2 id="cf">How to configure a model</h2>
        <p>Set these in the server's <code>.env</code> file (never in this app, and never commit the key), then restart the API:</p>
        <div className="codeblock"><pre>{`LLM_PROVIDER=nvidia
NVIDIA_API_KEY=<your key>
NVIDIA_MODEL=<a model name from the list above>`}</pre></div>
        <p className="small muted" style={{ marginTop: 12 }}>
          Other providers use their own key variable. Without a key the app runs the deterministic mock provider and nothing leaves your machine.
          Full steps are in the <a href="https://github.com/ShreyasK06/text2sql-guard#readme">project README</a>.
        </p>
      </section>

      <section className="card" aria-labelledby="dt">
        <h2 id="dt">Your data in this browser</h2>
        <p>
          {history.length} history {history.length === 1 ? "entry" : "entries"} and {saved.length} saved {saved.length === 1 ? "query" : "queries"}.
          Only the text you typed, the role, the SQL and the decision are stored. Result rows never are.
        </p>
        <div className="row" style={{ marginTop: 12 }}>
          <button type="button" className="btn ghost" disabled={!history.length}
            onClick={() => { historyStore.set([]); setDone("History cleared."); }}>Clear history</button>
          <button type="button" className="btn ghost" disabled={!saved.length}
            onClick={() => { savedStore.set([]); setDone("Saved queries cleared."); }}>Clear saved queries</button>
        </div>
        <p className="small muted" role="status" style={{ marginTop: 8 }}>{done}</p>
      </section>
    </>
  );
}
