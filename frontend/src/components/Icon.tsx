const PATHS = {
  copy: "M9 9h10v11H9zM5 15V4h10",
  star: "M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1 5.9L12 16.9 6.8 19.7l1-5.9L3.5 9.7l5.9-.8z",
  download: "M12 4v10m0 0l-4-4m4 4l4-4M5 19h14",
  check: "M5 12.5l4.5 4.5L19 7.5",
  cross: "M6 6l12 12M18 6L6 18",
  minus: "M6 12h12",
  dot: "M12 12h.01",
  trash: "M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13",
  play: "M8 5l11 7-11 7z",
  sun: "M12 8a4 4 0 100 8 4 4 0 000-8zM12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M5 19l1.5-1.5M17.5 6.5L19 5",
  moon: "M20 14.5A8 8 0 019.5 4 8 8 0 1020 14.5z",
  auto: "M12 3a9 9 0 100 18V3z",
  alert: "M12 8v5m0 3h.01M12 3l10 18H2z",
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, filled = false }: { name: IconName; filled?: boolean }) {
  return (
    <svg className="icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"
      fill={filled ? "currentColor" : "none"} stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d={PATHS[name]} />
    </svg>
  );
}
