"""Pipeline orchestration: guards in order, self-correction loop, layer toggles, audit."""
from __future__ import annotations

import time
import uuid
from typing import Any

from app.guards.cost_gate import check_cost
from app.guards.input_guard import check_input
from app.guards.output_guard import guard_output
from app.guards.policy import Policy
from app.guards.sql_validator import validate_sql
from app.types import Decision, ExecResult, GuardConfig, GuardResult, PipelineResult

# Database error kinds worth feeding back to the LLM for another attempt.
_RETRYABLE_EXEC_ERRORS = {"syntax", "other"}


class Pipeline:
    def __init__(self, policy: Policy, executor=None, provider=None, audit=None, max_retries: int = 2):
        self.policy = policy
        self.executor = executor
        self.provider = provider
        self.audit = audit
        self.max_retries = max_retries

    # ------------------------------------------------------------------ public
    def run(
        self,
        question: str | None,
        role: str,
        region: str | None = None,
        *,
        sql_override: str | None = None,
        config: GuardConfig = GuardConfig.all(),
    ) -> PipelineResult:
        started = time.perf_counter()
        role_policy = self.policy.role(role)
        res = PipelineResult(
            request_id=uuid.uuid4().hex,
            role=role,
            region=region,
            question=question,
            decision="error",
            config=config.name(),
        )
        try:
            self._run(res, question, role_policy, region, sql_override, config)
        except Exception as exc:  # fail closed: an internal error never returns data
            res.decision = "error"
            res.blocked_by = res.blocked_by or "internal"
            res.rule_id = "internal.error"
            res.reason = f"{type(exc).__name__}: {exc}"
            res.columns, res.rows = [], []
        res.latency_ms = (time.perf_counter() - started) * 1000
        if self.audit is not None:
            self.audit.log(res)
        return res

    # ----------------------------------------------------------------- helpers
    @staticmethod
    def _record(res: PipelineResult, g: GuardResult) -> None:
        res.guard_results.append(g)
        res.layer_latency_ms[g.layer] = res.layer_latency_ms.get(g.layer, 0.0) + g.latency_ms

    @staticmethod
    def _block(res: PipelineResult, g: GuardResult) -> None:
        res.decision = "block"
        res.blocked_by = g.layer
        res.rule_id = g.rule_id
        res.reason = g.reason
        res.columns, res.rows = [], []

    def _timed(self, fn, *args: Any, **kwargs: Any):
        t0 = time.perf_counter()
        out = fn(*args, **kwargs)
        return out, (time.perf_counter() - t0) * 1000

    # -------------------------------------------------------------------- core
    def _run(self, res, question, role_policy, region, sql_override, config) -> None:
        if role_policy.row_scope == "region" and not region:
            res.decision = "block"
            res.blocked_by = "internal"
            res.rule_id = "internal.region_required"
            res.reason = f"role '{role_policy.name}' is region-scoped; a region is required"
            return

        # [1] input guard
        if config.on("input_guard") and question:
            g, ms = self._timed(check_input, question, role_policy, self.policy)
            g.latency_ms = ms
            self._record(res, g)
            if g.blocked:
                self._block(res, g)
                return

        if sql_override is None and self.provider is None:
            raise RuntimeError("no LLM provider configured and no SQL supplied")

        feedback: list[dict[str, Any]] = []
        attempts = 1 if sql_override is not None else self.max_retries + 1
        for attempt in range(attempts):
            last_attempt = attempt == attempts - 1
            # [2]+[3] schema retrieval and generation
            if sql_override is not None:
                sql = sql_override
            else:
                from app.generation.generator import generate_sql

                t0 = time.perf_counter()
                sql = generate_sql(question or "", role_policy, self.policy, self.provider, feedback or None)
                res.layer_latency_ms["generation"] = (
                    res.layer_latency_ms.get("generation", 0.0) + (time.perf_counter() - t0) * 1000
                )
            if attempt == 0:
                res.sql_generated = sql
            res.retries = attempt

            failure = self._guarded_execute(res, sql, role_policy, region, config)
            if failure is None:
                break
            feedback.append({"attempt": attempt, "sql": sql, **failure})
            res.correction_log = list(feedback)
            if last_attempt or not failure.get("retryable", False):
                break
            # reset per-attempt state before retrying
            res.decision, res.blocked_by, res.rule_id, res.reason = "error", None, None, ""

        if res.decision == "allow" and sql_override is None and self.provider is not None:
            from app.generation.verifier import self_check

            try:
                res.self_check = self_check(question or "", res.sql_executed or "", self.provider)
            except Exception as exc:  # the verifier is advisory
                res.self_check = {"verdict": "unverified", "explanation": f"verifier failed: {exc}"}

    def _guarded_execute(self, res, sql, role_policy, region, config) -> dict[str, Any] | None:
        """Run layers 4-7 for one candidate SQL. Returns None on success, else a failure record."""
        masked_outputs: dict[int, str] | None = None
        sql_to_run = sql

        # [4] static validation
        if config.on("allowlist") or config.on("ast_rules"):
            outcome, ms = self._timed(
                validate_sql,
                sql,
                role_policy,
                self.policy,
                check_allowlist=config.on("allowlist"),
                check_ast=config.on("ast_rules"),
            )
            share = ms / max(len(outcome.results), 1)
            for g in outcome.results:
                g.latency_ms = g.latency_ms or share
                self._record(res, g)
            for g in outcome.results:
                if g.blocked:
                    self._block(res, g)
                    return {"layer": g.layer, "rule_id": g.rule_id, "reason": g.reason, "retryable": True}
            sql_to_run = outcome.sql
            # Lineage-based masking is only trustworthy when the allowlist layer resolved columns.
            masked_outputs = outcome.masked_outputs if config.on("allowlist") else None

        if self.executor is None:
            raise RuntimeError("no executor configured")
        sandbox = config.on("sandbox")

        # [5] cost gate
        if config.on("cost_gate"):
            g, ms = self._timed(
                check_cost, sql_to_run, role_policy, region, self.executor, self.policy.limits, sandbox=sandbox
            )
            g.latency_ms = ms
            self._record(res, g)
            if g.blocked:
                self._block(res, g)
                return {"layer": g.layer, "rule_id": g.rule_id, "reason": g.reason, "retryable": True}

        # [6] execution
        ex: ExecResult = self.executor.execute(sql_to_run, role_policy, region, sandbox=sandbox)
        res.exec_result = ex
        res.sql_executed = sql_to_run
        res.layer_latency_ms["sandbox" if sandbox else "execution"] = ex.elapsed_ms
        if not ex.ok:
            if sandbox and ex.error_kind in {"permission", "timeout", "read_only", "multi_statement"}:
                g = GuardResult(
                    layer="sandbox",
                    decision=Decision.BLOCK,
                    rule_id=f"sandbox.{ex.error_kind}",
                    reason=ex.error or "",
                    latency_ms=ex.elapsed_ms,
                )
                self._record(res, g)
                self._block(res, g)
                return {"layer": "sandbox", "rule_id": g.rule_id, "reason": g.reason, "retryable": False}
            res.decision = "error"
            res.blocked_by = "execution"
            res.rule_id = f"execution.{ex.error_kind or 'other'}"
            res.reason = ex.error or ""
            res.columns, res.rows = [], []
            return {
                "layer": "execution",
                "rule_id": res.rule_id,
                "reason": res.reason,
                "retryable": ex.error_kind in _RETRYABLE_EXEC_ERRORS,
            }
        if sandbox:
            self._record(
                res, GuardResult(layer="sandbox", decision=Decision.ALLOW, latency_ms=ex.elapsed_ms,
                                 details={"truncated": ex.truncated})
            )

        columns, rows = list(ex.columns), list(ex.rows)

        # [7] output guard
        if config.on("output_guard"):
            out, ms = self._timed(
                guard_output, columns, rows, role_policy, self.policy, masked_outputs=masked_outputs
            )
            out.result.latency_ms = ms
            self._record(res, out.result)
            if out.result.blocked:
                self._block(res, out.result)
                return {"layer": "output_guard", "rule_id": out.result.rule_id,
                        "reason": out.result.reason, "retryable": False}
            columns, rows = out.columns, out.rows
            res.masked_columns = out.masked_columns
            res.redactions = out.redactions

        res.decision = "allow"
        res.blocked_by = res.rule_id = None
        res.reason = ""
        res.columns, res.rows = columns, rows
        return None


def build_pipeline(settings=None, *, with_audit: bool = True) -> Pipeline:
    """Wire the default pipeline from environment settings."""
    from app.audit.log import AuditLogger
    from app.config import get_settings
    from app.execution.executor import PostgresExecutor
    from app.generation.provider import get_provider
    from app.guards.policy import load_policy

    settings = settings or get_settings()
    policy = load_policy(settings.policy_path)
    return Pipeline(
        policy=policy,
        executor=PostgresExecutor(settings, policy.limits),
        provider=get_provider(settings),
        audit=AuditLogger(settings.audit_path) if with_audit else None,
        max_retries=settings.max_retries,
    )
