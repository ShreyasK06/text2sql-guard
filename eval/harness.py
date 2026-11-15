"""Evaluation harness: load attack items, run them through the pipeline under every guard configuration.

No LLM is involved: every item supplies the SQL (`sql_override`), so this measures the guards against
attacker-controlled SQL (entry "sql") or against what a fully compliant, jailbroken LLM would emit
(entry "question": the input guard screens the question first).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd

from app.types import LAYERS, GuardConfig
from eval.oracle import OracleContext, attack_succeeded

REPO_ROOT = Path(__file__).resolve().parent.parent
ADVERSARIAL_DIR = REPO_ROOT / "eval" / "adversarial"

CATEGORIES = ("destructive_escalation", "exfiltration", "prompt_injection", "resource_abuse", "benign")
ATTACK_CATEGORIES = CATEGORIES[:-1]
ROLES = ("analyst", "manager", "admin")
REGIONS = ("north", "south", "east", "west")
ENTRIES = ("sql", "question")
ORACLE_TYPES = ("executes", "contains_sensitive", "contains_any", "resource")
REQUIRED_KEYS = ("id", "split", "category", "subcategory", "role", "entry", "sql")


# --------------------------------------------------------------------------- configs
def build_configs() -> dict[str, GuardConfig]:
    cfgs: dict[str, GuardConfig] = {"none": GuardConfig.none()}
    for layer in LAYERS:
        cfgs[f"only:{layer}"] = GuardConfig.only(layer)
    for layer in LAYERS:
        cfgs[f"without:{layer}"] = GuardConfig.without(layer)
    cfgs["full"] = GuardConfig.all()
    return cfgs


CONFIGS: dict[str, GuardConfig] = build_configs()


# --------------------------------------------------------------------------- items
def validate_item(item: dict, where: str = "") -> None:
    def bad(msg: str) -> None:
        raise ValueError(f"{where}{item.get('id', '?')}: {msg}")

    for k in REQUIRED_KEYS:
        if k not in item:
            bad(f"missing key {k!r}")
    if item["category"] not in CATEGORIES:
        bad(f"bad category {item['category']!r}")
    if item["role"] not in ROLES:
        bad(f"bad role {item['role']!r}")
    if item["entry"] not in ENTRIES:
        bad(f"bad entry {item['entry']!r}")
    region = item.get("region")
    if region is not None and region not in REGIONS:
        bad(f"bad region {region!r}")
    if item["role"] == "manager" and not region:
        bad("manager items require a region")
    if item["entry"] == "question" and not item.get("question"):
        bad("entry 'question' requires a question")
    if not isinstance(item["sql"], str) or not item["sql"].strip():
        bad("empty sql")
    if item["category"] == "benign":
        if item.get("oracle"):
            bad("benign items must not carry an oracle")
    else:
        otype = (item.get("oracle") or {}).get("type")
        if otype not in ORACLE_TYPES:
            bad(f"bad oracle type {otype!r}")


def load_items(split: str, version: str = "v1", root: Path | str | None = None) -> list[dict]:
    base = Path(root) if root else ADVERSARIAL_DIR
    path = base / version / f"{split}.jsonl"
    items: list[dict] = []
    seen: set[str] = set()
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            validate_item(item, where=f"{path.name}:{n} ")
            if item["id"] in seen:
                raise ValueError(f"{path.name}:{n} duplicate id {item['id']}")
            seen.add(item["id"])
            items.append(item)
    return items


# --------------------------------------------------------------------------- caching executor
class CachingExecutor:
    """Memoises executor calls by (kind, sql, role, region, sandbox).

    Query results are deterministic and every execution is rolled back, so the same SQL under the
    same connection mode needs to run once across the 14 configurations. Set `bypass = True` to
    disable the cache (used for the latency pass, which must measure real work).
    """

    def __init__(self, inner):
        self._inner = inner
        self._cache: dict[tuple, Any] = {}
        self.hits = 0
        self.misses = 0
        self.bypass = False

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    def _key(self, kind: str, sql: str, role, region, sandbox: bool) -> tuple:
        return (kind, sql, getattr(role, "name", role), region, bool(sandbox))

    def execute(self, sql, role, region, *, sandbox: bool = True):
        if self.bypass:
            return self._inner.execute(sql, role, region, sandbox=sandbox)
        key = self._key("execute", sql, role, region, sandbox)
        if key in self._cache:
            self.hits += 1
            return self._cache[key]
        self.misses += 1
        res = self._inner.execute(sql, role, region, sandbox=sandbox)
        self._cache[key] = res
        return res

    def explain(self, sql, role, region, *, sandbox: bool = True):
        if self.bypass:
            return self._inner.explain(sql, role, region, sandbox=sandbox)
        key = self._key("explain", sql, role, region, sandbox)
        if key in self._cache:
            self.hits += 1
            cached = self._cache[key]
        else:
            self.misses += 1
            try:
                cached = ("ok", self._inner.explain(sql, role, region, sandbox=sandbox))
            except Exception as exc:  # cache failures too (ExplainError is deterministic per SQL)
                cached = ("err", exc)
            self._cache[key] = cached
        if cached[0] == "err":
            raise cached[1]
        return cached[1]

    def stats(self) -> dict[str, float]:
        total = self.hits + self.misses
        return {"hits": self.hits, "misses": self.misses, "hit_rate": (self.hits / total) if total else float("nan")}

    def close(self) -> None:
        closer = getattr(self._inner, "close", None)
        if closer:
            closer()


# --------------------------------------------------------------------------- running
_LAYER_COLS = {layer: f"lat_{layer}" for layer in LAYERS}


def run_item(pipeline, item: dict, config: GuardConfig, ctx: OracleContext | None = None, config_name: str | None = None) -> dict:
    """Run one item under one config; return a flat record."""
    question = item["question"] if item["entry"] == "question" else None
    result = pipeline.run(question, item["role"], item.get("region"), sql_override=item["sql"], config=config)
    is_benign = item["category"] == "benign"

    succeeded: bool | None = None
    false_block: bool | None = None
    if is_benign:
        false_block = result.decision != "allow"
    else:
        if ctx is None:
            raise ValueError("an OracleContext is required for attack items")
        succeeded = bool(attack_succeeded(item, result, ctx))

    ex = result.exec_result
    lat = result.layer_latency_ms or {}
    rec: dict[str, Any] = {
        "id": item["id"],
        "split": item["split"],
        "category": item["category"],
        "subcategory": item.get("subcategory", ""),
        "role": item["role"],
        "entry": item["entry"],
        "oracle_type": (item.get("oracle") or {}).get("type", ""),
        "config": config_name or config.name(),
        "decision": result.decision,
        "blocked_by": result.blocked_by or "",
        "rule_id": result.rule_id or "",
        "reason": (result.reason or "")[:200],
        "exec_ok": (ex.ok if ex is not None else None),
        "exec_error_kind": (ex.error_kind if ex is not None and ex.error_kind else ""),
        "exec_elapsed_ms": (ex.elapsed_ms if ex is not None else float("nan")),
        "elapsed_ms": result.latency_ms,
        "row_count": len(result.rows or []),
        "redactions": result.redactions,
        "masked_columns": ",".join(result.masked_columns or []),
        "attack_succeeded": succeeded,
        "false_block": false_block,
    }
    for layer, col in _LAYER_COLS.items():
        rec[col] = lat.get(layer, float("nan"))
    rec["lat_execution"] = lat.get("execution", float("nan"))
    return rec


def _wrap(pipeline) -> CachingExecutor:
    if not isinstance(pipeline.executor, CachingExecutor):
        pipeline.executor = CachingExecutor(pipeline.executor)
    return pipeline.executor


def default_pipeline():
    from app.pipeline import build_pipeline

    return build_pipeline(with_audit=False)


def run_split(
    split: str,
    configs: dict[str, GuardConfig] | None = None,
    pipeline=None,
    progress: bool = True,
    items: list[dict] | None = None,
    ctx: OracleContext | None = None,
    version: str = "v1",
) -> pd.DataFrame:
    """Run every item of `split` under every config; return one row per (item, config)."""
    configs = configs or CONFIGS
    pipeline = pipeline or default_pipeline()
    cache = _wrap(pipeline)
    items = items if items is not None else load_items(split, version)
    ctx = ctx or OracleContext(pipeline.policy)

    records: list[dict] = []
    total = len(items) * len(configs)
    t0 = time.perf_counter()
    done = 0
    for cname, cfg in configs.items():
        for item in items:
            records.append(run_item(pipeline, item, cfg, ctx, config_name=cname))
            done += 1
            if progress and (done % 200 == 0 or done == total):
                el = time.perf_counter() - t0
                print(f"[{split}] {done}/{total} runs, {el:.0f}s, cache hit rate {cache.stats()['hit_rate']:.2f}", file=sys.stderr)
    df = pd.DataFrame(records)
    df.attrs["cache"] = cache.stats()
    df.attrs["oracle_warnings"] = list(ctx.warnings)
    return df


def run_latency_pass(
    split: str,
    pipeline=None,
    items: list[dict] | None = None,
    warmup: int = 3,
    version: str = "v1",
) -> pd.DataFrame:
    """Benign items under 'full' and 'none' with the cache bypassed, so latency reflects real work."""
    pipeline = pipeline or default_pipeline()
    cache = _wrap(pipeline)
    items = items if items is not None else load_items(split, version)
    benign = [i for i in items if i["category"] == "benign"]
    cfgs = {"none": CONFIGS["none"], "full": CONFIGS["full"]}
    prev = cache.bypass
    cache.bypass = True
    try:
        for cfg in cfgs.values():  # warm connections and plan caches; results discarded
            for item in benign[:warmup]:
                run_item(pipeline, item, cfg)
        records = [run_item(pipeline, i, cfg, config_name=cn) for cn, cfg in cfgs.items() for i in benign]
    finally:
        cache.bypass = prev
    return pd.DataFrame(records)
