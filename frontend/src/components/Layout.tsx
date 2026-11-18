import { useEffect, useRef } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { useApp } from "../context/AppContext";
import { ErrBox, Skeleton } from "./States";

const NAV = [
  { to: "/console", label: "Console" },
  { to: "/audit", label: "Audit log" },
  { to: "/policy", label: "Policy" },
  { to: "/safety", label: "Safety and results" },
  { to: "/about", label: "About" },
];

function ModeBadge() {
  const { mode, health } = useApp();
  let cls = "mode";
  let text = "Connecting";
  if (mode === "demo") { cls += " demo"; text = "Recorded demo, not live"; }
  else if (mode === "offline") { cls += " off"; text = "Offline"; }
  else if (mode === "live" && health) {
    if (!health.db) cls += " off";
    text = `Live, ${health.provider} provider, database ${health.db ? "up" : "DOWN"}`;
  }
  return (
    <div className={cls} role="status">
      <i aria-hidden="true" />
      <span>{text}</span>
    </div>
  );
}

function Banner() {
  const { mode, demo, reason } = useApp();
  if (mode !== "demo" || !demo) return null;
  const d = new Date(demo.recorded_at);
  const when = d.toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" });
  return (
    <div role="region" aria-label="Demo notice">
      <div className="demo-banner" role="note">
        Recorded demo, not live.{" "}
        <span>
          These are real responses captured from the running service on {when} (mock provider, local Postgres).
          Nothing you click here is executed. {reason}
        </span>
      </div>
    </div>
  );
}

function SiteFooter() {
  return (
    <footer className="sitefoot">
      <div className="wrap">
        <span className="name">text2sql-guard</span>
        <p>
          MIT licensed. Source at{" "}
          <a href="https://github.com/ShreyasK06/text2sql-guard">github.com/ShreyasK06/text2sql-guard</a>. All data is synthetic.
        </p>
      </div>
    </footer>
  );
}

export function Layout() {
  const { mode, error, switchToDemo } = useApp();
  const { pathname } = useLocation();
  const mainRef = useRef<HTMLElement>(null);
  const first = useRef(true);
  const wide = pathname === "/about";

  useEffect(() => {
    if (first.current) { first.current = false; return; }
    window.scrollTo(0, 0);
    mainRef.current?.querySelector<HTMLElement>("h1")?.focus({ preventScroll: true });
  }, [pathname]);

  return (
    <div className={wide ? "site" : undefined}>
      <a
        className="skip"
        href="#view"
        onClick={(e) => { e.preventDefault(); mainRef.current?.focus(); }}
      >
        Skip to content
      </a>
      <Banner />
      <header className="topbar">
        <Link className="wordmark" to="/console" aria-label="text2sql-guard, console">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <rect x="1.5" y="1.5" width="21" height="21" rx="5" fill="currentColor" />
            <path d="M6 8h12M6 12h12M6 16h7" stroke="var(--paper)" strokeWidth="2" strokeLinecap="round" />
          </svg>
          text2sql-guard
        </Link>
        <nav className="nav" aria-label="Main">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to}>{n.label}</NavLink>
          ))}
        </nav>
        <ModeBadge />
      </header>
      <main id="view" ref={mainRef} tabIndex={-1} className={wide ? "wide" : undefined}>
        {mode === "loading" ? (
          <Skeleton label="Connecting" />
        ) : mode === "offline" ? (
          <ErrBox>
            {error}{" "}
            <button type="button" className="btn ghost" onClick={switchToDemo}>Open the recorded demo</button>
          </ErrBox>
        ) : (
          <Outlet />
        )}
      </main>
      {wide && mode !== "loading" && mode !== "offline" ? <SiteFooter /> : null}
    </div>
  );
}
