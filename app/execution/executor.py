"""Sandboxed and unsandboxed SQL execution against PostgreSQL.

Called by app/pipeline.py (execute) and app/guards/cost_gate.py (explain).

Sandbox ON  : connect as the role's database user (column grants, RLS, read-only), extended
              protocol (exactly one statement), READ ONLY transaction, statement_timeout, row cap,
              always rolled back.
Sandbox OFF : ablation baseline = "the application uses its owner credentials". Simple protocol,
              stacked statements run, transaction is read-write, but the harness still applies a
              watchdog timeout and ALWAYS rolls back.

HARNESS SAFETY (not a counted guard layer): in unsandboxed mode a stacked `COMMIT; DROP TABLE x`
would persist inside the simple protocol, because the user text could end our transaction itself.
Transaction-control statements (COMMIT/END/ROLLBACK/ABORT/BEGIN/START/SAVEPOINT/PREPARE
TRANSACTION/...) are therefore refused before execution so the evaluation database can never be
modified. This protects the evaluation database; it is not part of the defended system.
"""
from __future__ import annotations

import json
import time
from typing import Any, Protocol

import psycopg
from psycopg import errors as pgerr

from app.config import Settings
from app.guards.policy import Limits, RolePolicy
from app.types import REGIONS, ExecResult

_EXPLAIN_TIMEOUT_MS = 2000

# First keyword of a statement that touches transaction state (harness safety, see module docstring).
_TXN_STARTERS = frozenset(
    {"COMMIT", "END", "ROLLBACK", "ABORT", "BEGIN", "START", "SAVEPOINT", "RELEASE", "PREPARE"}
)
_TXN_WORDS = tuple(sorted(_TXN_STARTERS))


class ExplainError(Exception):
    def __init__(self, message: str, kind: str = "other"):
        super().__init__(message)
        self.kind = kind


class Executor(Protocol):
    def execute(self, sql: str, role: RolePolicy, region: str | None, *, sandbox: bool = True) -> ExecResult: ...

    def explain(self, sql: str, role: RolePolicy, region: str | None, *, sandbox: bool = True) -> dict: ...


def _short(exc: BaseException) -> str:
    text = str(exc).strip()
    msg = text.splitlines()[0] if text else type(exc).__name__
    return msg[:300]


def classify_error(exc: BaseException, *, watchdog: bool = False) -> str:
    """Map a psycopg exception to the error_kind vocabulary."""
    msg = str(exc).lower()
    if "multiple commands into a prepared statement" in msg:
        return "multi_statement"
    if isinstance(exc, pgerr.InsufficientPrivilege):
        return "permission"
    if isinstance(exc, pgerr.ReadOnlySqlTransaction):
        return "read_only"
    if isinstance(exc, pgerr.QueryCanceled):
        return "watchdog" if watchdog else "timeout"
    code = getattr(exc, "sqlstate", None) or ""
    if code == "42501":
        return "permission"
    if code == "25006":
        return "read_only"
    if code == "57014":
        return "watchdog" if watchdog else "timeout"
    if code.startswith("42"):
        return "syntax"
    return "other"


def _txn_control_present(sql: str) -> bool:
    """True if any statement in `sql` starts with a transaction-control keyword (sqlglot tokens)."""
    try:
        from sqlglot.dialects.postgres import Postgres
        from sqlglot.tokens import TokenType

        tokens = Postgres.Tokenizer().tokenize(sql)
    except Exception:
        # Fail closed only when the text could contain such a statement.
        low = sql.upper()
        return any(w in low for w in _TXN_WORDS)
    at_start = True
    for tok in tokens:
        if tok.token_type == TokenType.SEMICOLON:
            at_start = True
            continue
        if at_start:
            if tok.text.upper() in _TXN_STARTERS:
                return True
            at_start = False
    return False


def _plain(v: Any) -> Any:
    if isinstance(v, memoryview):
        return bytes(v)
    return v


class PostgresExecutor:
    def __init__(self, settings: Settings, limits: Limits):
        self.settings = settings
        self.limits = limits
        self._conns: dict[str, psycopg.Connection] = {}

    # ------------------------------------------------------------ connections
    def _dsn(self, role: RolePolicy, sandbox: bool) -> tuple[str, str]:
        if sandbox:
            return role.db_role, self.settings.role_dsn(role.db_role)
        return "__owner__", self.settings.owner_dsn()

    def _drop(self, key: str) -> None:
        conn = self._conns.pop(key, None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def _get_conn(self, key: str, dsn: str, *, fresh: bool = False) -> psycopg.Connection:
        conn = self._conns.get(key)
        if conn is not None and (fresh or conn.closed or conn.broken):
            self._drop(key)
            conn = None
        if conn is None:
            conn = psycopg.connect(dsn, autocommit=True)
            self._conns[key] = conn
        return conn

    def close(self) -> None:
        for key in list(self._conns):
            self._drop(key)

    @staticmethod
    def _rollback(conn: psycopg.Connection) -> bool:
        """Leave the connection idle and reusable. Returns False if it is unusable."""
        try:
            if conn.closed or conn.broken:
                return False
            conn.execute("ROLLBACK")
            return True
        except Exception:
            return False

    def _begin(self, key: str, dsn: str, *, read_only: bool, timeout_ms: int, region_stmt: str | None):
        """Open a transaction on a healthy connection; reconnect once if it is broken."""
        for attempt in (0, 1):
            conn = self._get_conn(key, dsn, fresh=attempt == 1)
            try:
                self._rollback(conn)  # clear any stale state; harmless when idle
                conn.execute("BEGIN READ ONLY" if read_only else "BEGIN")
                conn.execute(f"SET LOCAL statement_timeout = {int(timeout_ms)}")
                if region_stmt:
                    conn.execute(region_stmt)
                return conn
            except psycopg.OperationalError:
                self._drop(key)
                if attempt == 1:
                    raise
        raise RuntimeError("unreachable")

    @staticmethod
    def _region_stmt(role: RolePolicy, region: str | None) -> tuple[str | None, str | None]:
        """Returns (statement, error)."""
        if role.row_scope == "region":
            if region not in REGIONS:
                return None, f"invalid or missing region {region!r}; expected one of {list(REGIONS)}"
            return f"SET LOCAL app.current_region = '{region}'", None  # region validated against a fixed set
        return "SET LOCAL app.current_region = ''", None

    # ---------------------------------------------------------------- execute
    def execute(self, sql: str, role: RolePolicy, region: str | None, *, sandbox: bool = True) -> ExecResult:
        if sandbox:
            return self._execute_sandboxed(sql, role, region)
        return self._execute_unsandboxed(sql, role, region)

    def _execute_sandboxed(self, sql: str, role: RolePolicy, region: str | None) -> ExecResult:
        region_stmt, err = self._region_stmt(role, region)
        if err:
            return ExecResult(ok=False, error=err, error_kind="permission", sandboxed=True)
        key, dsn = self._dsn(role, True)
        max_rows = self.limits.max_rows
        try:
            conn = self._begin(key, dsn, read_only=True, timeout_ms=self.limits.statement_timeout_ms,
                               region_stmt=region_stmt)
        except Exception as exc:
            return ExecResult(ok=False, error=_short(exc), error_kind="other", sandboxed=True)
        t0 = time.perf_counter()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, prepare=True)  # extended protocol: exactly one statement
                columns = [d.name for d in cur.description] if cur.description else []
                fetched = cur.fetchmany(max_rows + 1) if cur.description else []
            elapsed = (time.perf_counter() - t0) * 1000
            truncated = len(fetched) > max_rows
            rows = [tuple(_plain(v) for v in r) for r in fetched[:max_rows]]
            return ExecResult(ok=True, columns=columns, rows=rows, row_count=len(rows),
                              truncated=truncated, elapsed_ms=elapsed, sandboxed=True)
        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            return ExecResult(ok=False, error=_short(exc), error_kind=classify_error(exc),
                              elapsed_ms=elapsed, sandboxed=True)
        finally:
            if not self._rollback(conn):
                self._drop(key)

    def _execute_unsandboxed(self, sql: str, role: RolePolicy, region: str | None) -> ExecResult:
        if _txn_control_present(sql):
            return ExecResult(ok=False, error="transaction control not permitted by harness",
                              error_kind="other", sandboxed=False)
        region_stmt, err = self._region_stmt(role, region)
        if err:
            region_stmt = None  # owner credentials bypass RLS anyway; the GUC is only for parity
        key, dsn = self._dsn(role, False)
        cap = self.settings.unsandboxed_row_cap
        try:
            conn = self._begin(key, dsn, read_only=False, timeout_ms=self.settings.watchdog_ms,
                               region_stmt=region_stmt)
        except Exception as exc:
            return ExecResult(ok=False, error=_short(exc), error_kind="other", sandboxed=False)
        t0 = time.perf_counter()
        try:
            with conn.cursor() as cur:
                cur.execute(sql)  # simple protocol: stacked statements run
                last_cols: list[str] = []
                last_rows: list[tuple] = []
                truncated = False
                while True:
                    if cur.description:
                        last_cols = [d.name for d in cur.description]
                        got = cur.fetchmany(cap + 1)
                        truncated = len(got) > cap
                        last_rows = [tuple(_plain(v) for v in r) for r in got[:cap]]
                    if not cur.nextset():
                        break
            elapsed = (time.perf_counter() - t0) * 1000
            return ExecResult(ok=True, columns=last_cols, rows=last_rows, row_count=len(last_rows),
                              truncated=truncated, elapsed_ms=elapsed, sandboxed=False)
        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            return ExecResult(ok=False, error=_short(exc), error_kind=classify_error(exc, watchdog=True),
                              elapsed_ms=elapsed, sandboxed=False)
        finally:
            if not self._rollback(conn):
                self._drop(key)

    # ---------------------------------------------------------------- explain
    def explain(self, sql: str, role: RolePolicy, region: str | None, *, sandbox: bool = True) -> dict:
        region_stmt, err = self._region_stmt(role, region)
        if err:
            if sandbox:
                raise ExplainError(err, "permission")
            region_stmt = None
        key, dsn = self._dsn(role, sandbox)
        try:
            conn = self._begin(key, dsn, read_only=True, timeout_ms=_EXPLAIN_TIMEOUT_MS,
                               region_stmt=region_stmt)
        except Exception as exc:
            raise ExplainError(_short(exc), "other") from exc
        try:
            with conn.cursor() as cur:
                cur.execute("EXPLAIN (FORMAT JSON) " + sql, prepare=True)
                row = cur.fetchone()
            doc = row[0] if row else None
            if isinstance(doc, str):
                doc = json.loads(doc)
            if isinstance(doc, list) and doc:
                doc = doc[0]
            if not isinstance(doc, dict) or "Plan" not in doc:
                raise ExplainError("EXPLAIN returned no plan", "other")
            return doc
        except ExplainError:
            raise
        except Exception as exc:
            raise ExplainError(_short(exc), classify_error(exc)) from exc
        finally:
            if not self._rollback(conn):
                self._drop(key)
