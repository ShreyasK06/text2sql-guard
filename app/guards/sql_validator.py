"""Static SQL validator: the `allowlist` and `ast_rules` layers (sqlglot AST based, fail closed).

Called by app/pipeline.py (and the eval harness). Decisions are made on the parsed AST only.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope

from app.guards.policy import Policy, RolePolicy
from app.types import Decision, GuardResult, ValidationOutcome

logging.getLogger("sqlglot").setLevel(logging.ERROR)

ALLOWLIST = "allowlist"
AST_RULES = "ast_rules"


def _cls(*names: str) -> tuple:
    return tuple(getattr(exp, n) for n in names if hasattr(exp, n))


@dataclass
class _V:
    rule_id: str
    reason: str


# ----------------------------------------------------------------------------- parsing

def _parse(sql, dialect: str):
    """Return (statements, error). Empty statements and bare semicolons are dropped."""
    if not isinstance(sql, str):
        return [], "input is not a string"
    try:
        raw = sqlglot.parse(sql, dialect=dialect)
    except BaseException as e:  # ParseError, TokenError, RecursionError ...
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        msg = (str(e).splitlines() or [type(e).__name__])[0][:200]
        return [], f"{type(e).__name__}: {msg}"
    stmts = [s for s in raw if s is not None and not isinstance(s, exp.Semicolon)]
    return stmts, None


def _normalize(stmt: exp.Expression, dialect: str) -> exp.Expression:
    """Copy with identifiers normalised and the `public.` qualifier removed."""
    norm = normalize_identifiers(stmt.copy(), dialect=dialect)
    for t in list(norm.find_all(exp.Table)):
        db = t.args.get("db")
        if db is not None and not t.args.get("catalog") and db.name == "public":
            t.set("db", None)
    for c in list(norm.find_all(exp.Column)):
        db = c.args.get("db")
        if db is not None and not c.args.get("catalog") and db.name == "public":
            c.set("db", None)
    return norm


def _is_query(n: exp.Expression) -> bool:
    return isinstance(n, (exp.Select, exp.SetOperation, exp.Subquery))


# ----------------------------------------------------------------------------- function names

_SYNTAX = _cls("And", "Or", "Xor", "Case", "Cast", "Extract", "Collate", "Exists", "Array")
_OPERATOR_FUNCS = _cls(
    "ArrayContains", "ArrayContainedBy", "ArrayContainsAll", "ArrayOverlaps",
    "JSONExtract", "JSONExtractScalar", "JSONBExtract", "JSONBExtractScalar", "JSONBContains",
    "JSONBContainsAllTopKeys", "JSONBContainsAnyTopKeys", "JSONBContainsTopKey",
    "JSONBDeleteAtPath", "JSONBPathExists", "RegexpLike", "RegexpILike", "RegexpFullMatch",
)
_ANON = _cls("Anonymous", "AnonymousAggFunc")

# Postgres names that sqlglot folds into a typed node with a different SQL name.
_NAME_OVERRIDES: dict[str, set[str]] = {
    "GroupConcat": {"string_agg"},
    "LogicalAnd": {"bool_and"},
    "LogicalOr": {"bool_or"},
    "TimestampTrunc": {"date_trunc"},
    "DateTrunc": {"date_trunc"},
    "StrPosition": {"position", "strpos"},
    "TimeToStr": {"to_char"},
    "StrToDate": {"to_date"},
    "ExplodingGenerateSeries": {"generate_series"},
    "GenerateSeries": {"generate_series"},
    "CurrentVersion": {"version"},
    "Trim": {"trim", "ltrim", "rtrim", "btrim"},
    "Pad": {"lpad", "rpad"},
    "Rand": {"random"},
    "Hex": {"to_hex"},
    "CurrentTimestamp": {"now", "current_timestamp"},
    "SHA2Digest": {"sha256"},
    "JSONArrayAgg": {"json_agg"},
}

# Bare keywords the parser may leave as plain columns.
_BARE_KEYWORDS = {"current_role", "current_user", "session_user", "user", "current_catalog", "current_schema"}


def _func_names(n: exp.Expression) -> set[str]:
    names = {k.lower() for k in n.sql_names()} if hasattr(n, "sql_names") else set()
    names.add(n.sql_name().lower())
    names |= _NAME_OVERRIDES.get(type(n).__name__, set())
    return names


def _check_functions(tree: exp.Expression, policy: Policy) -> _V | None:
    allowed = policy.functions
    for n in tree.walk():
        if isinstance(n, _ANON):
            name = str(n.name or "").lower()
            cands, shown = {name}, name
        elif isinstance(n, exp.Func):
            if isinstance(n, _SYNTAX) or isinstance(n, _OPERATOR_FUNCS):
                continue
            if isinstance(n, exp.If) and isinstance(n.parent, exp.Case):
                continue
            cands = _func_names(n)
            shown = n.sql_name().lower()
        elif isinstance(n, exp.Column) and not n.table and n.name.lower() in _BARE_KEYWORDS:
            cands, shown = {n.name.lower()}, n.name.lower()
        else:
            continue
        if not (cands & allowed):
            return _V("allowlist.function_denied", f"function '{shown}' is not in the allowed function list")
    return None


# ----------------------------------------------------------------------------- types

_T = exp.DataType.Type
_ALLOWED_TYPES = {
    getattr(_T, n) for n in (
        "TINYINT", "SMALLINT", "INT", "BIGINT", "DECIMAL", "FLOAT", "DOUBLE", "TEXT", "VARCHAR",
        "CHAR", "BPCHAR", "BOOLEAN", "DATE", "TIME", "TIMETZ", "TIMESTAMP", "TIMESTAMPTZ", "INTERVAL",
    ) if hasattr(_T, n)
}


def _check_types(tree: exp.Expression, dialect: str) -> _V | None:
    for dt in tree.find_all(exp.DataType):
        if not isinstance(dt.this, _T) or dt.this not in _ALLOWED_TYPES or dt.args.get("nested"):
            try:
                shown = dt.sql(dialect=dialect)
            except Exception:
                shown = str(dt.this)
            return _V("allowlist.type_denied", f"cast to type '{shown}' is not allowed")
    return None


# ----------------------------------------------------------------------------- allowlist

def _real_tables(norm: exp.Expression, scopes: list[Scope] | None) -> list[exp.Table]:
    if scopes is None:
        return list(norm.find_all(exp.Table))
    accounted: set[int] = set()
    real: list[exp.Table] = []
    for sc in scopes:
        for t in sc.tables:
            accounted.add(id(t))
            src = sc.sources.get(t.alias_or_name)
            if isinstance(src, Scope) and not t.args.get("db") and not t.args.get("catalog"):
                continue  # reference to a CTE
            real.append(t)
    for t in norm.find_all(exp.Table):
        if id(t) not in accounted:
            real.append(t)
    return real


def _check_tables(tables: list[exp.Table], role: RolePolicy) -> _V | None:
    for t in tables:
        if not isinstance(t.this, exp.Identifier):
            continue  # table function: handled by the function check
        name = t.name
        cat, db = t.args.get("catalog"), t.args.get("db")
        full = ".".join(p.name for p in (cat, db, t.this) if p is not None)
        if cat is not None or (db is not None and db.name != "public"):
            return _V("allowlist.table_denied",
                      f"table '{full}' is schema-qualified outside 'public' and is not permitted")
        if name not in role.tables:
            return _V("allowlist.table_denied", f"table '{name}' is not permitted for role '{role.name}'")
    return None


def _check_stars(norm: exp.Expression, scopes: list[Scope], role: RolePolicy, policy: Policy) -> _V | None:
    for star in norm.find_all(exp.Star):
        p = star.parent
        ok = False
        if isinstance(p, exp.Count):
            ok = True
        elif isinstance(p, exp.Select) and star.arg_key == "expressions":
            ok = True
        elif isinstance(p, exp.Column):
            gp = p.parent
            ok = isinstance(gp, exp.Count) or (isinstance(gp, exp.Select) and p.arg_key == "expressions")
        if not ok:
            return _V("allowlist.star_denied", "'*' used outside a plain projection or count(*)")
    for sc in scopes:
        sel = sc.expression
        if not isinstance(sel, exp.Select):
            continue
        for proj in sel.expressions:
            if isinstance(proj, exp.Star):
                srcs = [src for _n, src in sc.selected_sources.values()]
            elif isinstance(proj, exp.Column) and isinstance(proj.this, exp.Star):
                found = None
                s = sc
                while s is not None and found is None:
                    if proj.table in s.selected_sources:
                        found = s.selected_sources[proj.table][1]
                    s = s.parent
                if found is None:
                    return _V("allowlist.unresolved_column", f"'{proj.table}.*' refers to an unknown relation")
                srcs = [found]
            else:
                continue
            for src in srcs:
                if isinstance(src, exp.Table) and isinstance(src.this, exp.Identifier):
                    cols = policy.schema.get(src.name)
                    if cols is None:
                        return _V("allowlist.star_denied", f"'*' over unknown table '{src.name}'")
                    bad = [c for c in cols if role.column_access(src.name, c) == "denied"]
                    if bad:
                        return _V("allowlist.star_denied",
                                  f"'*' over table '{src.name}' would expose denied columns: {', '.join(bad)}")
    return None


def _qualify(norm: exp.Expression, policy: Policy, dialect: str) -> exp.Expression:
    schema = {t: {c: "text" for c in cols} for t, cols in policy.schema.items()}
    return qualify(
        norm.copy(), schema=schema, dialect=dialect,
        validate_qualify_columns=False, identify=False, quote_identifiers=False,
    )


@dataclass
class _Res:
    table: str | None = None
    column: str = ""
    access: str | None = None          # plain | masked | denied (real table columns only)
    alias_projs: list = field(default_factory=list)
    error: str | None = None


def _leaves(n: exp.Expression) -> list[exp.Select]:
    if isinstance(n, exp.Subquery):
        return _leaves(n.this)
    if isinstance(n, exp.SetOperation):
        return _leaves(n.this) + _leaves(n.expression)
    if isinstance(n, exp.Select):
        return [n]
    return []


def _unparen(n):
    while isinstance(n, exp.Paren):
        n = n.this
    return n


def _analyse_columns(qt: exp.Expression, role: RolePolicy, policy: Policy):
    """Return (violation | None, masked_outputs)."""
    for tc in qt.find_all(exp.TableColumn):
        return _V("allowlist.unresolved_column", f"whole-row reference '{tc.name}' is not allowed"), {}
    scopes = traverse_scope(qt)
    scope_of = {id(s.expression): s for s in scopes}
    columns = [c for c in qt.find_all(exp.Column) if not isinstance(c.this, exp.Star)]
    res: dict[int, _Res] = {}

    def cands(scope: Scope, name: str):
        out = []
        for alias, (_node, src) in scope.selected_sources.items():
            if isinstance(src, exp.Table):
                if name in policy.schema.get(src.name, ()):
                    out.append((alias, src))
            elif isinstance(src, Scope):
                if name in src.expression.named_selects:
                    out.append((alias, src))
        return out

    def real(table: exp.Table, name: str) -> _Res:
        t = table.name
        if name not in policy.schema.get(t, ()):
            return _Res(error=f"column '{t}.{name}' does not exist in the catalog")
        return _Res(table=t, column=name, access=role.column_access(t, name))

    for c in columns:
        name = c.name
        owner = c.find_ancestor(exp.Select, exp.SetOperation)
        sc = scope_of.get(id(owner)) if owner is not None else None
        if sc is None:
            res[id(c)] = _Res(error=f"column '{name}' could not be attributed to a query scope")
            continue
        if c.args.get("db") is not None or c.args.get("catalog") is not None:
            res[id(c)] = _Res(error=f"schema-qualified column reference '{c.sql()}' is not allowed")
            continue
        if c.table:
            s, src = sc, None
            while s is not None:
                if c.table in s.sources:
                    src = s.sources[c.table]
                    break
                s = s.parent
            if isinstance(src, exp.Table) and isinstance(src.this, exp.Identifier):
                res[id(c)] = real(src, name)
            elif isinstance(src, (Scope, exp.Table)):  # derived table, CTE or (allow-listed) table function
                res[id(c)] = _Res()
            else:
                res[id(c)] = _Res(error=f"column '{c.table}.{name}' refers to an unknown relation")
            continue
        # unqualified leftover
        r = _Res()
        direct_ref = (
            (isinstance(c.parent, exp.Ordered) and isinstance(c.parent.parent, exp.Order)
             and c.parent.parent.parent is owner)
            or (isinstance(c.parent, exp.Group) and c.parent.parent is owner)
        )
        if isinstance(owner, exp.SetOperation):
            names = owner.named_selects
            if direct_ref and name in names:
                idx = names.index(name)
                for leaf in _leaves(owner):
                    if idx < len(leaf.expressions):
                        r.alias_projs.append(leaf.expressions[idx])
            else:
                r.error = f"column '{name}' could not be attributed to a table"
            res[id(c)] = r
            continue
        alias_projs = []
        if direct_ref:
            alias_projs = [p for p in owner.expressions if p.alias_or_name == name]
        found = []
        s = sc
        while s is not None and not found:
            found = cands(s, name)
            s = s.parent
        if alias_projs:
            r.alias_projs = alias_projs
        if len(found) == 1:
            alias, src = found[0]
            if isinstance(src, exp.Table):
                rr = real(src, name)
                rr.alias_projs = alias_projs
                r = rr
        elif len(found) > 1 and not alias_projs:
            r.error = f"column '{name}' is ambiguous"
        elif not found and not alias_projs:
            r.error = f"column '{name}' could not be attributed to any table"
        res[id(c)] = r

    # masked projections: bare columns in outermost select(s)
    allowed: set[int] = set()
    masked_outputs: dict[int, str] = {}
    for leaf in _leaves(qt):
        for i, proj in enumerate(leaf.expressions):
            inner = _unparen(proj.this if isinstance(proj, exp.Alias) else proj)
            r = res.get(id(inner)) if isinstance(inner, exp.Column) else None
            if r is not None and r.access == "masked" and not r.error and inner.parent is not None:
                allowed.add(id(inner))
                masked_outputs.setdefault(i, role.mask_kind(r.table, r.column) or "text")

    denied: _V | None = None
    misuse: _V | None = None
    unresolved: _V | None = None
    for c in columns:
        r = res[id(c)]
        label = f"{r.table}.{r.column}" if r.table else c.name
        if r.error:
            unresolved = unresolved or _V("allowlist.unresolved_column", r.error)
            continue
        if r.access == "denied":
            denied = denied or _V("allowlist.column_denied",
                                  f"column '{label}' is not permitted for role '{role.name}'")
        elif r.access == "masked" and id(c) not in allowed:
            misuse = misuse or _V(
                "allowlist.masked_column_misuse",
                f"masked column '{label}' may only appear as a bare column in the outermost SELECT list",
            )
        for p in r.alias_projs:
            for cc in p.find_all(exp.Column):
                rr = res.get(id(cc))
                if rr is not None and rr.access == "masked":
                    misuse = misuse or _V(
                        "allowlist.masked_column_misuse",
                        f"masked column '{rr.table}.{rr.column}' used via ORDER BY/GROUP BY on its output alias",
                    )
    # ordinals in ORDER BY / GROUP BY
    for node in qt.find_all(exp.Order, exp.Group):
        owner = node.parent
        if not isinstance(owner, (exp.Select, exp.SetOperation)):
            continue
        lits = []
        for e in node.expressions:
            e = e.this if isinstance(e, exp.Ordered) else e
            if isinstance(e, exp.Literal) and not e.is_string:
                lits.append(e)
        for lit in lits:
            try:
                idx = int(lit.name) - 1
            except ValueError:
                continue
            for leaf in _leaves(owner):
                if 0 <= idx < len(leaf.expressions):
                    for cc in leaf.expressions[idx].find_all(exp.Column):
                        rr = res.get(id(cc))
                        if rr is not None and rr.access == "masked":
                            misuse = misuse or _V(
                                "allowlist.masked_column_misuse",
                                f"masked column '{rr.table}.{rr.column}' used via positional ORDER BY/GROUP BY",
                            )
    return (denied or misuse or unresolved), masked_outputs


def _generic_columns(norm: exp.Expression, tables: list[exp.Table], role: RolePolicy, policy: Policy) -> _V | None:
    """Best-effort column check for non-query statements (DML/DDL). Fail closed on denied/masked."""
    names = [t for t in tables if isinstance(t.this, exp.Identifier)]
    for c in norm.find_all(exp.Column):
        if isinstance(c.this, exp.Star):
            continue
        cand = []
        for t in names:
            if c.table and c.table not in (t.alias_or_name, t.name):
                continue
            if c.name in policy.schema.get(t.name, ()):
                cand.append(t.name)
        for tname in cand:
            acc = role.column_access(tname, c.name)
            if acc == "denied":
                return _V("allowlist.column_denied", f"column '{tname}.{c.name}' is not permitted for role '{role.name}'")
            if acc == "masked":
                return _V("allowlist.masked_column_misuse", f"masked column '{tname}.{c.name}' cannot be used here")
    return None


# Statement kinds whose objects are visible to the parser (everything else is opaque -> blocked).
_OBJECT_STMTS = _cls("Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "TruncateTable")


def _allowlist_statement(stmt: exp.Expression, role: RolePolicy, policy: Policy, dialect: str):
    if not _is_query(stmt) and not isinstance(stmt, _OBJECT_STMTS):
        return _V("allowlist.parse_error",
                  f"statement type {type(stmt).__name__.upper()} cannot be object-checked (fail closed)"), {}
    try:
        norm = _normalize(stmt, dialect)
    except Exception as e:
        return _V("allowlist.parse_error", f"could not analyse statement: {type(e).__name__}"), {}
    query = _is_query(norm)
    scopes: list[Scope] | None = None
    if query:
        try:
            scopes = traverse_scope(norm)
        except Exception as e:
            return _V("allowlist.parse_error", f"could not analyse query structure: {type(e).__name__}"), {}
    tables = _real_tables(norm, scopes)
    v = _check_tables(tables, role)
    if v:
        return v, {}
    v = _check_functions(norm, policy)
    if v:
        return v, {}
    v = _check_types(norm, dialect)
    if v:
        return v, {}
    if not query:
        return _generic_columns(norm, tables, role, policy), {}
    v = _check_stars(norm, scopes or [], role, policy)
    if v:
        return v, {}
    for c in norm.find_all(exp.Column):
        if c.args.get("catalog") is not None or c.args.get("db") is not None:
            return _V("allowlist.unresolved_column", f"schema-qualified column reference '{c.sql()}' is not allowed"), {}
    try:
        qt = _qualify(norm, policy, dialect)
        return _analyse_columns(qt, role, policy)
    except Exception as e:
        return _V("allowlist.unresolved_column",
                  f"column resolution failed ({type(e).__name__}: {str(e)[:120]})"), {}


def _run_allowlist(stmts, perr, role, policy, dialect):
    if perr:
        return _V("allowlist.parse_error", f"SQL could not be parsed: {perr}"), {}
    if not stmts:
        return _V("allowlist.parse_error", "empty SQL"), {}
    masked: dict[int, str] = {}
    for stmt in stmts:
        v, m = _allowlist_statement(stmt, role, policy, dialect)
        if v:
            return v, {}
        masked = m
    return None, masked


# ----------------------------------------------------------------------------- ast rules

_FORBIDDEN = _cls(
    "DML", "DDL", "Insert", "Update", "Delete", "Merge", "Create", "Alter", "Drop", "TruncateTable",
    "Command", "Copy", "Set", "SetItem", "Grant", "Revoke", "Analyze", "Transaction", "Commit",
    "Rollback", "Use", "Pragma", "Comment", "Declare", "Kill", "Show", "Describe", "Cache",
    "Uncache", "Attach", "Detach", "LoadData", "Refresh", "Returning",
)


def _find_forbidden(tree: exp.Expression) -> exp.Expression | None:
    for n in tree.walk():
        if isinstance(n, _FORBIDDEN):
            return n
    return None


def _count_subqueries(tree: exp.Expression) -> int:
    count = 0
    for q in tree.find_all(exp.Select, exp.SetOperation):
        # skip operands of a set operation (they are counted by max_set_operations)
        p = q.parent
        if isinstance(p, exp.Subquery) and isinstance(p.parent, exp.SetOperation) and p.arg_key in ("this", "expression"):
            continue
        if isinstance(p, exp.SetOperation) and q.arg_key in ("this", "expression"):
            continue
        if q.find_ancestor(exp.Select, exp.SetOperation) is not None:
            count += 1
    return count


def _depth(tree: exp.Expression) -> int:
    best = 0
    for s in tree.find_all(exp.Select):
        d = 1
        a = s.parent
        while a is not None:
            if isinstance(a, exp.Select):
                d += 1
            a = a.parent
        best = max(best, d)
    return best


def _from_of(sel: exp.Select):
    return sel.args.get("from_") or sel.args.get("from")


def _flatten_and(n):
    n = _unparen(n)
    if isinstance(n, exp.And):
        return _flatten_and(n.this) + _flatten_and(n.expression)
    return [n]


def _cartesian(tree: exp.Expression) -> _V | None:
    for sel in tree.find_all(exp.Select):
        joins = sel.args.get("joins") or []
        if not joins:
            continue
        frm = _from_of(sel)
        earlier: list[str] = []
        if frm is not None and frm.this is not None:
            earlier.append(frm.this.alias_or_name)
        where = sel.args.get("where")
        conj = _flatten_and(where.this) if where is not None else []
        for j in joins:
            target = j.this
            alias = target.alias_or_name if target is not None else ""
            method = str(j.args.get("method") or "").upper()
            kind = str(j.args.get("kind") or "").upper()
            on, using = j.args.get("on"), j.args.get("using")
            ok = False
            if method == "NATURAL" or using:
                ok = True
            elif target is not None and not (isinstance(target, exp.Table) and isinstance(target.this, exp.Identifier)):
                # LATERAL / table function / derived table: conditioned if it reads an earlier alias
                ok = any(c.table in earlier for c in target.find_all(exp.Column) if c.table)
            if not ok and on is not None:
                tabs = {c.table for c in on.find_all(exp.Column) if c.table}
                ok = len(tabs) >= 2 and alias in tabs
            if not ok and on is None and kind in ("", "CROSS", "INNER"):
                for e in conj:
                    if isinstance(e, exp.EQ):
                        l, r = _unparen(e.this), _unparen(e.expression)
                        if isinstance(l, exp.Column) and isinstance(r, exp.Column) and l.table and r.table:
                            pair = {l.table, r.table}
                            if len(pair) == 2 and alias in pair and (pair - {alias}) <= set(earlier):
                                ok = True
                                break
            if not ok:
                what = "CROSS JOIN" if kind == "CROSS" else ("comma join" if on is None else "join")
                return _V("ast.cartesian_join",
                          f"{what} with '{alias or '?'}' has no condition relating it to the other tables (cartesian product)")
            earlier.append(alias)
    return None


def _limit_value(lim) -> tuple[str, int | None]:
    """Classify a limit node: ("none"|"literal"|"other", value)."""
    if lim is None:
        return "none", None
    if isinstance(lim, exp.Limit):
        e = lim.expression
        if isinstance(e, exp.Literal) and not e.is_string:
            try:
                return "literal", int(e.name)
            except ValueError:
                return "other", None
        return "other", None
    if isinstance(lim, exp.Fetch):
        opts = lim.args.get("limit_options")
        if opts is not None and (opts.args.get("percent") or opts.args.get("with_ties")):
            return "other", None
        cnt = lim.args.get("count")
        if cnt is None:
            return "literal", 1
        if isinstance(cnt, exp.Literal) and not cnt.is_string:
            try:
                return "literal", int(cnt.name)
            except ValueError:
                return "other", None
        return "other", None
    return "other", None


def _apply_limit(stmt: exp.Expression, max_rows: int, dialect: str):
    """Return (rewritten_sql, details)."""
    tree = stmt.copy()
    kind, val = _limit_value(tree.args.get("limit"))
    if kind == "literal" and val is not None and val <= max_rows:
        forced, limit = False, val
    else:
        tree.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))
        forced, limit = True, max_rows
    out = tree.sql(dialect=dialect, comments=False)
    return out, {"forced_limit": forced, "limit": limit}


def _run_ast(sql, stmts, perr, policy: Policy, dialect: str):
    lim = policy.limits
    if isinstance(sql, str) and len(sql) > lim.max_sql_chars:
        return _V("ast.too_long", f"SQL has {len(sql)} characters (max {lim.max_sql_chars})"), None, {}
    if perr:
        return _V("ast.parse_error", f"SQL could not be parsed: {perr}"), None, {}
    if not stmts:
        return _V("ast.empty", "empty SQL"), None, {}
    if len(stmts) > 1:
        return _V("ast.multi_statement", f"{len(stmts)} statements found; exactly one is allowed"), None, {}
    stmt = stmts[0]
    if not _is_query(stmt) or not _leaves(stmt):
        return _V("ast.non_select", f"only SELECT queries are allowed (got {type(stmt).__name__.upper()})"), None, {}
    bad = _find_forbidden(stmt)
    if bad is not None:
        return _V("ast.non_select", f"statement contains a non-read operation ({type(bad).__name__.upper()})"), None, {}
    for n in stmt.walk():
        if isinstance(n, exp.Select) and n.args.get("into") is not None:
            return _V("ast.select_into", "SELECT ... INTO is not allowed"), None, {}
    for n in stmt.walk():
        if isinstance(n, (exp.Select, exp.SetOperation, exp.Subquery)) and n.args.get("locks"):
            return _V("ast.locking_clause", "FOR UPDATE / FOR SHARE locking clauses are not allowed"), None, {}
    for t in stmt.find_all(exp.Table):
        cat, db = t.args.get("catalog"), t.args.get("db")
        name = t.name.lower() if isinstance(t.this, exp.Identifier) else ""
        if cat is not None or (db is not None and db.name.lower() != "public"):
            full = ".".join(p.name for p in (cat, db, t.this) if p is not None)
            return _V("ast.system_catalog", f"reference to '{full}' outside the public schema is not allowed"), None, {}
        if name.startswith("pg_") or name == "information_schema":
            return _V("ast.system_catalog", f"system catalog object '{name}' is not allowed"), None, {}
    for i in stmt.find_all(exp.Identifier):
        if i.name.lower() in ("pg_catalog", "information_schema"):
            return _V("ast.system_catalog", f"reference to '{i.name}' is not allowed"), None, {}
    for w in stmt.find_all(exp.With):
        if w.args.get("recursive"):
            return _V("ast.recursive_cte", "WITH RECURSIVE is not allowed"), None, {}
    joins = len(list(stmt.find_all(exp.Join)))
    if joins > lim.max_joins:
        return _V("ast.too_many_joins", f"{joins} joins (max {lim.max_joins})"), None, {}
    subs = _count_subqueries(stmt)
    if subs > lim.max_subqueries:
        return _V("ast.too_many_subqueries", f"{subs} subqueries/CTEs (max {lim.max_subqueries})"), None, {}
    depth = _depth(stmt)
    if depth > lim.max_depth:
        return _V("ast.too_deep", f"query nesting depth {depth} (max {lim.max_depth})"), None, {}
    nsets = len(list(stmt.find_all(exp.SetOperation)))
    if nsets > lim.max_set_operations:
        return _V("ast.too_many_set_ops", f"{nsets} set operations (max {lim.max_set_operations})"), None, {}
    # cartesian analysis on a qualified copy when possible so unqualified columns are attributed
    try:
        base = _normalize(stmt, dialect)
        try:
            base = _qualify(base, policy, dialect)
        except Exception:
            pass
        v = _cartesian(base)
    except Exception as e:
        return _V("ast.parse_error", f"could not analyse joins: {type(e).__name__}"), None, {}
    if v:
        return v, None, {}
    try:
        new_sql, details = _apply_limit(stmt, lim.max_rows, dialect)
        # the rewritten SQL must itself parse to a single statement and be stable
        re_stmts, re_err = _parse(new_sql, dialect)
        if re_err or len(re_stmts) != 1 or re_stmts[0].sql(dialect=dialect, comments=False) != new_sql:
            return _V("ast.parse_error", "rewritten SQL failed the round-trip check"), None, {}
    except Exception as e:
        return _V("ast.parse_error", f"could not rewrite LIMIT: {type(e).__name__}"), None, {}
    return None, new_sql, details


# ----------------------------------------------------------------------------- entry point

def _result(layer: str, v: _V | None, t0: float, details: dict | None = None) -> GuardResult:
    ms = (time.perf_counter() - t0) * 1000.0
    if v is None:
        return GuardResult(layer, Decision.ALLOW, None, "", ms, details or {})
    return GuardResult(layer, Decision.BLOCK, v.rule_id, v.reason, ms, details or {})


def validate_sql(
    sql: str,
    role: RolePolicy,
    policy: Policy,
    *,
    check_allowlist: bool = True,
    check_ast: bool = True,
    dialect: str | None = None,
) -> ValidationOutcome:
    dialect = dialect or policy.dialect
    if not (check_allowlist or check_ast):
        return ValidationOutcome(results=[], sql=sql, masked_outputs={}, parsed=False)
    stmts, perr = _parse(sql, dialect)
    results: list[GuardResult] = []
    masked: dict[int, str] = {}
    out_sql = sql
    if check_allowlist:
        t0 = time.perf_counter()
        try:
            v, m = _run_allowlist(stmts, perr, role, policy, dialect)
        except Exception as e:  # fail closed
            v, m = _V("allowlist.parse_error", f"internal analysis error: {type(e).__name__}"), {}
        results.append(_result(ALLOWLIST, v, t0))
        masked = m if v is None else {}
    new_sql = None
    if check_ast:
        t0 = time.perf_counter()
        try:
            v, new_sql, details = _run_ast(sql, stmts, perr, policy, dialect)
        except Exception as e:  # fail closed
            v, new_sql, details = _V("ast.parse_error", f"internal analysis error: {type(e).__name__}"), None, {}
        results.append(_result(AST_RULES, v, t0, details if v is None else {}))
    blocked = any(r.blocked for r in results)
    if check_ast and not blocked and new_sql is not None:
        out_sql = new_sql
    return ValidationOutcome(
        results=results, sql=out_sql, masked_outputs={} if blocked else masked,
        parsed=perr is None and bool(stmts),
    )
