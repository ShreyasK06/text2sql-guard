"""Run every item of a split UNGUARDED as the table owner, in a rolled-back transaction.

Label QA only: it tells an item author whether the SQL is valid PostgreSQL for this schema
and how long it takes. It does not import or consult any guard code.

    python eval/adversarial/v1/check_unguarded.py dev
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import psycopg

HERE = Path(__file__).resolve().parent
TXN_WORDS = re.compile(
    r"\b(commit|end|rollback|abort|begin|start\s+transaction|prepare\s+transaction|savepoint)\b", re.I
)


def dsn() -> str:
    env = os.environ.get
    return (
        f"host={env('T2S_DB_HOST', '127.0.0.1')} port={env('T2S_DB_PORT', '54329')} "
        f"dbname={env('T2S_DB_NAME', 't2s')} user={env('T2S_OWNER_USER', 't2s_owner')} "
        f"password={env('T2S_OWNER_PASSWORD', 't2s_local_dev_only')} connect_timeout=5"
    )


def main() -> int:
    split = sys.argv[1] if len(sys.argv) > 1 else "dev"
    items = [json.loads(l) for l in (HERE / f"{split}.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    problems = 0
    with psycopg.connect(dsn(), autocommit=True) as conn:
        for it in items:
            sql = it["sql"]
            if TXN_WORDS.search(sql):
                print(f"{it['id']:<22} skipped-unsafe")
                continue
            status, rows, detail = "ok", 0, ""
            t0 = time.perf_counter()
            try:
                conn.execute("BEGIN")
                conn.execute("SET LOCAL statement_timeout = 3000")
                cur = conn.execute(sql)
                if cur.description:
                    rows = len(cur.fetchmany(5000))
            except psycopg.errors.QueryCanceled:
                status = "timeout"
            except psycopg.Error as exc:
                status, detail = "error", f"{exc.sqlstate} {str(exc).splitlines()[0]}"
            finally:
                conn.execute("ROLLBACK")
            ms = (time.perf_counter() - t0) * 1000
            bad = it["category"] == "benign" and (status != "ok" or ms > 1000)
            problems += bad
            print(f"{it['id']:<22} {status:<8} rows={rows:<5} {ms:7.0f} ms {'<-- FIX' if bad else ''} {detail}")
    print(f"\n{len(items)} items, {problems} benign items needing a fix")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
