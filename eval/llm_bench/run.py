"""Small SQL-generation benchmark across NVIDIA-hosted models, through the FULL guard pipeline.

    .venv\\Scripts\\python -m eval.llm_bench.run --dry-run                 # offline, fake provider, no key, no DB
    .venv\\Scripts\\python -m eval.llm_bench.run --check-gold              # run only the gold SQL (needs the DB)
    .venv\\Scripts\\python -m eval.llm_bench.run                           # real run (NVIDIA_API_KEY + LLM_PROVIDER=nvidia in .env)
    .venv\\Scripts\\python -m eval.llm_bench.run --models gemma-4-31b-it,bolt   # substring match against the live list
    .venv\\Scripts\\python -m eval.llm_bench.run --summarize-only          # rebuild summary.json and RESULTS.md

Scoring is execution accuracy: the pipeline's masked, row-capped result for the model's SQL is compared with the
result of the hand-written gold SQL pushed through the same pipeline (same role, region, masking, row cap).
Rows are compared as a multiset (as a list only when the item sets `order_matters`). Numbers are rounded to 2 dp
and midnight timestamps are treated as dates. Policy items are scored separately (see `classify_policy`).

Free-tier hygiene: sequential calls, the provider's own rate limiter (default 30 req/min), the provider's retry
logic, a hard cap on total HTTP requests, and a clean abort (partial results kept) on repeated 429.
The API key is read from the environment/.env only and is never written anywhere.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.generation.provider import ProviderError  # noqa: E402
from app.pipeline import Pipeline  # noqa: E402
from eval.stats import bootstrap_ci, wilson_ci  # noqa: E402

BENCH_DIR = Path(__file__).resolve().parent
QUESTIONS_PATH = BENCH_DIR / "questions.jsonl"
RESULTS_DIR = BENCH_DIR / "results"

# Substrings looked up in the LIVE /models list, in order. Ids are never taken from here as truth.
CANDIDATE_HINTS = (
    "nemotron-3.5-bolt-e4b",
    "gemma-4-31b-it",
    "minimax-m3.1-flash",
    "mistral-large-4-1t-a52b",
    "starcoder2-15b",
)
DEFAULT_MAX_REQUESTS = 400
CONSECUTIVE_429_ABORT = 3
UNAVAILABLE_AFTER = 3  # consecutive non-429 provider errors at the start of a model: skip the model


class BenchAbort(BaseException):
    """Stops the whole run (request cap or repeated 429). BaseException so the pipeline's fail-closed
    `except Exception` cannot swallow it."""


# ------------------------------------------------------------------ questions
def load_questions(path: Path | str = QUESTIONS_PATH) -> list[dict]:
    items = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            items.append(json.loads(line))
    ids = [i["id"] for i in items]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate question ids")
    for it in items:
        if it["kind"] == "accuracy" and not it.get("gold_sql"):
            raise ValueError(f"{it['id']}: accuracy item without gold_sql")
        if it["role"] == "manager" and not it.get("region"):
            raise ValueError(f"{it['id']}: manager items need a region")
    return items


# -------------------------------------------------------------------- scoring
def normalize_value(v: Any) -> Any:
    """Make equal answers compare equal: numbers to 2 dp, midnight timestamps as dates."""
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, (int, float, Decimal)):
        return round(float(v), 2) + 0.0  # + 0.0 turns -0.0 into 0.0
    if isinstance(v, datetime):
        return v.date().isoformat() if v.time() == datetime.min.time() else v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def _norm_rows(rows) -> list[tuple]:
    return [tuple(normalize_value(c) for c in r) for r in rows]


def _key(v: Any) -> str:
    return repr(v)


def compare_results(gold_rows, pred_rows, order_matters: bool) -> dict[str, bool]:
    """Execution-accuracy comparison. `strict` compares rows positionally (column order matters);
    `lenient` also ignores column order within a row. Both ignore row order unless `order_matters`."""
    g, p = _norm_rows(gold_rows), _norm_rows(pred_rows)
    if len(g) != len(p):
        return {"strict": False, "lenient": False}
    if order_matters:
        strict = [tuple(map(_key, r)) for r in g] == [tuple(map(_key, r)) for r in p]
        lenient = [tuple(sorted(map(_key, r))) for r in g] == [tuple(sorted(map(_key, r))) for r in p]
    else:
        strict = Counter(tuple(map(_key, r)) for r in g) == Counter(tuple(map(_key, r)) for r in p)
        lenient = Counter(tuple(sorted(map(_key, r))) for r in g) == Counter(tuple(sorted(map(_key, r))) for r in p)
    return {"strict": strict, "lenient": lenient}


def classify_policy(res_decision: str, rule_id: str | None, reason: str, sql: str | None,
                    hidden: list[str]) -> str:
    """Outcome of a question that needs data the role cannot see.
    refused_by_guard      the pipeline blocked it (input guard, allowlist, ...): policy respected.
    refused_by_model      the model declined (CANNOT_ANSWER) so nothing ran: policy respected.
    answered_without_hidden  ran and returned rows; the SQL names no hidden column (the answer may still be
                          a substitute for what was asked: read it).
    leak_suspect          ran and the SQL text mentions a hidden column. Should be impossible; flagged if seen.
    other_error           provider or execution error, no verdict."""
    if res_decision == "block":
        return "refused_by_guard"
    if res_decision == "allow":
        low = (sql or "").lower()
        if any(re.search(rf"\b{re.escape(h.lower())}\b", low) for h in hidden):
            return "leak_suspect"
        return "answered_without_hidden"
    if "CANNOT_ANSWER" in (reason or ""):
        return "refused_by_model"
    return "other_error"


# --------------------------------------------------------------- HTTP counting
class CountingClient:
    """Wraps the provider's httpx client: counts every HTTP request (retries included), enforces the hard
    cap, and remembers the last chat-completion token usage. Never stores headers or bodies."""

    def __init__(self, inner, max_requests: int):
        self.inner = inner
        self.max_requests = max_requests
        self.requests = 0
        self.statuses: Counter = Counter()
        self.last_usage: dict | None = None

    def request(self, method, url, **kw):
        if self.requests >= self.max_requests:
            raise BenchAbort(f"hard request cap reached ({self.max_requests})")
        self.requests += 1
        resp = self.inner.request(method, url, **kw)
        self.statuses[resp.status_code] += 1
        if method == "POST" and resp.status_code // 100 == 2:
            try:
                u = resp.json().get("usage")
            except Exception:  # noqa: BLE001
                u = None
            prev = self.last_usage or {}
            if isinstance(u, dict):  # sum across the calls of one question (retries, self-correction)
                self.last_usage = {k: int(prev.get(k) or 0) + int(u.get(k) or 0)
                                   for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
        return resp

    def __getattr__(self, name):
        return getattr(self.inner, name)


# ------------------------------------------------------------------- dry run
class FakeProvider:
    """Offline stand-in. Answers with the gold SQL, except for deterministic faults so every outcome path
    (correct, wrong, block-then-correct, model refusal) is exercised. Needs no key and no network."""

    name = "fake"

    def __init__(self, items: list[dict], wrong_every: int = 5):
        self.gold = {" ".join(i["question"].split()): i for i in items}
        self.wrong_every = wrong_every
        self.calls = 0

    def complete(self, system: str, prompt: str) -> str:
        self.calls += 1
        q = prompt.rsplit("QUESTION: ", 1)[-1].strip()
        item = self.gold.get(q)
        if item is None or item["kind"] == "policy":
            return "CANNOT_ANSWER: the question needs data outside the visible schema"
        n = int(re.sub(r"\D", "", item["id"]) or 0)
        if "FEEDBACK (" in prompt:  # self-correction round: behave
            return f"```sql\n{item['gold_sql']}\n```"
        if n % self.wrong_every == 0:
            return "```sql\nSELECT count(*) FROM orders WHERE status = 'pending'\n```"
        if n % self.wrong_every == 1 and n > 1:
            return "```sql\nSELECT email FROM users\n```"  # policy-violating: blocked, then corrected
        return f"```sql\n{item['gold_sql']}\n```"


class FakeExecutor:
    """Deterministic executor: the same SQL text always returns the same rows. No database."""

    def explain(self, sql, role, region, *, sandbox=True):
        return {"Plan": {"Total Cost": 10.0, "Plan Rows": 5}}

    def execute(self, sql, role, region, *, sandbox=True):
        from app.types import ExecResult

        norm = " ".join(sql.lower().split())
        norm = re.sub(r"\s+limit\s+\d+\s*;?$", "", norm)  # the validator forces a LIMIT: ignore it
        h = int(hashlib.sha256(norm.encode()).hexdigest()[:8], 16)
        rows = [(f"k{i}", Decimal(h % 1000 + i)) for i in range(3)]
        return ExecResult(ok=True, columns=["k", "v"], rows=rows, row_count=3)


# -------------------------------------------------------------------- running
def _ms(res, key: str) -> float | None:
    v = res.layer_latency_ms.get(key)
    return round(v, 1) if v is not None else None


def gold_results(pipeline: Pipeline, items: list[dict]) -> dict[str, dict]:
    """Gold SQL through the same pipeline (same guards, masking, row cap). Run once, shared by all models."""
    out: dict[str, dict] = {}
    for it in items:
        if it["kind"] != "accuracy":
            continue
        res = pipeline.run(None, it["role"], it.get("region"), sql_override=it["gold_sql"])
        out[it["id"]] = {
            "decision": res.decision, "rule_id": res.rule_id, "reason": res.reason,
            "rows": [tuple(r) for r in res.rows], "columns": list(res.columns),
        }
    return out


def run_item(pipeline: Pipeline, client: CountingClient | None, item: dict, gold: dict | None, model: str) -> dict:
    before = 0
    if client is not None:
        client.last_usage = None
        before = client.requests
    t0 = time.perf_counter()
    res = pipeline.run(item["question"], item["role"], item.get("region"))
    wall_ms = (time.perf_counter() - t0) * 1000
    rec: dict[str, Any] = {
        "model": model, "id": item["id"], "kind": item["kind"], "difficulty": item["difficulty"],
        "role": item["role"], "question": item["question"],
        "sql_generated": res.sql_generated, "sql_executed": res.sql_executed,
        "decision": res.decision, "blocked_by": res.blocked_by, "rule_id": res.rule_id,
        "reason": (res.reason or "")[:300], "retries": res.retries,
        "rows": len(res.rows), "rows_preview": [[normalize_value(c) for c in r] for r in res.rows[:3]],
        "latency_ms": round(wall_ms, 1), "generation_ms": _ms(res, "generation"),
        "tokens": client.last_usage if client else None,
        "http_requests": (client.requests - before) if client else None,
        "provider_error": res.llm_error, "correct": None, "correct_lenient": None, "policy_outcome": None,
    }
    if item["kind"] == "accuracy":
        if res.decision == "allow" and gold and gold["decision"] == "allow":
            cmp = compare_results(gold["rows"], res.rows, item["order_matters"])
            rec["correct"], rec["correct_lenient"] = cmp["strict"], cmp["lenient"]
        else:
            rec["correct"] = rec["correct_lenient"] = False
    else:
        rec["policy_outcome"] = classify_policy(
            res.decision, res.rule_id, res.reason, res.sql_executed or res.sql_generated,
            item.get("hidden_columns", []))
    return rec


def rescore_file(pipeline: Pipeline, path: Path, items: list[dict], gold: dict[str, dict]) -> int:
    """Re-score stored records after a gold-SQL change WITHOUT calling the model: the SQL the model wrote
    (`sql_generated`) is re-run through the same pipeline (SQL override) and compared with the current gold.
    Only records whose decision was allow are re-scored. Returns how many `correct` flags changed."""
    by_id = {i["id"]: i for i in items}
    recs = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    changed = 0
    for r in recs:
        it = by_id.get(r["id"])
        if not it or it["kind"] != "accuracy" or r["decision"] != "allow" or not r.get("sql_executed"):
            continue
        res = pipeline.run(None, it["role"], it.get("region"), sql_override=r["sql_executed"])
        cmp = compare_results(gold[it["id"]]["rows"], res.rows, it["order_matters"]) if res.decision == "allow"             else {"strict": False, "lenient": False}
        if cmp["strict"] != r["correct"]:
            changed += 1
            r["rescored_from"] = r["correct"]
        r["correct"], r["correct_lenient"] = cmp["strict"], cmp["lenient"]
    path.write_text("".join(json.dumps(r) + chr(10) for r in recs), encoding="utf-8", newline=chr(10))
    return changed


def run_model(pipeline, client, model: str, items: list[dict], gold: dict[str, dict], out_path: Path,
              *, resume: bool = False, retry_errors: bool = False, time_budget_s: float | None = None, log=print) -> dict:
    """Run every item for one model, appending each record to out_path as it completes."""
    done: dict[str, dict] = {}
    if resume and out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                done[r["id"]] = r
        if retry_errors:  # re-run items that failed with a provider error (timeout, 429, 5xx); keep the rest
            done = {k: v for k, v in done.items() if not v.get("provider_error")}
            out_path.write_text("".join(json.dumps(v) + chr(10) for v in done.values()),
                                encoding="utf-8", newline=chr(10))
    elif out_path.exists():
        out_path.unlink()
    status: dict[str, Any] = {"model": model, "aborted": None, "unavailable": False}
    consec_429 = consec_err = 0
    started = time.monotonic()
    for it in items:
        if it["id"] in done:
            continue
        if time_budget_s is not None and time.monotonic() - started > time_budget_s:
            status["time_budget_exceeded"] = f"stopped after {time_budget_s:.0f}s; remaining items not run"
            break
        rec = run_item(pipeline, client, it, gold.get(it["id"]), model)
        with out_path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(rec) + "\n")
        done[it["id"]] = rec
        code = (rec["provider_error"] or {}).get("code")
        log(f"  {model} {it['id']} {rec['decision']:<5} correct={rec['correct']} "
            f"{rec['latency_ms'] / 1000:.1f}s{' ERR=' + code if code else ''}")
        consec_429 = consec_429 + 1 if code == "rate_limited" else 0
        consec_err = consec_err + 1 if code and code != "rate_limited" else 0
        if consec_429 >= CONSECUTIVE_429_ABORT:
            status["aborted"] = f"{consec_429} consecutive rate_limited results"
            break
        if consec_err >= UNAVAILABLE_AFTER and len(done) == consec_err:
            status["unavailable"] = True
            status["unavailable_reason"] = f"first {consec_err} requests failed with provider error {code}"
            break
    return status


# ------------------------------------------------------------------- summary
def _pct(vals: list[float], q: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def summarize_model(records: list[dict]) -> dict:
    acc = [r for r in records if r["kind"] == "accuracy"]
    pol = [r for r in records if r["kind"] == "policy"]
    flags = [1 if r["correct"] else 0 for r in acc]
    flags_len = [1 if r["correct_lenient"] else 0 for r in acc]
    rate, lo, hi = bootstrap_ci(flags) if flags else (None, None, None)
    lat = [r["latency_ms"] / 1000 for r in records]
    toks = [r["tokens"]["total_tokens"] for r in records if r.get("tokens") and r["tokens"].get("total_tokens")]
    return {
        "n_items": len(records), "n_accuracy_items": len(acc), "n_policy_items": len(pol),
        "execution_accuracy": rate, "ci95": [lo, hi] if flags else None,
        "wilson95": list(wilson_ci(sum(flags), len(flags))[1:]) if flags else None,
        "correct": sum(flags), "correct_lenient_columns": sum(flags_len),
        "guardrail_blocks": sum(1 for r in acc if r["decision"] == "block"),
        "blocked_ids": [r["id"] for r in acc if r["decision"] == "block"],
        "errors": sum(1 for r in acc if r["decision"] == "error"),
        "provider_errors": sum(1 for r in records if r.get("provider_error")),
        "error_ids": [r["id"] for r in acc if r["decision"] == "error"],
        "latency_s_p50": _pct(lat, 0.5), "latency_s_p95": _pct(lat, 0.95),
        "avg_retries": (sum(r["retries"] for r in acc) / len(acc)) if acc else None,
        "tokens_total": sum(toks) if toks else None,
        "policy_outcomes": dict(Counter(r["policy_outcome"] for r in pol)),
        "failed_ids": [r["id"] for r in acc if not r["correct"]],
    }


def build_summary(results_dir: Path, meta: dict | None = None) -> dict:
    per_model: dict[str, dict] = {}
    for p in sorted(results_dir.glob("*.jsonl")):
        recs = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
        if recs:
            per_model[recs[0]["model"]] = summarize_model(recs)
    scored = [m for m, s in per_model.items() if s["n_accuracy_items"]]
    failed_by_all = None
    if scored:
        failed_by_all = sorted(set.intersection(*[set(per_model[m]["failed_ids"]) for m in scored]))
    return {"meta": meta or {}, "models": per_model, "failed_by_all_models": failed_by_all}


def write_results_md(summary: dict, path: Path) -> None:
    m = summary["models"]
    meta = summary.get("meta", {})
    first = max(m.values(), key=lambda x: x["n_items"], default=None)
    order = sorted(m, key=lambda k: (-(m[k]["execution_accuracy"] or 0), m[k]["latency_s_p50"] or 1e9))
    L = ["# LLM SQL-generation benchmark (small dev set)", ""]
    L.append("**Read this first.** This is a small development benchmark: "
             f"{first['n_accuracy_items'] if first else 0} scored questions plus "
             f"{first['n_policy_items'] if first else 0} policy questions on one synthetic schema, written by the "
             "repo author, with hand-written gold SQL. The models were not tuned on it, but the questions were "
             "visible to whoever wrote the prompt. With n this small the 95% bootstrap intervals are wide and "
             "differences of a few questions are noise. Latency and failures vary with free-tier load. "
             "Each model was run once at temperature 0.")
    L.append("")
    if meta:
        L.append(f"Run: {meta.get('started', '?')} UTC, provider `{meta.get('provider', '?')}`, "
                 f"{meta.get('http_requests', '?')} HTTP requests in total (cap {meta.get('max_requests', '?')}), "
                 f"pipeline max retries {meta.get('pipeline_max_retries', '?')}, guards: all on.")
        L.append("")
    L += ["## Per model", "",
          "| model | exec. accuracy (strict) | 95% bootstrap CI | 95% Wilson CI | correct if column order is ignored | guardrail blocks | errors | latency p50 / p95 (s) |",
          "|---|---|---|---|---|---|---|---|"]
    for k in order:
        s = m[k]
        n = s["n_accuracy_items"]
        acc = "n/a" if s["execution_accuracy"] is None else f"{s['correct']}/{n} = {s['execution_accuracy'] * 100:.1f}%"
        ci = "n/a" if not s["ci95"] else f"[{s['ci95'][0] * 100:.1f}%, {s['ci95'][1] * 100:.1f}%]"
        wi = "n/a" if not s.get("wilson95") else f"[{s['wilson95'][0] * 100:.1f}%, {s['wilson95'][1] * 100:.1f}%]"
        p50 = "n/a" if s["latency_s_p50"] is None else f"{s['latency_s_p50']:.1f} / {s['latency_s_p95']:.1f}"
        L.append(f"| `{k}` | {acc} | {ci} | {wi} | {s['correct_lenient_columns']}/{n} | {s['guardrail_blocks']} | "
                 f"{s['errors']} (provider: {s['provider_errors']}) | {p50} |")
    L += ["", "Strict = result rows compared positionally (column order matters) as a multiset, order only when the "
          "question asks for one. Numbers are compared at 2 decimals. Guardrail blocks are accuracy questions on "
          "which the pipeline refused the model's SQL (counted as wrong); some are the model writing disallowed SQL, "
          "not a wrongly blocked correct query, see the notes below. Errors are non-block failures (provider, "
          "execution, no SQL). Latency is the full pipeline call per question, including any self-correction retries.", ""]
    L += ["## Policy-respecting outcomes (questions that need data the role cannot see)", "",
          "| model | refused by guard | refused by model | answered without hidden data | leak suspect | other error |",
          "|---|---|---|---|---|---|"]
    for k in order:
        po = m[k]["policy_outcomes"]
        L.append(f"| `{k}` | " + " | ".join(str(po.get(x, 0)) for x in (
            "refused_by_guard", "refused_by_model", "answered_without_hidden", "leak_suspect", "other_error")) + " |")
    L += ["", "No outcome here is scored right or wrong: refusing and answering without the hidden column are both "
          "policy-respecting; `leak suspect` would be a failure. Whether an answer that silently dropped or "
          "substituted a column is useful is a human judgment, see `results/*.jsonl`.", ""]
    fba = summary.get("failed_by_all_models")
    L += ["## Items every model failed", "", ", ".join(fba) if fba else "none", ""]
    if meta.get("notes"):
        L += ["## Notes on failures and gold-SQL review", "", *meta["notes"], ""]
    L += ["## Files", "", "`questions.jsonl` (items and gold SQL), `results/<model>.jsonl` (raw per-item records: "
          "SQL, decision, rule, rows, correctness, latency, tokens when returned, provider error), "
          "`results/summary.json`. Re-run: `.venv\\Scripts\\python -m eval.llm_bench.run`.", ""]
    path.write_text("\n".join(L), encoding="utf-8", newline="\n")


# ------------------------------------------------------------------ model list
def resolve_models(live: list[str], wanted: list[str] | None = None) -> tuple[list[str], list[str]]:
    """Map hints (exact ids or substrings) to ids present in the live list. Returns (resolved, unresolved)."""
    resolved, missing = [], []
    for h in wanted or list(CANDIDATE_HINTS):
        hit = h if h in live else next((m for m in live if h.lower() in m.lower()), None)
        if hit and hit not in resolved:
            resolved.append(hit)
        elif not hit:
            missing.append(h)
    return resolved, missing


def slug(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", model)


# ------------------------------------------------------------------------ main
def _load_env() -> None:
    from scripts.llm_smoke import load_dotenv

    load_dotenv(REPO_ROOT / ".env")


def _write_outputs(out_dir: Path, meta: dict) -> dict:
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8", newline="\n")
    summary = build_summary(out_dir, meta)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8", newline="\n")
    write_results_md(summary, out_dir / "RESULTS.md")
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="fake provider and fake executor: no key, no DB, no network")
    ap.add_argument("--check-gold", action="store_true", help="only run the gold SQL through the pipeline (needs the DB)")
    ap.add_argument("--summarize-only", action="store_true")
    ap.add_argument("--rescore", action="store_true",
                    help="after a gold-SQL fix: re-run stored model SQL through the pipeline and re-score (no model calls)")
    ap.add_argument("--models", help="comma-separated ids or substrings, matched against the live /models list")
    ap.add_argument("--questions", default=str(QUESTIONS_PATH))
    ap.add_argument("--out", help="results directory (default eval/llm_bench/results; dry-run uses a temp dir)")
    ap.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS)
    ap.add_argument("--rate", type=int, default=30, help="max LLM requests per minute")
    ap.add_argument("--limit", type=int, help="only the first N questions (smoke)")
    ap.add_argument("--resume", action="store_true", help="skip items already in a model's results file")
    ap.add_argument("--model-budget-s", type=float, default=1800.0,
                    help="wall-clock cap per model; remaining items are not run (recorded in meta.json)")
    ap.add_argument("--retry-errors", action="store_true",
                    help="with --resume: re-run items whose record has a provider error (timeouts, 429, 5xx)")
    args = ap.parse_args(argv)

    items = load_questions(args.questions)
    if args.limit:
        items = items[: args.limit]
    out_dir = Path(args.out) if args.out else (
        Path(tempfile.mkdtemp(prefix="llm_bench_dry_")) if args.dry_run else RESULTS_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.summarize_only:
        meta_path = out_dir / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        _write_outputs(out_dir, meta)
        print(f"wrote summary.json and RESULTS.md in {out_dir}")
        return 0

    _load_env()
    from app.config import get_settings
    from app.guards.policy import load_policy

    settings = get_settings()
    policy = load_policy(settings.policy_path)

    if args.dry_run:
        executor = FakeExecutor()
    else:
        from app.execution.executor import PostgresExecutor

        executor = PostgresExecutor(settings, policy.limits)

    # gold SQL once, through the same guards and caps
    gold_pipeline = Pipeline(policy, executor, provider=None, audit=None, max_retries=0, self_check_enabled=False)
    gold = gold_results(gold_pipeline, items)
    for k, v in gold.items():
        extra = f" {v['rule_id']}: {v['reason'][:100]}" if v["decision"] != "allow" else ""
        print(f"gold {k}: {v['decision']} rows={len(v['rows'])}{extra}")
    bad_gold = sorted(k for k, v in gold.items() if v["decision"] != "allow")
    if bad_gold:
        print(f"ERROR: gold SQL not allowed for {bad_gold}; fix the items first", file=sys.stderr)
        return 2
    if args.check_gold:
        return 0
    if args.rescore:
        rp = Pipeline(policy, executor, provider=None, audit=None, max_retries=0, self_check_enabled=False)
        for f in sorted(out_dir.glob("*.jsonl")):
            print(f"{f.name}: {rescore_file(rp, f, items, gold)} correct flag(s) changed")
        meta_path = out_dir / "meta.json"
        _write_outputs(out_dir, json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {})
        return 0

    from app.generation.openai_compat import NVIDIA_BASE_URL, OpenAICompatibleProvider
    from app.generation.ratelimit import RateLimiter

    key = limiter = None
    if args.dry_run:
        models = ["fake/dry-run-model"]
        print("dry run: fake provider, fake executor, nothing leaves the machine")
    else:
        key = os.environ.get("NVIDIA_API_KEY")
        if settings.llm_provider != "nvidia" or not key:
            print("Needs NVIDIA_API_KEY and LLM_PROVIDER=nvidia (in .env or the environment). Nothing was called.",
                  file=sys.stderr)
            return 2
        limiter = RateLimiter(args.rate, max_wait=70.0)  # wait for headroom instead of failing
        listing = OpenAICompatibleProvider(key, NVIDIA_BASE_URL, None, name="nvidia", limiter=limiter)
        try:
            live = listing.list_models()
        except ProviderError as exc:
            print(f"Could not list models: [{exc.code}] {exc}", file=sys.stderr)
            return 1
        wanted = [s.strip() for s in args.models.split(",")] if args.models else None
        models, missing = resolve_models(live, wanted)
        print(f"live models: {len(live)}; benchmarking: {models}; not in live list: {missing}")
        if not models:
            return 1

    started = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    total_requests = 0
    statuses: dict[str, dict] = {}
    aborted = None
    for model in models:
        client = None
        if args.dry_run:
            provider: Any = FakeProvider(items)
        else:
            provider = OpenAICompatibleProvider(key, NVIDIA_BASE_URL, model, name="nvidia", limiter=limiter)
            client = CountingClient(provider.client, max(0, args.max_requests - total_requests))
            provider.client = client
        pipeline = Pipeline(policy, executor, provider, audit=None, max_retries=settings.max_retries,
                            self_check_enabled=False)
        print(f"== {model}")
        try:
            statuses[model] = run_model(pipeline, client, model, items, gold, out_dir / f"{slug(model)}.jsonl",
                                        resume=args.resume, retry_errors=args.retry_errors,
                                        time_budget_s=args.model_budget_s)
        except BenchAbort as exc:
            statuses[model] = {"model": model, "aborted": str(exc)}
        if client is not None:
            total_requests += client.requests
            statuses[model]["http_requests"] = client.requests
            statuses[model]["http_statuses"] = {str(k): v for k, v in client.statuses.items()}
        if statuses[model].get("aborted"):
            aborted = statuses[model]["aborted"]
            print(f"ABORT: {aborted}; partial results kept", file=sys.stderr)
            break

    meta = {"started": started, "provider": "fake (dry run)" if args.dry_run else "nvidia",
            "max_requests": args.max_requests, "http_requests": total_requests,
            "pipeline_max_retries": settings.max_retries, "rate_limit_per_min": args.rate,
            "model_status": statuses, "aborted": aborted}
    meta_path = out_dir / "meta.json"
    if args.resume and meta_path.exists():
        old = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["http_requests"] += old.get("http_requests", 0)
        meta["model_status"] = {**old.get("model_status", {}), **statuses}
        meta["started"] = old.get("started", started)
        if "notes" in old:
            meta["notes"] = old["notes"]
    summary = _write_outputs(out_dir, meta)
    for k, s in summary["models"].items():
        print(f"{k}: correct {s['correct']}/{s['n_accuracy_items']} blocks={s['guardrail_blocks']} "
              f"errors={s['errors']} p50={s['latency_s_p50']}")
    print(f"results in {out_dir}")
    return 3 if aborted else 0


if __name__ == "__main__":
    raise SystemExit(main())
