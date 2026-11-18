"""Shared types. Every module codes against these; see docs/ARCHITECTURE.md."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# Ablation layers, in pipeline order.
LAYERS: tuple[str, ...] = (
    "input_guard",
    "allowlist",
    "ast_rules",
    "cost_gate",
    "sandbox",
    "output_guard",
)

ROLES: tuple[str, ...] = ("analyst", "manager", "admin")
REGIONS: tuple[str, ...] = ("north", "south", "east", "west")


class Decision(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"


@dataclass
class GuardResult:
    """What every guard returns: allow/block + rule id + human-readable reason."""

    layer: str                      # one of LAYERS
    decision: Decision
    rule_id: str | None = None      # e.g. "ast.non_select"; None when nothing fired
    reason: str = ""
    latency_ms: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return self.decision == Decision.BLOCK

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer,
            "decision": self.decision.value,
            "rule_id": self.rule_id,
            "reason": self.reason,
            "latency_ms": round(self.latency_ms, 3),
            "details": self.details,
        }


@dataclass
class ValidationOutcome:
    """Result of the static validator (covers the `allowlist` and `ast_rules` layers)."""

    results: list[GuardResult]      # one per enabled static layer, in order
    sql: str                        # SQL to execute: rewritten (forced LIMIT) or the input unchanged
    masked_outputs: dict[int, str] = field(default_factory=dict)  # output column index -> mask kind
    parsed: bool = False

    @property
    def blocked(self) -> bool:
        return any(r.blocked for r in self.results)


@dataclass
class ExecResult:
    ok: bool
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    row_count: int = 0              # rows fetched (before the row cap is applied when sandbox is off)
    truncated: bool = False         # row cap applied
    elapsed_ms: float = 0.0
    error: str | None = None
    # permission | timeout | watchdog | read_only | multi_statement | syntax | other
    error_kind: str | None = None
    sandboxed: bool = True


@dataclass
class OutputOutcome:
    result: GuardResult
    columns: list[str]
    rows: list[tuple]
    masked_columns: list[str] = field(default_factory=list)
    redactions: int = 0             # number of cells changed


@dataclass(frozen=True)
class GuardConfig:
    """Which layers are switched on. Used by the ablation harness."""

    enabled: frozenset[str] = frozenset(LAYERS)

    def on(self, layer: str) -> bool:
        return layer in self.enabled

    @classmethod
    def all(cls) -> "GuardConfig":
        return cls(frozenset(LAYERS))

    @classmethod
    def none(cls) -> "GuardConfig":
        return cls(frozenset())

    @classmethod
    def only(cls, *layers: str) -> "GuardConfig":
        return cls(frozenset(layers))

    @classmethod
    def without(cls, *layers: str) -> "GuardConfig":
        return cls(frozenset(LAYERS) - frozenset(layers))

    def name(self) -> str:
        if self.enabled == frozenset(LAYERS):
            return "full"
        if not self.enabled:
            return "none"
        if len(self.enabled) == 1:
            return "only:" + next(iter(self.enabled))
        missing = [l for l in LAYERS if l not in self.enabled]
        if len(missing) == 1:
            return "without:" + missing[0]
        return "+".join(l for l in LAYERS if l in self.enabled)


@dataclass
class PipelineResult:
    request_id: str
    role: str
    region: str | None
    question: str | None
    decision: str                   # "allow" | "block" | "error"
    blocked_by: str | None = None   # layer that blocked, or "execution" for a DB error
    rule_id: str | None = None
    reason: str = ""
    sql_generated: str | None = None
    sql_executed: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    masked_columns: list[str] = field(default_factory=list)
    redactions: int = 0
    guard_results: list[GuardResult] = field(default_factory=list)
    exec_result: ExecResult | None = None
    retries: int = 0
    correction_log: list[dict[str, Any]] = field(default_factory=list)
    self_check: dict[str, Any] | None = None
    llm_error: dict[str, Any] | None = None   # structured provider failure, e.g. {"code": "rate_limited", "retry_after_s": 12}
    latency_ms: float = 0.0
    layer_latency_ms: dict[str, float] = field(default_factory=dict)
    config: str = "full"
