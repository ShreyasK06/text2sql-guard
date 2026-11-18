import type { ReactNode } from "react";

export function Skeleton({ label = "Loading", variant = "block" }: { label?: string; variant?: "block" | "result" }) {
  if (variant === "result") {
    return (
      <div className="stack" role="status" aria-busy="true">
        <span className="sr-only">{label}</span>
        <div className="skeleton tall" aria-hidden="true" />
        <div className="skeleton" aria-hidden="true" />
        <div className="skeleton lines" aria-hidden="true"><i /><i /><i /></div>
      </div>
    );
  }
  return (
    <div className="skeleton" role="status" aria-busy="true">
      <span className="sr-only">{label}</span>
    </div>
  );
}

export function ErrBox({ title, children, onRetry }: { title?: string; children: ReactNode; onRetry?: () => void }) {
  return (
    <div className="errbox" role="alert">
      {title ? <b>{title} </b> : null}
      {children}
      {onRetry ? <button type="button" className="btn ghost sm" onClick={onRetry}>Try again</button> : null}
    </div>
  );
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      <h2>{title}</h2>
      {children ? <p>{children}</p> : null}
      {action ? <div className="empty-action">{action}</div> : null}
    </div>
  );
}

export function PageHead({ title, children }: { title: string; children: ReactNode }) {
  return (
    <header className="page-head">
      <h1 tabIndex={-1}>{title}</h1>
      <p>{children}</p>
    </header>
  );
}
