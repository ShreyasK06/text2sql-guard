"""HTTP API tests: offline with a fake pipeline, plus live tests against PostgreSQL."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.api.main import app, get_pipeline
from app.config import REPO_ROOT
from app.guards.policy import load_policy
from app.types import Decision, GuardResult, PipelineResult


class FakePipeline:
    def __init__(self):
        self.policy = load_policy()
        self.audit = None
        self.calls = []

    def run(self, question, role, region=None, *, sql_override=None, config=None):
        self.calls.append((question, role, region, sql_override, config))
        if sql_override and "email" in sql_override:
            return PipelineResult(
                request_id="r", role=role, region=region, question=question, decision="block",
                blocked_by="allowlist", rule_id="allowlist.column_denied", reason="denied",
                guard_results=[GuardResult("allowlist", Decision.BLOCK, "allowlist.column_denied", "denied")],
            )
        return PipelineResult(
            request_id="r", role=role, region=region, question=question, decision="allow",
            columns=["d", "amount", "n"], rows=[(dt.date(2024, 1, 2), Decimal("1.50"), None)],
        )


@pytest.fixture
def client():
    fake = FakePipeline()
    app.dependency_overrides[get_pipeline] = lambda: fake
    with TestClient(app) as c:
        c.fake = fake
        yield c
    app.dependency_overrides.clear()


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and isinstance(body["db"], bool)


@pytest.mark.parametrize("role", ["analyst", "manager", "admin"])
def test_policy(client, role):
    body = client.get(f"/policy/{role}").json()
    assert body["role"] == role and "orders" in body["plain_columns"]
    assert "api_keys" not in body["visible_tables"]


def test_policy_unknown_role(client):
    assert client.get("/policy/root").status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {"role": "analyst"},
        {"role": "root", "question": "q"},
        {"role": "manager", "region": "mars", "question": "q"},
        {"role": "analyst", "question": "q", "layers": ["nope"]},
    ],
)
def test_query_validation(client, payload):
    assert client.post("/query", json=payload).status_code == 422


def test_query_serialises_values(client):
    body = client.post("/query", json={"role": "analyst", "question": "revenue"}).json()
    assert body["decision"] == "allow" and body["rows"] == [["2024-01-02", 1.5, None]] and body["row_count"] == 1


def test_blocked_request_is_http_200(client):
    r = client.post("/query", json={"role": "analyst", "sql": "SELECT email FROM users"})
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] == "block" and body["rule_id"] == "allowlist.column_denied" and body["rows"] == []


def test_layer_override_is_off_by_default(client, monkeypatch):
    monkeypatch.delenv("T2S_ALLOW_LAYER_OVERRIDE", raising=False)
    r = client.post("/query", json={"role": "analyst", "question": "q", "layers": ["sandbox"]})
    assert r.status_code == 403 and client.fake.calls == []
    monkeypatch.setenv("T2S_ALLOW_LAYER_OVERRIDE", "1")
    r = client.post("/query", json={"role": "analyst", "question": "q", "layers": ["sandbox"]})
    assert r.status_code == 200 and client.fake.calls[0][4].enabled == frozenset({"sandbox"})


def test_audit_empty_without_logger(client):
    assert client.get("/audit").json() == []


# ------------------------------------------------------------------ live database
@pytest.fixture
def live_client():
    get_pipeline.cache_clear()
    with TestClient(app) as c:
        yield c


@pytest.mark.db
def test_live_allowed_and_blocked(live_client, monkeypatch, tmp_path):
    monkeypatch.setenv("T2S_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    get_pipeline.cache_clear()
    ok = live_client.post("/query", json={"role": "analyst", "question": "order count by status"}).json()
    assert ok["decision"] == "allow" and ok["row_count"] == 5
    blocked = live_client.post("/query", json={"role": "analyst", "sql": "SELECT email FROM users LIMIT 5"}).json()
    assert blocked["decision"] == "block" and blocked["rule_id"].startswith("allowlist.")
    no_region = live_client.post("/query", json={"role": "manager", "question": "order count by status"}).json()
    assert no_region["decision"] == "block"
    masked = live_client.post(
        "/query", json={"role": "manager", "region": "west", "question": "list customers with contact details"}
    ).json()
    assert masked["decision"] == "allow" and "***" in masked["rows"][0][masked["columns"].index("email")]
    assert len(live_client.get("/audit?n=10").json()) == 4
    get_pipeline.cache_clear()


@pytest.mark.db
def test_streamlit_app_runs_headless():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(REPO_ROOT / "ui" / "streamlit_app.py"), default_timeout=60).run()
    assert not at.exception
