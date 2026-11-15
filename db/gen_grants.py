"""Generate db/init/02_grants.sql from policies/policy.yaml.

    python -m db.gen_grants           # rewrite the file
    python -m db.gen_grants --check   # exit 1 if the committed file is stale

The DB grant covers every column a role may read at all: plain columns plus masked columns
(masking itself is applied later by the output guard). Anything not listed gets no grant:
default deny, so api_keys and users.password_hash are unreadable for every app role.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = REPO_ROOT / "policies" / "policy.yaml"
OUTPUT_PATH = REPO_ROOT / "db" / "init" / "02_grants.sql"

HEADER = """\
-- GENERATED FILE. DO NOT EDIT BY HAND.
-- Source: policies/policy.yaml   Generator: python -m db.gen_grants
-- Column-level SELECT grants per database role. Masked columns are granted too: the DB grant
-- says what a role may read at all, the output guard applies the mask afterwards.
-- Tables and columns not listed here are not granted to anyone (default deny).
"""


def _quote(identifier: str) -> str:
    if not identifier.replace("_", "").isalnum():
        raise ValueError(f"unsafe identifier in policy: {identifier!r}")
    return identifier


def render(policy: dict) -> str:
    catalog: dict[str, list[str]] = policy["schema"]
    roles: dict = policy["roles"]
    db_roles = [_quote(r["db_role"]) for r in roles.values()]

    out = [HEADER]
    out.append("-- Start from a clean slate (the owner keeps its own privileges).")
    out.append(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC, {', '.join(db_roles)};")
    out.append("")

    for role_name, role in roles.items():
        db_role = _quote(role["db_role"])
        out.append(f"-- role: {role_name} ({db_role})")
        for table, spec in role["tables"].items():
            if table not in catalog:
                raise ValueError(f"{role_name}: table {table!r} not in catalog")
            spec = spec or {}
            plain = list(spec.get("columns") or [])
            masked = list((spec.get("masked") or {}).keys())
            wanted = set(plain) | set(masked)
            unknown = wanted - set(catalog[table])
            if unknown:
                raise ValueError(f"{role_name}.{table}: unknown columns {sorted(unknown)}")
            # catalog order keeps the output stable and readable
            cols = [c for c in catalog[table] if c in wanted]
            if not cols:
                continue
            cols_sql = ", ".join(_quote(c) for c in cols)
            out.append(f"GRANT SELECT ({cols_sql}) ON {_quote(table)} TO {db_role};")
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def generate(policy_path: Path = POLICY_PATH) -> str:
    with open(policy_path, encoding="utf-8") as fh:
        return render(yaml.safe_load(fh))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if the committed file is stale")
    args = ap.parse_args(argv)

    text = generate()
    if args.check:
        current = OUTPUT_PATH.read_text(encoding="utf-8") if OUTPUT_PATH.exists() else ""
        if current.replace("\r\n", "\n") != text:
            print(f"{OUTPUT_PATH.relative_to(REPO_ROOT)} is stale: run python -m db.gen_grants", file=sys.stderr)
            return 1
        print("02_grants.sql is in sync with policy.yaml")
        return 0
    OUTPUT_PATH.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUTPUT_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
