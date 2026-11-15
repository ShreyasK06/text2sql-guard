"""Tests for the policy loader (offline, no database)."""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
import yaml

from app.config import DEFAULT_POLICY_PATH
from app.guards.policy import Limits, Policy, PolicyError, RolePolicy, load_policy


def _raw() -> dict:
    return yaml.safe_load(Path(DEFAULT_POLICY_PATH).read_text(encoding="utf-8"))


def _write(tmp_path, data: dict) -> Path:
    p = tmp_path / "policy.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


def test_default_policy_loads(policy):
    assert policy.version == 1
    assert policy.dialect == "postgres"
    assert set(policy.roles) == {"analyst", "manager", "admin"}
    assert policy.limits.max_rows == 1000
    assert policy.limits.max_joins == 6


def test_dataclasses_are_frozen(policy):
    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.version = 5
    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.limits.max_rows = 5
    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.role("analyst").name = "x"


def test_functions_flattened_lowercase(policy):
    assert isinstance(policy.functions, frozenset)
    assert {"count", "coalesce", "date_trunc", "row_number", "starts_with"} <= policy.functions
    assert all(f == f.lower() for f in policy.functions)
    assert "pg_sleep" not in policy.functions


def test_unknown_role(policy):
    with pytest.raises(PolicyError):
        policy.role("root")


@pytest.mark.parametrize(
    "role,table,column,expected",
    [
        ("analyst", "users", "city", "plain"),
        ("analyst", "users", "ssn", "denied"),
        ("analyst", "api_keys", "secret", "denied"),
        ("manager", "users", "email", "masked"),
        ("manager", "users", "full_name", "plain"),
        ("manager", "users", "ssn", "denied"),
        ("admin", "users", "ssn", "masked"),
        ("admin", "users", "password_hash", "denied"),
        ("admin", "payments", "card_number", "masked"),
        ("admin", "USERS", "SSN", "masked"),
    ],
)
def test_column_access(policy, role, table, column, expected):
    assert policy.role(role).column_access(table, column) == expected


def test_visible_schema(policy):
    vis = policy.role("manager").visible_schema()
    assert "email" in vis["users"] and "full_name" in vis["users"]
    assert "ssn" not in vis["users"]
    assert "api_keys" not in vis
    assert "api_keys" not in policy.role("admin").visible_schema()


def test_sensitive_for(policy):
    analyst = {(t, c) for t, c, _ in policy.sensitive_for("analyst")}
    assert ("users", "ssn") in analyst and ("api_keys", "secret") in analyst
    assert ("users", "email") in analyst
    manager = {(t, c): k for t, c, k in policy.sensitive_for("manager")}
    assert manager[("users", "email")] == "email"     # masked counts
    assert ("users", "full_name") not in manager      # plain for manager
    assert ("users", "ssn") in manager                # denied counts
    admin = {(t, c): k for t, c, k in policy.sensitive_for("admin")}
    assert admin[("users", "ssn")] == "ssn"
    assert ("users", "email") not in admin
    assert ("api_keys", "secret") in admin


def test_row_scope(policy):
    assert policy.role("manager").row_scope == "region"
    assert policy.role("analyst").row_scope == "none"


def test_allow_all():
    p = Policy.allow_all({"Singer": ["Name", "Age"], "concert": ["id"]}, dialect="sqlite")
    role = p.role("all")
    assert p.dialect == "sqlite"
    assert role.column_access("singer", "name") == "plain"
    assert role.column_access("singer", "nope") == "denied"
    assert p.sensitive_for("all") == []
    assert "count" in p.functions
    assert p.limits == load_policy().limits
    assert Policy.allow_all({"t": ["a"]}, role_name="x").role("x").name == "x"


def test_missing_file(tmp_path):
    with pytest.raises(PolicyError):
        load_policy(tmp_path / "nope.yaml")


def test_invalid_yaml(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("a: [unclosed", encoding="utf-8")
    with pytest.raises(PolicyError):
        load_policy(p)


def _mut_unknown_table(d):
    d["roles"]["analyst"]["tables"]["ghost"] = {"columns": ["id"]}


def _mut_unknown_column(d):
    d["roles"]["analyst"]["tables"]["orders"]["columns"].append("ghost")


def _mut_overlap(d):
    d["roles"]["manager"]["tables"]["users"]["masked"]["city"] = "text"


def _mut_bad_mask(d):
    d["roles"]["manager"]["tables"]["users"]["masked"]["email"] = "bogus"


def _mut_bad_scope(d):
    d["roles"]["analyst"]["row_scope"] = "galaxy"


def _mut_bad_sensitive(d):
    d["sensitive_columns"]["users.ghost"] = "text"


def _mut_bad_sensitive_kind(d):
    d["sensitive_columns"]["users.ssn"] = "bogus"


def _mut_bad_limit(d):
    d["limits"]["max_rows"] = "many"


def _mut_unknown_limit(d):
    d["limits"]["max_wishes"] = 3


def _mut_no_roles(d):
    del d["roles"]


@pytest.mark.parametrize(
    "mutate",
    [_mut_unknown_table, _mut_unknown_column, _mut_overlap, _mut_bad_mask, _mut_bad_scope,
     _mut_bad_sensitive, _mut_bad_sensitive_kind, _mut_bad_limit, _mut_unknown_limit, _mut_no_roles],
)
def test_validation_errors(tmp_path, mutate):
    data = _raw()
    mutate(data)
    with pytest.raises(PolicyError):
        load_policy(_write(tmp_path, data))


def test_load_policy_from_str_path(tmp_path):
    p = _write(tmp_path, _raw())
    assert isinstance(load_policy(str(p)), Policy)


def test_types_exposed():
    assert dataclasses.is_dataclass(Limits) and dataclasses.is_dataclass(RolePolicy)
