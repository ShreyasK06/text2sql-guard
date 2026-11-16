"""Collect the measured numbers for the web app into web/data/results.json.

Reads only eval/results/summary_*.json, latency_*.csv, ablation_*.csv and the test-count line of
RESULTS.md. Nothing is typed by hand, and nothing is computed beyond copying and one subtraction
(median overhead = full minus no-guards median).

    python scripts/build_web_data.py
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "eval" / "results"
OUT = ROOT / "web" / "data" / "results.json"


def num(x: str | None) -> float | None:
    return float(x) if x not in ("", None) else None


def main() -> None:
    splits = {}
    for split in ("dev", "heldout"):
        s = json.loads((RES / f"summary_{split}.json").read_text(encoding="utf-8"))
        lat = list(csv.DictReader((RES / f"latency_{split}.csv").open(encoding="utf-8")))
        abl = [r for r in csv.DictReader((RES / f"ablation_{split}.csv").open(encoding="utf-8"))
               if r["category"].startswith("benign")]
        by = {(r["scope"], r["config"]): r for r in lat}
        full, none = by[("end_to_end", "full")], by[("end_to_end", "none")]
        splits[split] = {
            "n_benign": s["n_benign"],
            "n_attacks": s["n_attacks"],
            "false_block": s["false_block_rate"],
            "false_blocks": s["false_blocks"],
            "latency_per_layer": [
                {"scope": r["scope"], "n": int(r["n"]), "median_ms": num(r["median_ms"]), "p95_ms": num(r["p95_ms"])}
                for r in lat if r["config"] == "full" and r["scope"] != "end_to_end"
            ],
            "end_to_end": {
                "full_median_ms": num(full["median_ms"]), "full_p95_ms": num(full["p95_ms"]),
                "none_median_ms": num(none["median_ms"]), "none_p95_ms": num(none["p95_ms"]),
                "median_overhead_ms": round(num(full["median_ms"]) - num(none["median_ms"]), 2),
                "n": int(full["n"]),
            },
            "ablation_benign": [
                {"config": r["config"], "n": int(float(r["n"])), "false_blocks": int(float(r["false_blocks"] or 0)),
                 "false_block_rate": num(r["false_block_rate"])}
                for r in abl
            ],
            "timestamp": s["timestamp"],
            "git_commit": s["git_commit"],
        }
    md = (RES / "RESULTS.md").read_text(encoding="utf-8")
    m = re.search(r"Test suite at generation time: (\d+) passed, (\d+) failed, (\d+) skipped", md)
    out = {
        "tests": {"passed": int(m[1]), "failed": int(m[2]), "skipped": int(m[3])} if m else None,
        "policy_limits": json.loads((RES / "summary_dev.json").read_text(encoding="utf-8"))["policy_limits"],
        "splits": splits,
        "source": "eval/results (summary_*.json, latency_*.csv, ablation_*.csv, RESULTS.md)",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
