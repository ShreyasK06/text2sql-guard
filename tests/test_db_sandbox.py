"""Database-level sandbox: grants, RLS, read-only, timeout, revoked functions, seed sanity.

Every test connects directly with psycopg as the real database role. Skipped when the
database is down (see tests/conftest.py).
"""
from __future__ import annotations

import json
import re
import time
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest

from app.config import get_settings

pytestmark = pytest.mark.db

SETTINGS = get_settings()
APP_ROLES = ("t2s_analyst", "t2s_manager", "t2s_admin")
SEED_DIR = Path(__file__).resolve().parent.parent / "db" / "seed_data"


@contextmanager
def connect(db_role: str):
    dsn = SETTINGS.owner_dsn() if db_role == SETTINGS.owner_user else SETTINGS.role_dsn(db_role)
    conn = psycopg.connect(dsn, autocommit=True)
    try:
        yield conn
    finally:
        conn.close()


def one(db_role: str, sql: str, params=None):
    with connect(db_role) as conn:
        return conn.execute(sql, params).fetchone()


def all_rows(db_role: str, sql: str):
    with connect(db_role) as conn:
        return conn.execute(sql).fetchall()


def luhn_ok(number: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(number)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


# ---------------------------------------------------------------- column grants

@pytest.mark.parametrize("sql", [
    "SELECT email FROM users LIMIT 1",
    "SELECT ssn FROM users LIMIT 1",
    "SELECT * FROM users LIMIT 1",
    "SELECT salary FROM employees LIMIT 1",
    "SELECT card_number FROM payments LIMIT 1",
    "SELECT secret FROM api_keys LIMIT 1",
    "SELECT * FROM api_keys",
])
def test_analyst_denied(sql):
    with connect("t2s_analyst") as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(sql)


def test_analyst_allowed_columns():
    assert one("t2s_analyst", "SELECT count(*) FROM users")[0] == 2000
    rows = all_rows("t2s_analyst", "SELECT id, city, state, region FROM users LIMIT 3")
    assert len(rows) == 3
    assert one("t2s_analyst", "SELECT count(*), sum(total_amount) FROM orders")[0] == 20000
    assert one("t2s_analyst", "SELECT count(*) FROM products")[0] == 200


@pytest.mark.parametrize("role", APP_ROLES)
def test_nobody_reads_api_keys_or_password_hash(role):
    with connect(role) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT id FROM api_keys")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT password_hash FROM users LIMIT 1")


def test_admin_reads_ssn_but_not_password_hash():
    ssn = one("t2s_admin", "SELECT ssn FROM users ORDER BY id LIMIT 1")[0]
    assert re.fullmatch(r"\d{3}-\d{2}-\d{4}", ssn)
    with connect("t2s_admin") as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("SELECT password_hash FROM users LIMIT 1")


def test_manager_masked_columns_are_db_readable():
    # the DB grant covers masked columns; the output guard masks them later
    with connect("t2s_manager") as conn:
        conn.execute("SELECT email, phone FROM users LIMIT 1")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT ssn FROM users LIMIT 1")


# ---------------------------------------------------------------- writes and DDL

WRITE_STATEMENTS = [
    "INSERT INTO products (id, name, category, price, description) VALUES (999999, 'x', 'x', 1, 'x')",
    "UPDATE products SET price = 0",
    "DELETE FROM products",
    "TRUNCATE products",
    "DROP TABLE products",
    "CREATE TABLE evil (id int)",
    "CREATE TEMP TABLE evil (id int)",
    "CREATE SCHEMA evil",
    "ALTER TABLE products ADD COLUMN x int",
]


@pytest.mark.parametrize("role", APP_ROLES)
@pytest.mark.parametrize("sql", WRITE_STATEMENTS)
def test_app_roles_cannot_write_or_ddl(role, sql):
    with connect(role) as conn, pytest.raises(psycopg.Error):
        conn.execute(sql)
    assert one("t2s_owner", "SELECT count(*) FROM products")[0] == 200


@pytest.mark.parametrize("role", APP_ROLES)
def test_session_defaults(role):
    with connect(role) as conn:
        def show(name):
            return conn.execute(f"SHOW {name}").fetchone()[0]

        assert show("default_transaction_read_only") == "on"
        assert show("transaction_read_only") == "on"
        assert show("statement_timeout") == "2s"
        assert show("idle_in_transaction_session_timeout") == "10s"
        assert show("lock_timeout") == "1s"


def test_owner_has_no_sandbox_settings():
    with connect("t2s_owner") as conn:
        assert conn.execute("SHOW default_transaction_read_only").fetchone()[0] == "off"
        assert conn.execute("SHOW statement_timeout").fetchone()[0] == "0"
        assert conn.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user").fetchone()[0] is False


def test_tables_owned_by_t2s_owner():
    owners = {r[0] for r in all_rows("t2s_owner", "SELECT tableowner FROM pg_tables WHERE schemaname = 'public'")}
    assert owners == {"t2s_owner"}


# ---------------------------------------------------------------- timeout

def test_statement_timeout_cancels_slow_query():
    with connect("t2s_analyst") as conn:
        start = time.perf_counter()
        with pytest.raises(psycopg.errors.QueryCanceled):
            conn.execute("SELECT count(*) FROM generate_series(1, 400000000) AS a")
        elapsed = time.perf_counter() - start
    # cancellation of a set-returning function is not prompt: observed 2-9 s under load for a 2 s timeout
    assert 1.5 <= elapsed <= 20.0, elapsed


# ---------------------------------------------------------------- revoked functions

@pytest.mark.parametrize("role", APP_ROLES)
@pytest.mark.parametrize("sql", [
    "SELECT pg_sleep(0.01)",
    "SELECT pg_sleep_for('10 milliseconds')",
    "SELECT set_config('app.current_region', 'west', false)",
    "SELECT pg_terminate_backend(pg_backend_pid())",
    "SELECT pg_cancel_backend(pg_backend_pid())",
    "SELECT pg_advisory_lock(1)",
    "SELECT pg_try_advisory_lock(1)",
    "SELECT query_to_xml('select 1', true, false, '')",
])
def test_dangerous_functions_denied_to_app_roles(role, sql):
    with connect(role) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(sql)


def test_owner_can_call_pg_sleep():
    with connect("t2s_owner") as conn:
        conn.execute("SELECT pg_sleep(0.01)")
        conn.execute("SELECT set_config('app.x', '1', false)")


# ---------------------------------------------------------------- row level security

def _owner_count(sql: str) -> int:
    return one("t2s_owner", sql)[0]


def test_manager_west_sees_only_west():
    with connect("t2s_manager") as conn:
        with conn.transaction():
            conn.execute("SET LOCAL app.current_region = 'west'")
            assert conn.execute("SELECT current_setting('app.current_region')").fetchone()[0] == "west"
            assert {r[0] for r in conn.execute("SELECT DISTINCT region FROM users")} == {"west"}
            assert {r[0] for r in conn.execute("SELECT DISTINCT region FROM orders")} == {"west"}
            assert {r[0] for r in conn.execute("SELECT DISTINCT region FROM employees")} == {"west"}
            got = {
                "users": conn.execute("SELECT count(*) FROM users").fetchone()[0],
                "orders": conn.execute("SELECT count(*) FROM orders").fetchone()[0],
                "employees": conn.execute("SELECT count(*) FROM employees").fetchone()[0],
                "payments": conn.execute("SELECT count(*) FROM payments").fetchone()[0],
                "order_items": conn.execute("SELECT count(*) FROM order_items").fetchone()[0],
                "support_tickets": conn.execute("SELECT count(*) FROM support_tickets").fetchone()[0],
                "products": conn.execute("SELECT count(*) FROM products").fetchone()[0],
            }
    expected = {
        "users": _owner_count("SELECT count(*) FROM users WHERE region = 'west'"),
        "orders": _owner_count("SELECT count(*) FROM orders WHERE region = 'west'"),
        "employees": _owner_count("SELECT count(*) FROM employees WHERE region = 'west'"),
        "payments": _owner_count("SELECT count(*) FROM payments p JOIN orders o ON o.id = p.order_id WHERE o.region = 'west'"),
        "order_items": _owner_count("SELECT count(*) FROM order_items i JOIN orders o ON o.id = i.order_id WHERE o.region = 'west'"),
        "support_tickets": _owner_count("SELECT count(*) FROM support_tickets t JOIN users u ON u.id = t.user_id WHERE u.region = 'west'"),
        "products": 200,
    }
    assert got == expected
    assert 0 < got["users"] < 2000


@pytest.mark.parametrize("table", ["users", "orders", "employees", "payments", "order_items", "support_tickets"])
def test_manager_sees_nothing_when_region_unset(table):
    assert one("t2s_manager", f"SELECT count(*) FROM {table}")[0] == 0


def test_manager_sees_nothing_when_region_empty_or_bogus():
    with connect("t2s_manager") as conn:
        with conn.transaction():
            conn.execute("SET LOCAL app.current_region = ''")
            assert conn.execute("SELECT count(*) FROM orders").fetchone()[0] == 0
        with conn.transaction():
            conn.execute("SET LOCAL app.current_region = 'nowhere'")
            assert conn.execute("SELECT count(*) FROM orders").fetchone()[0] == 0


def test_manager_set_local_inside_transaction_works():
    with connect("t2s_manager") as conn:
        with conn.transaction():
            conn.execute("SET LOCAL app.current_region = 'west'")
        # SET LOCAL is gone after the transaction
        assert conn.execute("SELECT count(*) FROM orders").fetchone()[0] == 0


@pytest.mark.parametrize("role", ["t2s_analyst", "t2s_admin"])
def test_analyst_and_admin_see_all_rows(role):
    assert one(role, "SELECT count(*) FROM orders")[0] == 20000
    assert one(role, "SELECT count(*) FROM users")[0] == 2000


def test_owner_bypasses_rls():
    assert one("t2s_owner", "SELECT count(*) FROM orders")[0] == 20000


# ---------------------------------------------------------------- seed sanity

def test_row_counts():
    expected = {"users": 2000, "employees": 300, "products": 200, "orders": 20000,
                "payments": 20000, "support_tickets": 3000, "api_keys": 10}
    for table, n in expected.items():
        assert _owner_count(f"SELECT count(*) FROM {table}") == n, table
    assert 55000 <= _owner_count("SELECT count(*) FROM order_items") <= 65000


def test_user_formats():
    rows = all_rows("t2s_owner", "SELECT email, phone, ssn, date_of_birth, password_hash, region FROM users")
    assert len({r[0] for r in rows}) == len(rows)
    for email, phone, ssn, dob, pw, region in rows:
        assert re.fullmatch(r"[a-z0-9]+\.[a-z0-9]+\d+@[\w.-]+", email), email
        assert re.fullmatch(r"\d{3}-\d{3}-\d{4}", phone)
        assert re.fullmatch(r"\d{3}-\d{2}-\d{4}", ssn)
        assert 1950 <= dob.year <= 2005
        assert re.fullmatch(r"\$2b\$12\$[A-Za-z0-9./]{53}", pw)
        assert region in {"north", "south", "east", "west"}
    assert {r[5] for r in rows} == {"north", "south", "east", "west"}


def test_employee_constraints():
    rows = all_rows("t2s_owner", "SELECT id, salary, ssn, manager_id, department FROM employees")
    assert sum(1 for r in rows if r[3] is None) in range(15, 26)
    for eid, salary, ssn, mgr, dept in rows:
        assert 40000 <= salary <= 250000
        assert re.fullmatch(r"\d{3}-\d{2}-\d{4}", ssn)
        assert mgr is None or mgr < eid
    assert {r[4] for r in rows} == {"Sales", "Support", "Engineering", "Finance", "Marketing", "Operations"}


def test_order_constraints():
    assert _owner_count("SELECT count(*) FROM orders o JOIN users u ON u.id = o.user_id WHERE o.region <> u.region") == 0
    assert _owner_count("SELECT count(*) FROM orders WHERE total_amount >= 20000") == 0
    assert _owner_count("SELECT count(*) FROM orders WHERE order_date NOT BETWEEN '2023-01-01' AND '2025-12-31'") == 0
    assert _owner_count("SELECT count(*) FROM orders o JOIN employees e ON e.id = o.sales_rep_id WHERE e.department <> 'Sales'") == 0
    assert _owner_count("SELECT count(*) FROM orders WHERE sales_rep_id IS NULL") == 0


def test_payment_cards_luhn():
    rows = all_rows("t2s_owner", "SELECT method, card_number, card_last4 FROM payments")
    assert any(r[0] == "card" for r in rows)
    for method, card, last4 in rows:
        if method == "card":
            assert re.fullmatch(r"\d{16}", card) and luhn_ok(card)
            assert last4 == card[-4:]
        else:
            assert card is None and last4 is None


def test_api_keys_format():
    for (secret,) in all_rows("t2s_owner", "SELECT secret FROM api_keys"):
        assert re.fullmatch(r"t2s_secret_[0-9a-f]{32}", secret)


def test_tickets_embed_owner_pii():
    rows = all_rows(
        "t2s_owner",
        "SELECT t.id, t.body, u.email, u.phone, u.ssn FROM support_tickets t JOIN users u ON u.id = t.user_id WHERE t.id > 50",
    )
    with_pii = sum(1 for _, body, email, phone, ssn in rows if email in body or phone in body or ssn in body
                   or re.search(r"\b4\d{15}\b", body))
    assert 0.08 * len(rows) <= with_pii <= 0.25 * len(rows)


def test_poison_rows_present():
    data = json.loads((SEED_DIR / "poison_dev.json").read_text(encoding="utf-8"))
    assert len(data["support_tickets"]) == 30 and len(data["products"]) == 5
    for row in data["support_tickets"]:
        body = one("t2s_owner", "SELECT body FROM support_tickets WHERE id = %s", (row["id"],))[0]
        assert row["payload"] in body
    for row in data["products"]:
        desc = one("t2s_owner", "SELECT description FROM products WHERE id = %s", (row["id"],))[0]
        assert row["payload"] in desc


def test_seed_generation_is_deterministic():
    from db import seed

    assert seed.generate() == seed.generate()
