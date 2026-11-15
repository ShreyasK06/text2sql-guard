"""One entry point that reproduces every number and figure.

    python scripts/reproduce.py [--skip-tests] [--splits dev heldout] [--no-db-up]

Steps: (1) database up and reachable, (2) test suite, (3) ablation over the requested splits,
(4) figures, (5) RESULTS.md and a printed headline table. Nothing is fabricated: if the database is
unreachable the script says which layers cannot be evaluated and exits non-zero.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
os.chdir(REPO_ROOT)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

RESULTS = REPO_ROOT / "eval" / "results"
TABLES = ("users", "employees", "products", "orders", "order_items", "payments", "support_tickets", "api_keys")


def step(msg: str) -> None:
    print(f"\n=== {msg}", flush=True)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=REPO_ROOT, **kw)


def check_db() -> dict[str, int] | None:
    """Return row counts per table, or None when the database is unreachable."""
    try:
        import psycopg

        from app.config import get_settings

        with psycopg.connect(get_settings().owner_dsn(), connect_timeout=5) as conn:
            return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TABLES}
    except Exception as exc:  # noqa: BLE001
        print(f"database check failed: {type(exc).__name__}: {exc}")
        return None


def parse_pytest(output: str) -> dict[str, int]:
    counts = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0}
    last = [l for l in output.strip().splitlines() if re.search(r"\d+ (passed|failed|skipped|error)", l)]
    if last:
        for n, word in re.findall(r"(\d+) (passed|failed|skipped|errors?|deselected)", last[-1]):
            key = "errors" if word.startswith("error") else word
            if key in counts:
                counts[key] = int(n)
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-tests", action="store_true")
    ap.add_argument("--splits", nargs="+", help="splits to evaluate (default: every split whose jsonl exists)")
    ap.add_argument("--no-db-up", action="store_true", help="do not run scripts/db.py up first")
    args = ap.parse_args(argv)

    # (1) database
    step("1/5 database")
    if not args.no_db_up:
        db_script = REPO_ROOT / "scripts" / "db.py"
        if db_script.exists():
            run([sys.executable, str(db_script), "up"])
        else:
            print("scripts/db.py not found; skipping 'db up'")
    counts = check_db()
    if counts is None or counts.get("users", 0) == 0:
        print("\nThe database is not usable, so these layers CANNOT be evaluated:")
        print("  cost_gate (EXPLAIN needs a live planner), sandbox (read-only role, RLS, timeouts),")
        print("  and the execution-based oracles (contains_sensitive, contains_any, resource).")
        print("Static layers (input_guard, allowlist, ast_rules, output_guard) are covered only by unit tests.")
        print("No results were produced.")
        return 2
    print("row counts: " + ", ".join(f"{t}={n}" for t, n in counts.items()))

    # (2) tests
    RESULTS.mkdir(parents=True, exist_ok=True)
    if not args.skip_tests:
        step("2/5 test suite")
        proc = run([sys.executable, "-m", "pytest", "-q"], capture_output=True, text=True)
        print(proc.stdout[-3000:])
        tr = parse_pytest(proc.stdout)
        tr["exit_code"] = proc.returncode
        (RESULTS / "test_run.json").write_text(json.dumps(tr, indent=2), encoding="utf-8")
        print("tests:", tr)
    else:
        step("2/5 test suite (skipped)")

    # (3) ablation
    step("3/5 ablation")
    from eval.harness import ADVERSARIAL_DIR

    splits = args.splits or [s for s in ("dev", "heldout") if (ADVERSARIAL_DIR / "v1" / f"{s}.jsonl").exists()]
    if not splits:
        print("no attack sets found under eval/adversarial/v1/")
        return 3
    cmd = [sys.executable, "-m", "eval.ablations.run_ablation", "--out", str(RESULTS)]
    for s in splits:
        cmd += ["--split", s]
    if run(cmd).returncode != 0:
        print("ablation run failed")
        return 4

    # (4) figures, (5) report
    step("4/5 figures")
    from eval.plots import make_ablation_figure, make_ci_figure

    print(make_ablation_figure(RESULTS, splits=tuple(splits)))
    print(make_ci_figure(RESULTS, splits=tuple(splits)))

    step("5/5 report")
    from eval.report import write_results_md

    print(write_results_md(RESULTS, RESULTS / "RESULTS.md"))
    print("\nheadline (full pipeline):")
    print(f"{'split':8} {'attacks':>8} {'live':>6} {'block rate all [95% CI]':>28} {'block rate live [95% CI]':>28} {'benign':>7} {'false-block [95% CI]':>24}")
    for s in splits:
        d = json.loads((RESULTS / f"summary_{s}.json").read_text(encoding="utf-8"))

        def f(x):
            if not x or x.get("rate") is None:
                return "n/a"
            return f"{100 * x['rate']:.1f}% [{100 * x['ci_lo']:.1f}, {100 * x['ci_hi']:.1f}]"

        print(f"{s:8} {d['n_attacks']:>8} {d['n_live']:>6} {f(d['full_block_rate_all']):>28} "
              f"{f(d['full_block_rate_live']):>28} {d['n_benign']:>7} {f(d['false_block_rate']):>24}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
