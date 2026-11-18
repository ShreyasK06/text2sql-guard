import type { ReactNode } from "react";

export function Skeleton({ label = "Loading" }: { label?: string }) {
  return (
    <div className="skeleton" role="status" aria-busy="true">
      <span className="sr-only">{label}</span>
    </div>
  );
}

export function ErrBox({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <div className="errbox" role="alert">
      {title ? <b>{title} </b> : null}
      {children}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <h2>{title}</h2>
      {children ? <p>{children}</p> : null}
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
