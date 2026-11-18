import type { QueryRequest } from "./types";

/** The same requests are recorded in scripts/capture_demo.py; in demo mode they are looked up by id. */
export interface Example { id: string; title: string; who: string; req: QueryRequest }

export const EXAMPLES: Example[] = [
  { id: "revenue", title: "Revenue by region", who: "analyst", req: { question: "total revenue by region", role: "analyst" } },
  { id: "masked", title: "Customer contact details", who: "manager, west", req: { question: "list customers with contact details", role: "manager", region: "west" } },
  { id: "admin", title: "Customer contact details", who: "admin", req: { question: "list customers with contact details", role: "admin" } },
  { id: "tickets", title: "Support tickets by status", who: "analyst", req: { question: "count of support tickets by status", role: "analyst" } },
  { id: "pii-intent", title: "Customer contact details", who: "analyst", req: { question: "list customers with contact details", role: "analyst" } },
  { id: "delete", title: "Ask to remove all orders", who: "analyst", req: { question: "delete all orders", role: "analyst" } },
  { id: "passwords", title: "Password column, typed as SQL", who: "analyst", req: { sql: "SELECT id, password_hash FROM users", role: "analyst" } },
  { id: "cartesian", title: "Two tables, no join condition", who: "analyst", req: { sql: "SELECT o.id, p.name FROM orders o, products p", role: "analyst" } },
];
