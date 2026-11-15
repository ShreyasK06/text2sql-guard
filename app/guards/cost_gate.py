"""Cost gate: refuse queries whose EXPLAIN plan exceeds the policy ceilings. Fails closed."""
from __future__ import annotations

import time
from typing import Any

from app.execution.executor import ExplainError
from app.guards.policy import Limits, RolePolicy
from app.types import Decision, GuardResult

LAYER = "cost_gate"


def _walk(node: dict[str, Any]):
    yield node
    for child in node.get("Plans", []) or []:
        yield from _walk(child)


def check_cost(sql: str, role: RolePolicy, region: str | None, executor, limits: Limits,
               *, sandbox: bool = True) -> GuardResult:
    t0 = time.perf_counter()

    def done(decision: Decision, rule_id: str | None, reason: str, details: dict) -> GuardResult:
        return GuardResult(layer=LAYER, decision=decision, rule_id=rule_id, reason=reason,
                           latency_ms=(time.perf_counter() - t0) * 1000, details=details)

    try:
        doc = executor.explain(sql, role, region, sandbox=sandbox)
        root = doc["Plan"]
        total_cost = float(root.get("Total Cost", 0.0))
        max_rows = max(float(n.get("Plan Rows", 0) or 0) for n in _walk(root))
    except ExplainError as exc:
        kind = getattr(exc, "kind", "other")
        return done(Decision.BLOCK, "cost.explain_failed",
                    f"EXPLAIN failed ({kind}): {exc}; refusing to run an unplannable query",
                    {"kind": kind})
    except Exception as exc:  # malformed plan or executor failure: fail closed
        return done(Decision.BLOCK, "cost.explain_failed",
                    f"EXPLAIN failed: {type(exc).__name__}: {exc}", {"kind": "other"})

    details = {"total_cost": total_cost, "max_plan_rows": max_rows}
    if total_cost > limits.max_plan_cost:
        return done(Decision.BLOCK, "cost.too_expensive",
                    f"estimated plan cost {total_cost:.0f} exceeds the ceiling of {limits.max_plan_cost}",
                    details)
    if max_rows > limits.max_plan_rows:
        return done(Decision.BLOCK, "cost.too_many_rows",
                    f"estimated {max_rows:.0f} rows in a plan node exceeds the ceiling of "
                    f"{limits.max_plan_rows}", details)
    return done(Decision.ALLOW, None, "", details)
