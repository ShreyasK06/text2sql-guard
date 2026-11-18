"""Record real API responses for the static demo (frontend/public/data/demo.json).

Start the API first (mock provider, Postgres up):

    python -m uvicorn app.api.main:app --port 8077
    python scripts/capture_demo.py --base http://127.0.0.1:8077

Nothing here is hand-written output: every response is what the running service returned,
saved verbatim (rows are trimmed to the first 12 so the file stays small, and row_count keeps
the real count). Re-run it to refresh the recording.
"""
from __future__ import annotations

import argparse
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "frontend" / "public" / "data" / "demo.json"
KEEP_ROWS = 12

# (id, headline shown in the demo list, request body). Plain examples a person would type.
CASES = [
    ("revenue", "Revenue by region", {"question": "total revenue by region", "role": "analyst"}),
    ("masked", "Customer contact details, as a manager",
     {"question": "list customers with contact details", "role": "manager", "region": "west"}),
    ("pii-intent", "Customer contact details, as an analyst",
     {"question": "list customers with contact details", "role": "analyst"}),
    ("passwords", "Password column, SQL typed directly",
     {"sql": "SELECT id, password_hash FROM users", "role": "analyst"}),
    ("delete", "Ask to remove all orders",
     {"question": "delete all orders", "role": "analyst"}),
    ("admin", "Customer contact details, as an admin",
     {"question": "list customers with contact details", "role": "admin"}),
    ("cartesian", "Two tables joined with no condition",
     {"sql": "SELECT o.id, p.name FROM orders o, products p", "role": "analyst"}),
    ("tickets", "Support tickets by status",
     {"question": "count of support tickets by status", "role": "analyst"}),
]


def call(base: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data, {"content-type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8077")
    base = ap.parse_args().base.rstrip("/")

    health = call(base, "/health")
    if health["provider"] != "mock":
        raise SystemExit("refusing to record: provider is not 'mock'")
    catalog = call(base, "/catalog")
    out = {
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provider": health["provider"],
        "db_was_up": health["db"],
        "catalog": catalog,
        "policies": {r["name"]: call(base, f"/policy/{r['name']}") for r in catalog["roles"]},
        "cases": [],
    }
    for cid, title, body in CASES:
        res = call(base, "/query", body)
        res["rows"] = res["rows"][:KEEP_ROWS]
        out["cases"].append({"id": cid, "title": title, "request": body, "response": res})
        print(f"{cid:11} {res['decision']:6} {res['blocked_by'] or '-':14} {res['rule_id'] or '-':34} rows={res['row_count']}")
    # The audit entries those calls produced, straight from the service.
    out["audit"] = call(base, f"/audit?n={len(CASES)}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
