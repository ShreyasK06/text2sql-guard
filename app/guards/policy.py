"""Policy loader: parses and validates policies/policy.yaml against its own catalog.

Called by the static validator, the output guard, the pipeline and the grant generator.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

from app.config import DEFAULT_POLICY_PATH

MASK_KINDS = frozenset({"email", "phone", "ssn", "card", "name", "date", "number", "text", "secret"})
ROW_SCOPES = frozenset({"none", "region"})


class PolicyError(Exception):
    """Raised when the policy file is malformed or inconsistent with its catalog."""


@dataclass(frozen=True)
class Limits:
    max_rows: int = 1000
    max_sql_chars: int = 4000
    max_joins: int = 6
    max_subqueries: int = 4
    max_depth: int = 4
    max_set_operations: int = 3
    max_plan_cost: int = 500000
    max_plan_rows: int = 2000000
    statement_timeout_ms: int = 2000
    max_cell_chars: int = 2000


@dataclass(frozen=True)
class RolePolicy:
    name: str
    description: str
    db_role: str
    row_scope: str  # "none" | "region"
    scrub_patterns: tuple[str, ...]
    tables: dict[str, frozenset[str]]  # table -> plain columns
    masked: dict[str, dict[str, str]]  # table -> {column: mask kind}

    def column_access(self, table: str, column: str) -> str:
        """Return "plain", "masked" or "denied" for table.column (case-insensitive)."""
        t, c = table.lower(), column.lower()
        if c in self.tables.get(t, frozenset()):
            return "plain"
        if c in self.masked.get(t, {}):
            return "masked"
        return "denied"

    def mask_kind(self, table: str, column: str) -> str | None:
        return self.masked.get(table.lower(), {}).get(column.lower())

    def visible_schema(self) -> dict[str, list[str]]:
        """Plain plus masked columns per table, for the prompt."""
        out: dict[str, list[str]] = {}
        names = set(self.tables) | set(self.masked)
        for t in sorted(names):
            cols = sorted(self.tables.get(t, frozenset()) | set(self.masked.get(t, {})))
            if cols:
                out[t] = cols
        return out


@dataclass(frozen=True)
class Policy:
    version: int
    dialect: str
    limits: Limits
    schema: dict[str, tuple[str, ...]]  # full catalog
    sensitive_columns: dict[str, str]  # "table.column" -> mask kind
    functions: frozenset[str]  # lower-case allowlist
    roles: dict[str, RolePolicy] = field(default_factory=dict)

    def role(self, name: str) -> RolePolicy:
        try:
            return self.roles[name]
        except KeyError:
            raise PolicyError(f"unknown role: {name!r} (known: {sorted(self.roles)})") from None

    def sensitive_for(self, role_name: str) -> list[tuple[str, str, str]]:
        """(table, column, kind) for every sensitive column that is not plain for the role."""
        role = self.role(role_name)
        out: list[tuple[str, str, str]] = []
        for key, kind in self.sensitive_columns.items():
            table, column = key.split(".", 1)
            if role.column_access(table, column) != "plain":
                out.append((table, column, kind))
        return out

    @classmethod
    def allow_all(
        cls,
        schema: dict[str, list[str]],
        dialect: str = "postgres",
        role_name: str = "all",
    ) -> "Policy":
        """Permissive single-role policy: every table and column plain.

        Same function allowlist and limits as the default policy file. Used to run the
        validator over schemas of a public benchmark.
        """
        base = _load_defaults()
        cat = {t.lower(): tuple(c.lower() for c in cols) for t, cols in schema.items()}
        role = RolePolicy(
            name=role_name,
            description="Permissive role (all tables and columns plain).",
            db_role=role_name,
            row_scope="none",
            scrub_patterns=(),
            tables={t: frozenset(cols) for t, cols in cat.items()},
            masked={},
        )
        return cls(
            version=base.version,
            dialect=dialect,
            limits=base.limits,
            schema=cat,
            sensitive_columns={},
            functions=base.functions,
            roles={role_name: role},
        )


_DEFAULTS: Policy | None = None


def _load_defaults() -> Policy:
    global _DEFAULTS
    if _DEFAULTS is None:
        _DEFAULTS = load_policy()
    return _DEFAULTS


def _req(d: Any, key: str, typ: type | tuple, where: str) -> Any:
    if not isinstance(d, dict) or key not in d:
        raise PolicyError(f"{where}: missing key {key!r}")
    v = d[key]
    if (not isinstance(v, typ)) or (isinstance(v, bool) and typ is int):
        raise PolicyError(f"{where}.{key}: expected {typ}, got {type(v).__name__}")
    return v


def _flatten_functions(raw: Any) -> frozenset[str]:
    out: set[str] = set()

    def walk(x: Any) -> None:
        if isinstance(x, str):
            out.add(x.strip().lower())
        elif isinstance(x, (list, tuple)):
            for i in x:
                walk(i)
        elif isinstance(x, dict):
            for i in x.values():
                walk(i)
        else:
            raise PolicyError(f"functions: unexpected entry {x!r}")

    walk(raw)
    out.discard("")
    return frozenset(out)


def load_policy(path: str | Path | None = None) -> Policy:
    p = Path(path) if path is not None else DEFAULT_POLICY_PATH
    try:
        with open(p, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except OSError as e:
        raise PolicyError(f"cannot read policy file {p}: {e}") from e
    except yaml.YAMLError as e:
        raise PolicyError(f"invalid YAML in {p}: {e}") from e
    if not isinstance(raw, dict):
        raise PolicyError("policy file must be a mapping")

    version = _req(raw, "version", int, "policy")
    dialect = str(raw.get("dialect", "postgres"))

    raw_limits = raw.get("limits") or {}
    if not isinstance(raw_limits, dict):
        raise PolicyError("limits must be a mapping")
    known = {f.name for f in fields(Limits)}
    unknown = set(raw_limits) - known
    if unknown:
        raise PolicyError(f"limits: unknown keys {sorted(unknown)}")
    for k, v in raw_limits.items():
        if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
            raise PolicyError(f"limits.{k}: must be a positive integer")
    limits = Limits(**raw_limits)

    raw_schema = _req(raw, "schema", dict, "policy")
    schema: dict[str, tuple[str, ...]] = {}
    for t, cols in raw_schema.items():
        if not isinstance(cols, list) or not cols or not all(isinstance(c, str) for c in cols):
            raise PolicyError(f"schema.{t}: must be a non-empty list of column names")
        lowered = tuple(c.lower() for c in cols)
        if len(set(lowered)) != len(lowered):
            raise PolicyError(f"schema.{t}: duplicate column names")
        schema[str(t).lower()] = lowered

    sensitive: dict[str, str] = {}
    for key, kind in (raw.get("sensitive_columns") or {}).items():
        key_l = str(key).lower()
        if key_l.count(".") != 1:
            raise PolicyError(f"sensitive_columns.{key}: expected 'table.column'")
        t, c = key_l.split(".")
        if t not in schema or c not in schema[t]:
            raise PolicyError(f"sensitive_columns.{key}: not in schema")
        if kind not in MASK_KINDS:
            raise PolicyError(f"sensitive_columns.{key}: invalid mask kind {kind!r}")
        sensitive[key_l] = kind

    functions = _flatten_functions(raw.get("functions") or [])

    raw_roles = _req(raw, "roles", dict, "policy")
    roles: dict[str, RolePolicy] = {}
    for rname, rdef in raw_roles.items():
        where = f"roles.{rname}"
        if not isinstance(rdef, dict):
            raise PolicyError(f"{where}: must be a mapping")
        row_scope = str(rdef.get("row_scope", "none"))
        if row_scope not in ROW_SCOPES:
            raise PolicyError(f"{where}.row_scope: {row_scope!r} not in {sorted(ROW_SCOPES)}")
        tables: dict[str, frozenset[str]] = {}
        masked: dict[str, dict[str, str]] = {}
        for t, tdef in (rdef.get("tables") or {}).items():
            tl = str(t).lower()
            if tl not in schema:
                raise PolicyError(f"{where}.tables.{t}: table not in schema")
            if not isinstance(tdef, dict):
                raise PolicyError(f"{where}.tables.{t}: must be a mapping")
            cols = [str(c).lower() for c in (tdef.get("columns") or [])]
            mcols = {str(c).lower(): k for c, k in (tdef.get("masked") or {}).items()}
            for c in list(cols) + list(mcols):
                if c not in schema[tl]:
                    raise PolicyError(f"{where}.tables.{t}: column {c!r} not in schema")
            overlap = set(cols) & set(mcols)
            if overlap:
                raise PolicyError(f"{where}.tables.{t}: columns both plain and masked: {sorted(overlap)}")
            for c, k in mcols.items():
                if k not in MASK_KINDS:
                    raise PolicyError(f"{where}.tables.{t}.masked.{c}: invalid mask kind {k!r}")
            tables[tl] = frozenset(cols)
            if mcols:
                masked[tl] = mcols
        roles[str(rname)] = RolePolicy(
            name=str(rname),
            description=str(rdef.get("description", "")),
            db_role=str(rdef.get("db_role", "")),
            row_scope=row_scope,
            scrub_patterns=tuple(str(x) for x in (rdef.get("scrub_patterns") or [])),
            tables=tables,
            masked=masked,
        )

    return Policy(
        version=version,
        dialect=dialect,
        limits=limits,
        schema=schema,
        sensitive_columns=sensitive,
        functions=functions,
        roles=roles,
    )
