"""Least privilege at the prompt: only tables and columns the role may see, ranked by relevance."""
from __future__ import annotations

import re

SYNONYMS = {
    "customer": "users", "customers": "users", "user": "users", "signup": "users", "signups": "users",
    "revenue": "orders", "sales": "orders", "order": "orders", "spend": "orders", "spent": "orders",
    "product": "products", "item": "order_items", "items": "order_items", "quantity": "order_items",
    "category": "products", "staff": "employees", "employee": "employees", "headcount": "employees",
    "hire": "employees", "hires": "employees", "department": "employees", "salary": "employees",
    "ticket": "support_tickets", "tickets": "support_tickets", "complaint": "support_tickets",
    "payment": "payments", "payments": "payments", "refund": "payments", "paid": "payments",
}
NEIGHBOURS = {
    "products": ["order_items"],
    "users": ["orders"],
    "order_items": ["orders", "products"],
    "payments": ["orders"],
    "support_tickets": ["users"],
}


def retrieve_schema(question: str, role, policy, k: int = 4) -> dict[str, list[str]]:
    visible = role.visible_schema()
    words = set(re.findall(r"[a-z_]+", question.lower()))
    scores: dict[str, int] = {}
    for table, cols in visible.items():
        s = 0
        if table in words or table.rstrip("s") in words:
            s += 3
        s += sum(2 for w in words if SYNONYMS.get(w) == table)
        s += sum(1 for c in cols if c in words)
        scores[table] = s
    ranked = [t for t in sorted(visible, key=lambda t: (-scores[t], t)) if scores[t] > 0]
    if not ranked:
        ranked = sorted(visible)
    chosen: list[str] = []
    for t in ranked:
        for cand in [t, *NEIGHBOURS.get(t, [])]:
            if cand in visible and cand not in chosen and len(chosen) < k:
                chosen.append(cand)
    return {t: list(visible[t]) for t in chosen}
