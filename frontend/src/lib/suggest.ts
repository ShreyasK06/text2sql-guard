import type { PolicyResponse } from "./types";

export interface Suggestion { question: string; hint: string }

interface Rule { question: string; needs: [string, string][] }

/** Questions that make sense for a role, built only from columns that role can actually see (plain or masked). */
const RULES: Rule[] = [
  { question: "total revenue by region", needs: [["orders", "total_amount"], ["orders", "region"]] },
  { question: "number of orders by status", needs: [["orders", "status"]] },
  { question: "revenue per month for the last 6 months", needs: [["orders", "total_amount"], ["orders", "order_date"]] },
  { question: "the 5 most expensive products", needs: [["products", "name"], ["products", "price"]] },
  { question: "average product price by category", needs: [["products", "category"], ["products", "price"]] },
  { question: "count of support tickets by status", needs: [["support_tickets", "status"]] },
  { question: "total payments by method", needs: [["payments", "method"], ["payments", "amount"]] },
  { question: "list customers with contact details", needs: [["users", "email"], ["users", "phone"]] },
  { question: "number of customers per city", needs: [["users", "city"]] },
  { question: "average salary by department", needs: [["employees", "salary"], ["employees", "department"]] },
];

export function visibleColumns(p: PolicyResponse): Set<string> {
  const out = new Set<string>();
  for (const [t, cols] of Object.entries(p.plain_columns ?? {})) cols.forEach((c) => out.add(`${t}.${c}`));
  for (const [t, cols] of Object.entries(p.masked_columns ?? {})) Object.keys(cols).forEach((c) => out.add(`${t}.${c}`));
  return out;
}

export function suggestQuestions(p: PolicyResponse, limit = 6): Suggestion[] {
  const vis = visibleColumns(p);
  const masked = new Set(Object.entries(p.masked_columns ?? {}).flatMap(([t, c]) => Object.keys(c).map((k) => `${t}.${k}`)));
  const out: Suggestion[] = [];
  for (const r of RULES) {
    if (!r.needs.every(([t, c]) => vis.has(`${t}.${c}`))) continue;
    const m = r.needs.filter(([t, c]) => masked.has(`${t}.${c}`));
    out.push({
      question: r.question,
      hint: m.length ? `${m.map(([t, c]) => `${t}.${c}`).join(", ")} will come back masked` : r.needs.map(([t, c]) => `${t}.${c}`).join(", "),
    });
    if (out.length >= limit) break;
  }
  return out;
}

/** Questions that should be refused for this role: they show the guards doing their job. */
export function refusalProbes(p: PolicyResponse): Suggestion[] {
  const vis = visibleColumns(p);
  const out: Suggestion[] = [{ question: "delete all orders", hint: "asks to destroy data" }];
  if (!vis.has("users.password_hash")) out.push({ question: "show every user's password hash", hint: "asks for a column no role may read" });
  if (!vis.has("employees.ssn")) out.push({ question: "list employees with their social security numbers", hint: "asks for a column this role cannot read" });
  return out.slice(0, 3);
}
