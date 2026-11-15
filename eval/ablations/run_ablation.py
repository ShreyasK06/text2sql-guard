"""Run the 14-config ablation and write all result tables.

    python -m eval.ablations.run_ablation --split dev [--split heldout] [--out eval/results]

Outputs per split (see docs/ARCHITECTURE.md): raw/items_<split>.csv (git-ignored), ablation_<split>.csv,
full_items_<split>.csv, attribution_<split>.csv, latency_<split>.csv, summary_<split>.json.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import math
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.types import LAYERS  # noqa: E402
from eval.harness import ATTACK_CATEGORIES, CONFIGS  # noqa: E402
from eval.stats import bootstrap_ci  # noqa: E402

ABLATION_COLUMNS = [
    "config", "category", "n", "n_live", "blocked", "block_rate", "ci_lo", "ci_hi",
    "live_block_rate", "live_ci_lo", "live_ci_hi", "false_blocks", "false_block_rate",
]


# --------------------------------------------------------------------------- helpers
def _bool(series: pd.Series) -> np.ndarray:
    """Object column holding True/False/None/NaN -> bool array (missing -> False)."""
    return np.array(
        [bool(v) if v is not None and not (isinstance(v, float) and math.isnan(v)) else False for v in series],
        dtype=bool,
    )


def _attacks(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["category"] != "benign"]


def live_ids(df: pd.DataFrame) -> set[str]:
    """Attack ids that succeed with every guard off."""
    a = _attacks(df)
    a = a[a["config"] == "none"]
    return set(a.loc[_bool(a["attack_succeeded"]), "id"])


def _nan_to_none(o):
    if isinstance(o, float) and math.isnan(o):
        return None
    if isinstance(o, dict):
        return {k: _nan_to_none(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_nan_to_none(v) for v in o]
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return _nan_to_none(float(o))
    if isinstance(o, np.bool_):
        return bool(o)
    return o


# --------------------------------------------------------------------------- tables
def aggregate_ablation(df: pd.DataFrame) -> pd.DataFrame:
    """One row per (config, category) plus 'all_attacks' and 'benign'."""
    live = live_ids(df)
    rows: list[dict] = []
    nan = float("nan")
    present = list(df["config"].unique())
    configs = [c for c in CONFIGS if c in present] + [c for c in present if c not in CONFIGS]
    for cfg in configs:
        sub_cfg = df[df["config"] == cfg]
        att = _attacks(sub_cfg)
        for cat in (*ATTACK_CATEGORIES, "all_attacks"):
            sub = att if cat == "all_attacks" else att[att["category"] == cat]
            n = len(sub)
            blocked = (~_bool(sub["attack_succeeded"])).astype(int)
            is_live = sub["id"].isin(live).to_numpy()
            rate, lo, hi = bootstrap_ci(blocked)
            lrate, llo, lhi = bootstrap_ci(blocked[is_live])
            rows.append({
                "config": cfg, "category": cat, "n": n, "n_live": int(is_live.sum()),
                "blocked": int(blocked.sum()), "block_rate": rate, "ci_lo": lo, "ci_hi": hi,
                "live_block_rate": lrate, "live_ci_lo": llo, "live_ci_hi": lhi,
                "false_blocks": nan, "false_block_rate": nan,
            })
        ben = sub_cfg[sub_cfg["category"] == "benign"]
        fb = _bool(ben["false_block"]).astype(int)
        rate, lo, hi = bootstrap_ci(fb)
        rows.append({
            "config": cfg, "category": "benign", "n": len(ben), "n_live": nan, "blocked": nan,
            "block_rate": nan, "ci_lo": lo, "ci_hi": hi, "live_block_rate": nan,
            "live_ci_lo": nan, "live_ci_hi": nan, "false_blocks": int(fb.sum()), "false_block_rate": rate,
        })
    return pd.DataFrame(rows, columns=ABLATION_COLUMNS)


def full_items_table(df: pd.DataFrame) -> pd.DataFrame:
    """Per-item outcome under 'full' and 'none' (feeds the residual-risk section)."""
    live = live_ids(df)
    out = df[df["config"].isin(["full", "none"])].copy()
    out["live"] = out["id"].isin(live) & (out["category"] != "benign")
    cols = ["id", "category", "subcategory", "role", "entry", "oracle_type", "live", "config", "decision",
            "blocked_by", "rule_id", "reason", "attack_succeeded", "false_block"]
    return out[cols].sort_values(["config", "id"]).reset_index(drop=True)


def attribution_table(df: pd.DataFrame) -> pd.DataFrame:
    """Under 'full': attacks by first blocking layer. 'none' = allowed; 'execution' = database error."""
    full = _attacks(df[df["config"] == "full"]).copy()
    full["first_layer"] = [
        "none" if d == "allow" else (b if b else "unknown") for d, b in zip(full["decision"], full["blocked_by"])
    ]
    full["succ"] = _bool(full["attack_succeeded"])
    rows = []
    for cat in (*ATTACK_CATEGORIES, "all_attacks"):
        sub = full if cat == "all_attacks" else full[full["category"] == cat]
        for layer, g in sub.groupby("first_layer"):
            rows.append({"category": cat, "blocked_by": layer, "n": len(g), "n_succeeded": int(g["succ"].sum())})
    return pd.DataFrame(rows, columns=["category", "blocked_by", "n", "n_succeeded"])


def latency_table(lat_df: pd.DataFrame) -> pd.DataFrame:
    """Per-layer latency over benign items under 'full'; end-to-end under 'full' and 'none'."""
    rows = []
    ben = lat_df[lat_df["category"] == "benign"]
    full = ben[ben["config"] == "full"]
    none = ben[ben["config"] == "none"]

    def stats(s: pd.Series) -> tuple[int, float, float]:
        s = s.dropna()
        if s.empty:
            return 0, float("nan"), float("nan")
        return len(s), float(np.median(s)), float(np.percentile(s, 95))

    for layer in (*LAYERS, "execution"):
        col = f"lat_{layer}"
        if col in full:
            n, med, p95 = stats(full[col])
            rows.append({"scope": layer, "config": "full", "n": n, "median_ms": med, "p95_ms": p95})
    for name, sub in (("full", full), ("none", none)):
        n, med, p95 = stats(sub["elapsed_ms"])
        rows.append({"scope": "end_to_end", "config": name, "n": n, "median_ms": med, "p95_ms": p95})
    return pd.DataFrame(rows, columns=["scope", "config", "n", "median_ms", "p95_ms"])


def _ci_dict(ind: np.ndarray) -> dict:
    r, lo, hi = bootstrap_ci(ind)
    return {"rate": r, "ci_lo": lo, "ci_hi": hi, "n": int(len(ind))}


def git_head() -> str | None:
    """Read-only `git rev-parse HEAD`; None when git or a repository is unavailable."""
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=10)
        return (out.stdout.strip() or None) if out.returncode == 0 else None
    except Exception:
        return None


def build_summary(df: pd.DataFrame, split: str, cache_stats: dict | None = None,
                  limits: dict | None = None, oracle_warnings: list[str] | None = None,
                  git_commit: str | None = None) -> dict:
    live = live_ids(df)
    full = df[df["config"] == "full"]
    att = _attacks(full)
    blocked = (~_bool(att["attack_succeeded"])).astype(int)
    is_live = att["id"].isin(live).to_numpy()
    per_cat = {}
    for cat in ATTACK_CATEGORIES:
        m = (att["category"] == cat).to_numpy()
        per_cat[cat] = {
            "n": int(m.sum()), "n_live": int((m & is_live).sum()),
            "block_rate_all": _ci_dict(blocked[m]), "block_rate_live": _ci_dict(blocked[m & is_live]),
        }
    ben = full[full["category"] == "benign"]
    fb = _bool(ben["false_block"])
    residual = att.loc[blocked == 0, "id"].tolist()
    return _nan_to_none({
        "split": split,
        "n_attacks": int(len(att)),
        "n_live": int(is_live.sum()),
        "full_block_rate_all": _ci_dict(blocked),
        "full_block_rate_live": _ci_dict(blocked[is_live]),
        "per_category": per_cat,
        "n_benign": int(len(ben)),
        "false_block_rate": _ci_dict(fb.astype(int)),
        "residual_attack_ids": residual,
        "false_blocks": [
            {"id": r["id"], "role": r["role"], "layer": r["blocked_by"], "rule_id": r["rule_id"], "reason": r["reason"]}
            for _, r in ben[fb].iterrows()
        ],
        "cache": cache_stats or {},
        "oracle_warnings": oracle_warnings or [],
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": git_commit if git_commit is not None else git_head(),
        "policy_limits": limits or {},
    })


# --------------------------------------------------------------------------- orchestration
def write_split_outputs(split: str, df: pd.DataFrame, lat_df: pd.DataFrame | None, out: Path,
                        limits: dict | None = None) -> dict:
    (out / "raw").mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "raw" / f"items_{split}.csv", index=False)
    aggregate_ablation(df).to_csv(out / f"ablation_{split}.csv", index=False)
    full_items_table(df).to_csv(out / f"full_items_{split}.csv", index=False)
    attribution_table(df).to_csv(out / f"attribution_{split}.csv", index=False)
    if lat_df is not None:
        latency_table(lat_df).to_csv(out / f"latency_{split}.csv", index=False)
    summary = build_summary(df, split, df.attrs.get("cache"), limits, df.attrs.get("oracle_warnings"))
    (out / f"summary_{split}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", action="append", help="split to run (repeatable); default: dev")
    ap.add_argument("--out", default=str(REPO_ROOT / "eval" / "results"))
    ap.add_argument("--no-latency", action="store_true", help="skip the uncached latency pass")
    args = ap.parse_args(argv)
    splits = args.split or ["dev"]
    out = Path(args.out)
    if not out.is_absolute():
        out = REPO_ROOT / out
    out.mkdir(parents=True, exist_ok=True)

    from eval.harness import default_pipeline, run_latency_pass, run_split

    pipeline = default_pipeline()
    limits = dataclasses.asdict(pipeline.policy.limits)
    try:
        for split in splits:
            df = run_split(split, pipeline=pipeline)
            lat = None if args.no_latency else run_latency_pass(split, pipeline=pipeline)
            summary = write_split_outputs(split, df, lat, out, limits)
            fr = summary["full_block_rate_all"]
            print(f"[{split}] attacks={summary['n_attacks']} live={summary['n_live']} "
                  f"full block rate (all)={fr['rate']} benign false-block={summary['false_block_rate']['rate']} "
                  f"cache={summary['cache']}")
    finally:
        closer = getattr(pipeline.executor, "close", None)
        if closer:
            closer()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
