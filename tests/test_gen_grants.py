"""02_grants.sql must match policy.yaml. No database needed."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from db import gen_grants

ROOT = Path(__file__).resolve().parent.parent


def test_grants_file_in_sync():
    assert gen_grants.main(["--check"]) == 0


def test_grants_header_and_no_denied_objects():
    text = (ROOT / "db" / "init" / "02_grants.sql").read_text(encoding="utf-8")
    assert text.startswith("-- GENERATED FILE")
    assert "\r" not in text
    assert "api_keys" not in text
    assert "password_hash" not in text
    assert "GRANT SELECT (" in text


def test_masked_columns_are_granted():
    text = gen_grants.generate()
    # manager masked email/phone, admin masked ssn
    assert re.search(r"GRANT SELECT \([^)]*\bemail\b[^)]*\) ON users TO t2s_manager;", text)
    assert re.search(r"GRANT SELECT \([^)]*\bssn\b[^)]*\) ON users TO t2s_admin;", text)
    assert not re.search(r"\bemail\b[^;]* ON users TO t2s_analyst;", text)


def test_statement_timeout_matches_policy():
    policy = yaml.safe_load((ROOT / "policies" / "policy.yaml").read_text(encoding="utf-8"))
    ms = policy["limits"]["statement_timeout_ms"]
    roles_sql = (ROOT / "db" / "init" / "00_roles.sql").read_text(encoding="utf-8")
    found = re.findall(r"SET statement_timeout = '(\d+)ms'", roles_sql)
    assert len(found) == 3 and all(int(v) == ms for v in found)


def test_sql_files_use_lf():
    for p in (ROOT / "db" / "init").glob("*.sql"):
        assert b"\r\n" not in p.read_bytes(), p.name
