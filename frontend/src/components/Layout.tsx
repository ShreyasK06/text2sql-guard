import { useEffect, useRef } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { useApp } from "../context/AppContext";
import type { LlmStatus } from "../lib/types";
import { themeStore, useTheme, type Theme } from "../lib/prefs";
import { ErrBox, Skeleton } from "./States";
import { Icon } from "./Icon";

const NAV = [
  { to: "/console", label: "Console" },
  { to: "/history", label: "History" },
  { to: "/audit", label: "Audit log" },
  { to: "/policy", label: "Policy" },
  { to: "/safety", label: "Safety" },
  { to: "/about", label: "About" },
  { to: "/settings", label: "Settings" },
];

/** Page titles, used for document.title and announced by the moved focus. */
export const ROUTE_TITLES: Record<string, string> = {
  "/console": "Console",
  "/history": "History",
  "/audit": "Audit log",
  "/policy": "Policy",
  "/safety": "Safety and results",
  "/about": "About",
  "/settings": "Settings",
};

function ModeBadge() {
  const { mode, health } = useApp();
  let cls = "mode";
  let short = "Connecting";
  let full = "Connecting to the service";
  if (mode === "demo") { cls += " demo"; short = "Recorded demo"; full = "Recorded demo, not live"; }
  else if (mode === "offline") { cls += " off"; short = "Offline"; full = "Offline"; }
  else if (mode === "live" && health) {
    if (!health.db) cls += " off";
    short = health.db ? "Live" : "Live, database down";
    full = `Live, ${health.provider} provider, database ${health.db ? "up" : "DOWN"}`;
  }
  return (
    <div className={cls} role="status" aria-label={full} title={full}>
      <i aria-hidden="true" />
      <span aria-hidden="true">{short}</span>
    </div>
  );
}

export function providerLabel(llm: LlmStatus): string {
  if (llm.mode !== "live") return "Mock provider";
  const name = llm.provider === "nvidia" ? "NVIDIA" : llm.provider;
  return `${name} · ${llm.model ?? "model chosen from live list"}`;
}

function ProviderBadge() {
  const { mode, llm } = useApp();
  if (mode !== "live" || !llm) return null;
  return (
    <Link to="/settings" className="provider" aria-label={`${providerLabel(llm)}. ${llm.privacy_note} Open settings.`}>
      <span>{providerLabel(llm)}</span>
      <span className="tip" aria-hidden="true">{llm.privacy_note}</span>
    </Link>
  );
}

const NEXT: Record<Theme, Theme> = { system: "light", light: "dark", dark: "system" };
const THEME_ICON = { system: "auto", light: "sun", dark: "moon" } as const;

function ThemeButton() {
  const theme = useTheme();
  const next = NEXT[theme];
  return (
    <button type="button" className="iconbtn" onClick={() => themeStore.set(next)}
      aria-label={`Theme: ${theme}. Switch to ${next}.`} title={`Theme: ${theme}`}>
      <Icon name={THEME_ICON[theme]} />
      <span className="themename" aria-hidden="true">{theme}</span>
    </button>
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

function SiteFooter({ big }: { big: boolean }) {
  return (
    <footer className={big ? "sitefoot" : "minifoot"}>
      <div className="wrap">
        {big ? <span className="name">text2sql-guard</span> : null}
        <p>
          {big ? null : <b>text2sql-guard. </b>}
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
  const title = ROUTE_TITLES[pathname];

  useEffect(() => {
    document.title = title ? `${title} · text2sql-guard` : "text2sql-guard";
  }, [title]);

  useEffect(() => {
    if (first.current) { first.current = false; return; }
    window.scrollTo(0, 0);
    mainRef.current?.querySelector<HTMLElement>("h1")?.focus({ preventScroll: true });
  }, [pathname]);

  const ready = mode !== "loading" && mode !== "offline";
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
        <div className="status-cluster">
          <ModeBadge />
          <ProviderBadge />
          <ThemeButton />
        </div>
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
      {ready ? <SiteFooter big={wide} /> : null}
    </div>
  );
}
