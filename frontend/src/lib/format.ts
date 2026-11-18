export const fmtMs = (v: number | null | undefined): string =>
  v == null ? "" : v < 10 ? v.toFixed(2) + " ms" : v < 100 ? v.toFixed(1) + " ms" : Math.round(v) + " ms";

export const pct = (x: number | null | undefined, d = 1): string => (x == null ? "n/a" : (x * 100).toFixed(d) + "%");

export const plural = (n: number, one: string, many = one + "s"): string => `${n} ${n === 1 ? one : many}`;

export function cfgName(c: string): string {
  if (c === "none") return "No guards";
  if (c === "full") return "All six layers";
  if (c.startsWith("only:")) return "Only " + c.slice(5).replace(/_/g, " ");
  if (c.startsWith("without:")) return "All but " + c.slice(8).replace(/_/g, " ");
  return c;
}
