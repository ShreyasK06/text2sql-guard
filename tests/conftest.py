"""Shared fixtures. DB-backed tests are marked `db` and skipped when PostgreSQL is unreachable."""
from __future__ import annotations

import os

# Tests must never read a developer's real .env (it may hold a live provider and key).
os.environ.setdefault("T2S_NO_DOTENV", "1")

import pytest

from app.config import get_settings


def _db_reachable() -> bool:
    try:
        import psycopg

        with psycopg.connect(get_settings().owner_dsn(), connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


_DB_UP: bool | None = None


def pytest_collection_modifyitems(config, items):
    global _DB_UP
    if not any("db" in item.keywords for item in items):
        return
    if _DB_UP is None:
        _DB_UP = _db_reachable()
    if not _DB_UP:
        skip = pytest.mark.skip(reason="PostgreSQL not reachable (run: python scripts/db.py up)")
        for item in items:
            if "db" in item.keywords:
                item.add_marker(skip)


@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest.fixture(scope="session")
def policy():
    from app.guards.policy import load_policy

    return load_policy()


@pytest.fixture(scope="session")
def analyst(policy):
    return policy.role("analyst")


@pytest.fixture(scope="session")
def manager(policy):
    return policy.role("manager")


@pytest.fixture(scope="session")
def admin(policy):
    return policy.role("admin")


@pytest.fixture(scope="session")
def executor(settings, policy):
    from app.execution.executor import PostgresExecutor

    ex = PostgresExecutor(settings, policy.limits)
    yield ex
    ex.close()
