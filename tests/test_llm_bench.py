"""LLM benchmark harness: scoring, dry-run end to end (fake provider, fake executor), abort paths. No key, no DB."""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal

import httpx
import pytest

from app.generation.openai_compat import OpenAICompatibleProvider
from app.generation.ratelimit import RateLimiter
from app.pipeline import Pipeline
from eval.llm_bench import run as bench


# ------------------------------------------------------------------ scoring
def test_normalize_numbers_and_dates():
    assert bench.normalize_value(Decimal("1.004")) == bench.normalize_value(1.0) == bench.normalize_value(1)
    assert bench.normalize_value(datetime(2024, 1, 1)) == bench.normalize_value(date(2024, 1, 1)) == "2024-01-01"
    assert bench.normalize_value(datetime(2024, 1, 1, 5)) != "2024-01-01"
    assert bench.normalize_value(None) is None


def test_multiset_ignores_row_order_unless_flagged():
    g = [("a", 1), ("b", 2), ("b", 2)]
    p = [("b", 2), ("a", 1), ("b", 2)]
    assert bench.compare_results(g, p, order_matters=False)["strict"]
    assert not bench.compare_results(g, p, order_matters=True)["strict"]
    assert bench.compare_results(g, list(g), order_matters=True)["strict"]


def test_multiset_counts_duplicates():
    assert not bench.compare_results([("a", 1), ("a", 1)], [("a", 1), ("b", 2)], False)["strict"]
    assert not bench.compare_results([("a", 1), ("b", 2)], [("a", 1), ("a", 1)], False)["strict"]
    assert not bench.compare_results([("a", 1)], [("a", 1), ("a", 1)], False)["strict"]


def test_column_order_strict_vs_lenient():
    res = bench.compare_results([("west", 5)], [(5, "west")], False)
    assert res == {"strict": False, "lenient": True}
    assert bench.compare_results([("west", 5)], [("west", 5, "x")], False) == {"strict": False, "lenient": False}


def test_numeric_type_differences_do_not_matter():
    assert bench.compare_results([(Decimal("10.50"), 3)], [(10.5, Decimal("3"))], False)["strict"]


@pytest.mark.parametrize("decision,rule,reason,sql,expected", [
    ("block", "input.restricted", "", None, "refused_by_guard"),
    ("error", "internal.error", "GenerationError: CANNOT_ANSWER: no salary column", None, "refused_by_model"),
    ("allow", None, "", "SELECT department, count(*) FROM employees GROUP BY 1", "answered_without_hidden"),
    ("allow", None, "", "SELECT department, avg(salary) FROM employees GROUP BY 1", "leak_suspect"),
    ("error", "llm.rate_limited", "rate limited", None, "other_error"),
])
def test_classify_policy(decision, rule, reason, sql, expected):
    assert bench.classify_policy(decision, rule, reason, sql, ["salary"]) == expected


def test_summarize_model_ci_and_counts():
    recs = [{"kind": "accuracy", "correct": c, "correct_lenient": c, "decision": d, "retries": 0, "latency_ms": ms,
             "tokens": None, "id": f"q{i}", "provider_error": None, "policy_outcome": None}
            for i, (c, d, ms) in enumerate([(True, "allow", 1000), (False, "block", 2000), (True, "allow", 3000),
                                            (False, "error", 4000)])]
    s = bench.summarize_model(recs)
    assert s["correct"] == 2 and s["execution_accuracy"] == 0.5
    assert s["guardrail_blocks"] == 1 and s["errors"] == 1 and s["failed_ids"] == ["q1", "q3"]
    lo, hi = s["ci95"]
    assert 0.0 <= lo <= 0.5 <= hi <= 1.0
    assert s["latency_s_p50"] == pytest.approx(2.5)


def test_resolve_models_uses_live_list_only():
    live = ["a/gemma-4-31b-it", "b/other", "nvidia/nemotron-3.5-bolt-e4b"]
    got, missing = bench.resolve_models(live, ["gemma-4-31b-it", "nemotron-3.5-bolt-e4b", "ghost-model"])
    assert got == ["a/gemma-4-31b-it", "nvidia/nemotron-3.5-bolt-e4b"] and missing == ["ghost-model"]


# ---------------------------------------------------------------- questions
def test_questions_file_is_well_formed():
    items = bench.load_questions()
    assert 22 <= len(items) <= 30
    assert {i["kind"] for i in items} == {"accuracy", "policy"}
    assert sum(1 for i in items if i["kind"] == "policy") == 2
    assert {i["difficulty"] for i in items if i["kind"] == "accuracy"} == {"easy", "medium", "hard"}
    for i in items:
        assert i["role"] in {"analyst", "manager", "admin"}
        if i["kind"] == "accuracy":
            assert i["gold_sql"].lstrip().upper().startswith(("SELECT", "WITH"))


def test_gold_sql_passes_static_guards(policy):
    """Every gold query must be allowed by the validator for its role (no DB needed)."""
    from app.guards.sql_validator import validate_sql

    for it in bench.load_questions():
        if it["kind"] == "accuracy":
            out = validate_sql(it["gold_sql"], policy.role(it["role"]), policy)
            assert not out.blocked, (it["id"], [r.reason for r in out.results if r.blocked])


# ------------------------------------------------------------------ dry run
def make_pipeline(policy, provider):
    return Pipeline(policy, bench.FakeExecutor(), provider, audit=None, max_retries=2, self_check_enabled=False)


def test_dry_run_end_to_end(tmp_path):
    rc = bench.main(["--dry-run", "--out", str(tmp_path)])
    assert rc == 0
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    recs = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
    items = bench.load_questions()
    assert [r["id"] for r in recs] == [i["id"] for i in items]
    acc = [r for r in recs if r["kind"] == "accuracy"]
    wrong = [r["id"] for r in acc if not r["correct"]]
    # the fake provider's deterministic wrong answers (q16 is a manager, who may see email, so it is wrong not blocked)
    assert wrong == ["q05", "q10", "q15", "q16", "q20"]
    # q06 first asks for a hidden column, is blocked, then answers correctly on the self-correction round
    q06 = next(r for r in recs if r["id"] == "q06")
    assert q06["correct"] and q06["retries"] == 1
    assert {r["policy_outcome"] for r in recs if r["kind"] == "policy"} <= {"refused_by_guard", "refused_by_model"}
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    s = summary["models"]["fake/dry-run-model"]
    assert s["correct"] == len(acc) - 5 and s["errors"] == 0
    assert summary["failed_by_all_models"] == wrong
    md = (tmp_path / "RESULTS.md").read_text(encoding="utf-8")
    assert "small development benchmark" in md and "fake/dry-run-model" in md
    for r in recs:  # records carry the documented fields
        assert {"model", "sql_generated", "decision", "rule_id", "rows", "latency_ms", "tokens",
                "provider_error"} <= set(r)


def test_resume_skips_finished_items(policy, tmp_path):
    items = bench.load_questions()
    gold = bench.gold_results(make_pipeline(policy, None), items)
    prov = bench.FakeProvider(items)
    out = tmp_path / "m.jsonl"
    bench.run_model(make_pipeline(policy, prov), None, "m", items[:5], gold, out, log=lambda *_: None)
    calls_after_first = prov.calls
    bench.run_model(make_pipeline(policy, prov), None, "m", items[:8], gold, out, resume=True, log=lambda *_: None)
    assert len(out.read_text(encoding="utf-8").splitlines()) == 8
    assert calls_after_first < prov.calls < calls_after_first + 8


def test_gold_blocked_by_policy_is_reported(policy):
    bad = [{"id": "x1", "kind": "accuracy", "role": "analyst", "region": None, "question": "q",
            "gold_sql": "SELECT email FROM users", "order_matters": False, "difficulty": "easy"}]
    g = bench.gold_results(make_pipeline(policy, None), bad)
    assert g["x1"]["decision"] == "block"


# ------------------------------------------------------- abort and request cap
def _provider_with_status(status: int, counter: list):
    def handler(request: httpx.Request) -> httpx.Response:
        counter.append(1)
        if status == 200:
            body = {"choices": [{"message": {"content": "```sql\nSELECT count(*) FROM orders\n```"}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
            return httpx.Response(200, json=body)
        return httpx.Response(status, headers={"retry-after": "0"}, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAICompatibleProvider("test-key-not-real", "http://x/v1", "m", name="nvidia", client=client,
                                    limiter=RateLimiter(10_000), max_attempts=2, sleep=lambda s: None)


def _setup(policy, status, cap=400, n=10):
    items = [i for i in bench.load_questions() if i["kind"] == "accuracy"][:n]
    gold = bench.gold_results(make_pipeline(policy, None), items)
    hits: list = []
    prov = _provider_with_status(status, hits)
    client = bench.CountingClient(prov.client, cap)
    prov.client = client
    return items, gold, hits, prov, client


def test_repeated_429_aborts_with_partial_results(policy, tmp_path):
    items, gold, hits, prov, client = _setup(policy, 429)
    out = tmp_path / "m.jsonl"
    status = bench.run_model(make_pipeline(policy, prov), client, "m", items, gold, out, log=lambda *_: None)
    assert status["aborted"] and "rate_limited" in status["aborted"]
    recs = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(recs) == bench.CONSECUTIVE_429_ABORT  # stopped early, partial results on disk
    assert all(r["provider_error"]["code"] == "rate_limited" for r in recs)
    assert all(r["decision"] == "error" and r["rows"] == 0 for r in recs)


def test_hard_request_cap_is_not_swallowed_by_the_pipeline(policy, tmp_path):
    items, gold, hits, prov, client = _setup(policy, 200, cap=3)
    out = tmp_path / "m.jsonl"
    with pytest.raises(bench.BenchAbort):
        bench.run_model(make_pipeline(policy, prov), client, "m", items, gold, out, log=lambda *_: None)
    assert len(hits) == 3 and client.requests == 3  # the 4th request was never sent


def test_token_usage_is_recorded(policy, tmp_path):
    items, gold, hits, prov, client = _setup(policy, 200, n=1)
    out = tmp_path / "m.jsonl"
    bench.run_model(make_pipeline(policy, prov), client, "m", items, gold, out, log=lambda *_: None)
    rec = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert rec["tokens"] and rec["tokens"]["total_tokens"] >= 15 and rec["http_requests"] >= 1


def test_unavailable_model_is_skipped_after_three_errors(policy, tmp_path):
    items, gold, hits, prov, client = _setup(policy, 404)
    status = bench.run_model(make_pipeline(policy, prov), client, "m", items, gold, tmp_path / "m.jsonl",
                             log=lambda *_: None)
    assert status["unavailable"] and len(hits) == bench.UNAVAILABLE_AFTER


def test_live_run_refuses_without_key(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setattr(bench, "_load_env", lambda: None)
    monkeypatch.setattr(bench, "FakeExecutor", bench.FakeExecutor)
    # main() builds the real executor (lazy connection) and runs gold: use a fake pipeline path instead
    monkeypatch.setattr("app.execution.executor.PostgresExecutor", lambda *a, **k: bench.FakeExecutor())
    assert bench.main(["--out", str(tmp_path), "--limit", "2"]) == 2
    assert "Nothing was called" in capsys.readouterr().err


def test_retry_errors_reruns_only_provider_error_items(policy, tmp_path):
    items = bench.load_questions()[:4]
    gold = bench.gold_results(make_pipeline(policy, None), items)
    prov = bench.FakeProvider(items)
    out = tmp_path / "m.jsonl"
    bench.run_model(make_pipeline(policy, prov), None, "m", items, gold, out, log=lambda *_: None)
    recs = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    recs[1]["provider_error"] = {"code": "network", "message": "x"}  # pretend q02 timed out
    out.write_text("".join(json.dumps(r) + chr(10) for r in recs), encoding="utf-8")
    before = prov.calls
    bench.run_model(make_pipeline(policy, prov), None, "m", items, gold, out, resume=True, retry_errors=True,
                    log=lambda *_: None)
    assert prov.calls == before + 1
    again = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert {r["id"] for r in again} == {i["id"] for i in items} and not any(r["provider_error"] for r in again)


def test_time_budget_stops_a_model(policy, tmp_path):
    items = bench.load_questions()[:4]
    gold = bench.gold_results(make_pipeline(policy, None), items)
    st = bench.run_model(make_pipeline(policy, bench.FakeProvider(items)), None, "m", items, gold,
                         tmp_path / "m.jsonl", time_budget_s=-1, log=lambda *_: None)
    assert "time_budget_exceeded" in st and not (tmp_path / "m.jsonl").exists()
