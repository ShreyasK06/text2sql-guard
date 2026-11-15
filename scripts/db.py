"""Manage the local demo database (docker compose + seed).

    python scripts/db.py up       start the container, wait until usable, seed if empty
    python scripts/db.py seed     re-seed (TRUNCATE + reload, deterministic)
    python scripts/db.py down     stop the container (data volume kept)
    python scripts/db.py reset    down -v, up, seed (re-runs db/init/*.sql)
    python scripts/db.py status   container state and row counts
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

CONTAINER = "t2s-postgres"
TABLES = ("users", "employees", "products", "orders", "order_items", "payments", "support_tickets", "api_keys")


def _die(msg: str, code: int = 1) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(code)


def _docker(*args: str, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    if shutil.which("docker") is None:
        _die("docker is not installed or not on PATH. Install Docker Desktop (or run your own PostgreSQL 16 "
             "and apply db/init/*.sql in order, then python -m db.seed).")
    try:
        return subprocess.run(["docker", *args], cwd=REPO_ROOT, check=check, text=True,
                              capture_output=capture)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip()
        _die(f"docker {' '.join(args)} failed (is Docker Desktop running?) {detail}")
    raise AssertionError  # unreachable


def _health() -> str:
    res = _docker("inspect", "--format", "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
                  CONTAINER, check=False, capture=True)
    return res.stdout.strip() if res.returncode == 0 else "missing"


def _connect_owner():
    import psycopg

    from app.config import get_settings

    return psycopg.connect(get_settings().owner_dsn(), connect_timeout=3)


def wait_ready(timeout_s: int = 120) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline and _health() != "healthy":
        time.sleep(1)
    if _health() != "healthy":
        _die(f"container {CONTAINER} not healthy after {timeout_s}s; see: docker logs {CONTAINER}")
    last = None
    while time.time() < deadline:
        try:
            with _connect_owner() as conn:
                conn.execute("SELECT 1")
            return
        except Exception as exc:  # noqa: BLE001 - retry until the owner role can log in
            last = exc
            time.sleep(1)
    _die(f"t2s_owner cannot connect: {last}")


def tables_empty() -> bool:
    try:
        with _connect_owner() as conn:
            return conn.execute("SELECT count(*) FROM users").fetchone()[0] == 0
    except Exception:  # noqa: BLE001 - missing table means the schema did not load
        return True


def do_seed() -> None:
    from db.seed import main as seed_main

    seed_main()


def cmd_up(_: argparse.Namespace) -> None:
    _docker("compose", "up", "-d")
    wait_ready()
    if tables_empty():
        print("tables empty, seeding...")
        do_seed()
    else:
        print("database already seeded")
    print("database ready on port 54329")


def cmd_seed(_: argparse.Namespace) -> None:
    wait_ready(30)
    do_seed()


def cmd_down(_: argparse.Namespace) -> None:
    _docker("compose", "down")


def cmd_reset(args: argparse.Namespace) -> None:
    _docker("compose", "down", "-v")
    cmd_up(args)


def cmd_status(_: argparse.Namespace) -> None:
    state = _health()
    print(f"container {CONTAINER}: {state}")
    if state != "healthy":
        return
    try:
        with _connect_owner() as conn:
            for table in TABLES:
                n = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                print(f"  {table:16s} {n:>7d}")
    except Exception as exc:  # noqa: BLE001
        print(f"  cannot query as t2s_owner: {exc}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("up", cmd_up), ("seed", cmd_seed), ("down", cmd_down), ("reset", cmd_reset), ("status", cmd_status)):
        sub.add_parser(name).set_defaults(fn=fn)
    args = ap.parse_args(argv)
    args.fn(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
