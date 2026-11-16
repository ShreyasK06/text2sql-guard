"""Executor and EXPLAIN cost gate against the live PostgreSQL (skipped when it is down)."""
from __future__ import annotations

import psycopg
import pytest

from app.guards.cost_gate import check_cost
from app.types import Decision

pytestmark = pytest.mark.db


def _owner_count(settings, table: str) -> int:
    with psycopg.connect(settings.owner_dsn()) as conn:
        return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


# ------------------------------------------------------------------ sandbox on
def test_allowed_read(executor, analyst):
    r = executor.execute("SELECT status, count(*) FROM orders GROUP BY status", analyst, None)
    assert r.ok and r.columns == ["status", "count"] and len(r.rows) == 5


def test_denied_column_is_permission_error(executor, analyst):
    r = executor.execute("SELECT email FROM users LIMIT 1", analyst, None)
    assert not r.ok and r.error_kind == "permission"


def test_denied_table_is_permission_error(executor, admin):
    r = executor.execute("SELECT secret FROM api_keys", admin, None)
    assert not r.ok and r.error_kind == "permission"


def test_write_is_refused_and_nothing_changes(executor, analyst, settings):
    before = _owner_count(settings, "orders")
    r = executor.execute("DELETE FROM orders", analyst, None)
    assert not r.ok and r.error_kind in {"read_only", "permission"}
    assert _owner_count(settings, "orders") == before


def test_multi_statement_rejected(executor, analyst):
    r = executor.execute("SELECT 1; SELECT 2", analyst, None)
    assert not r.ok and r.error_kind == "multi_statement"


def test_percent_sign_in_sql(executor, analyst):
    r = executor.execute("SELECT count(*) FROM products WHERE name LIKE '%a%'", analyst, None)
    assert r.ok and r.rows[0][0] > 0


def test_row_cap(executor, analyst, policy):
    r = executor.execute("SELECT id FROM orders", analyst, None)
    assert r.ok and r.truncated and len(r.rows) == policy.limits.max_rows


def test_statement_timeout_then_recovers(executor, analyst, policy):
    r = executor.execute("SELECT count(*) FROM orders a, orders b, orders c", analyst, None)
    assert not r.ok and r.error_kind == "timeout"
    assert r.elapsed_ms < policy.limits.statement_timeout_ms * 2
    assert executor.execute("SELECT 1", analyst, None).ok


def test_pg_sleep_not_available_to_app_roles(executor, analyst):
    r = executor.execute("SELECT pg_sleep(10)", analyst, None)
    assert not r.ok and r.error_kind in {"permission", "timeout"} and r.elapsed_ms < 3000


def test_manager_rows_are_region_scoped(executor, manager):
    west = executor.execute("SELECT DISTINCT region FROM orders", manager, "west")
    east = executor.execute("SELECT DISTINCT region FROM orders", manager, "east")
    assert west.ok and [r[0] for r in west.rows] == ["west"]
    assert east.ok and [r[0] for r in east.rows] == ["east"]


def test_manager_without_region_is_refused(executor, manager):
    assert not executor.execute("SELECT count(*) FROM orders", manager, None).ok
    assert not executor.execute("SELECT count(*) FROM orders", manager, "mars").ok


def test_manager_cannot_change_region_inside_query(executor, manager):
    r = executor.execute("SELECT set_config('app.current_region', 'east', true)", manager, "west")
    assert not r.ok and r.error_kind == "permission"


# ----------------------------------------------------------------- sandbox off
def test_unsandboxed_runs_as_owner_but_rolls_back(executor, analyst, settings):
    before = _owner_count(settings, "api_keys")
    r = executor.execute("DELETE FROM api_keys", analyst, None, sandbox=False)
    assert r.ok and not r.sandboxed
    assert _owner_count(settings, "api_keys") == before


def test_unsandboxed_stacked_statements_return_last_result(executor, analyst):
    r = executor.execute("SELECT 1 AS a; SELECT 2 AS b", analyst, None, sandbox=False)
    assert r.ok and r.columns == ["b"] and r.rows == [(2,)]


@pytest.mark.parametrize("sql", ["COMMIT; DELETE FROM api_keys", "DELETE FROM api_keys; COMMIT", "END; DELETE FROM api_keys"])
def test_unsandboxed_commit_smuggling_cannot_persist(executor, analyst, settings, sql):
    before = _owner_count(settings, "api_keys")
    r = executor.execute(sql, analyst, None, sandbox=False)
    assert not r.ok
    assert _owner_count(settings, "api_keys") == before


def test_unsandboxed_watchdog(executor, analyst, settings):
    r = executor.execute("SELECT pg_sleep(30)", analyst, None, sandbox=False)
    assert not r.ok and r.error_kind == "watchdog"
    assert r.elapsed_ms < settings.watchdog_ms + 2000


# ------------------------------------------------------------------- cost gate
def test_cost_gate_allows_cheap_query(executor, analyst, policy):
    g = check_cost("SELECT status, count(*) FROM orders GROUP BY status", analyst, None, executor, policy.limits)
    assert g.decision == Decision.ALLOW and g.details["total_cost"] > 0


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT count(*) FROM orders a CROSS JOIN orders b",
        "SELECT a.id, b.id FROM orders a CROSS JOIN orders b LIMIT 1000",
        "SELECT count(*) FROM orders a, users b",
    ],
)
def test_cost_gate_blocks_cross_joins(executor, analyst, policy, sql):
    g = check_cost(sql, analyst, None, executor, policy.limits)
    assert g.blocked and g.rule_id in {"cost.too_expensive", "cost.too_many_rows"}


def test_cost_gate_fails_closed_and_never_executes(executor, analyst, policy, settings):
    before = _owner_count(settings, "api_keys")
    for sql in ["DROP TABLE api_keys", "SELECT 1; DELETE FROM api_keys"]:
        for sandbox in (True, False):
            g = check_cost(sql, analyst, None, executor, policy.limits, sandbox=sandbox)
            assert g.blocked and g.rule_id == "cost.explain_failed"
    assert _owner_count(settings, "api_keys") == before


def test_cost_gate_handles_comment_and_semicolon(executor, analyst, policy):
    g = check_cost("-- note\nSELECT count(*) FROM products;", analyst, None, executor, policy.limits)
    assert g.decision == Decision.ALLOW
