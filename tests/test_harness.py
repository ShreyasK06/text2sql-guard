"""Harness, aggregation, figures and report, tested offline with fakes."""
import json
import math
from types import SimpleNamespace

import pandas as pd
import pytest

from app.types import LAYERS, ExecResult, GuardConfig, PipelineResult
from eval import harness
from eval.ablations import run_ablation as ra
from eval.harness import CONFIGS, CachingExecutor, load_items, run_item, run_split
from eval.oracle import OracleContext

LIMITS = SimpleNamespace(statement_timeout_ms=2000, max_rows=1000)


class FakePolicy:
    limits = LIMITS

    def sensitive_for(self, role):
        return []


class FakeExecutor:
    def __init__(self):
        self.calls = 0

    def execute(self, sql, role, region, *, sandbox=True):
        self.calls += 1
        return ExecResult(ok=True, rows=[(1,)], row_count=1)

    def explain(self, sql, role, region, *, sandbox=True):
        self.calls += 1
        if "bad" in sql:
            raise ValueError("bad plan")
        return {"cost": 1}


class FakePipeline:
    """Blocks anything containing DROP when ast_rules is on; everything else is allowed."""

    def __init__(self):
        self.policy = FakePolicy()
        self.executor = FakeExecutor()

    def run(self, question, role, region=None, *, sql_override=None, config=GuardConfig.all()):
        role_obj = SimpleNamespace(name=role)
        self.executor.execute(sql_override, role_obj, region, sandbox=config.on("sandbox"))
        blocked = "DROP" in sql_override and config.on("ast_rules")
        return PipelineResult(
            request_id="r", role=role, region=region, question=question,
            decision="block" if blocked else "allow",
            blocked_by="ast_rules" if blocked else None, rule_id="ast.non_select" if blocked else None,
            reason="x" * 300 if blocked else "", rows=[] if blocked else [(1,)],
            exec_result=None if blocked else ExecResult(ok=True, elapsed_ms=5.0),
            layer_latency_ms={"ast_rules": 1.0}, latency_ms=2.0, config=config.name(),
        )


def mk(id_, category, sql, oracle=None, **kw):
    d = {"id": id_, "split": "dev", "category": category, "subcategory": "s", "role": "analyst",
         "region": None, "entry": "sql", "question": None, "sql": sql}
    if oracle:
        d["oracle"] = oracle
    d.update(kw)
    return d


ITEMS = [
    mk("a1", "destructive_escalation", "DROP TABLE users", {"type": "executes"}),
    mk("a2", "exfiltration", "SELECT 1", {"type": "executes"}),
    mk("b1", "benign", "SELECT 2"),
    mk("b2", "benign", "SELECT DROP"),  # falsely blocked by the fake
]


# ------------------------------------------------------------------ configs / loading
def test_configs():
    assert len(CONFIGS) == 14
    assert list(CONFIGS)[0] == "none" and list(CONFIGS)[-1] == "full"
    assert CONFIGS["only:sandbox"].enabled == frozenset({"sandbox"})
    assert CONFIGS["without:sandbox"].enabled == frozenset(LAYERS) - {"sandbox"}
    for name, cfg in CONFIGS.items():
        assert cfg.name() == name


def test_load_items_validates(tmp_path):
    d = tmp_path / "v1"
    d.mkdir()
    good = mk("g", "benign", "SELECT 1")
    (d / "dev.jsonl").write_text(json.dumps(good) + "\n\n", encoding="utf-8")
    assert load_items("dev", root=tmp_path)[0]["id"] == "g"
    for bad in (
        mk("x", "weird", "SELECT 1"),
        mk("x", "benign", "SELECT 1", role="root"),
        mk("x", "benign", "SELECT 1", role="manager"),  # manager needs region
        mk("x", "exfiltration", "SELECT 1"),  # attack needs oracle
        mk("x", "benign", "SELECT 1", entry="question"),  # question required
    ):
        (d / "dev.jsonl").write_text(json.dumps(bad) + "\n", encoding="utf-8")
        with pytest.raises(ValueError):
            load_items("dev", root=tmp_path)
    (d / "dev.jsonl").write_text(json.dumps(good) + "\n" + json.dumps(good) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_items("dev", root=tmp_path)


# ------------------------------------------------------------------ caching executor
def test_caching_executor_memoises():
    inner = FakeExecutor()
    c = CachingExecutor(inner)
    role = SimpleNamespace(name="analyst")
    c.execute("SELECT 1", role, None, sandbox=True)
    c.execute("SELECT 1", role, None, sandbox=True)
    c.execute("SELECT 1", role, None, sandbox=False)  # different mode: miss
    c.execute("SELECT 1", SimpleNamespace(name="admin"), None, sandbox=True)  # different role: miss
    assert inner.calls == 3 and c.hits == 1 and c.misses == 3
    assert c.stats()["hit_rate"] == 0.25


def test_caching_executor_explain_errors_cached_and_reraised():
    inner = FakeExecutor()
    c = CachingExecutor(inner)
    role = SimpleNamespace(name="analyst")
    for _ in range(2):
        with pytest.raises(ValueError):
            c.explain("bad", role, None)
    assert inner.calls == 1
    assert c.explain("ok", role, None) == {"cost": 1}


def test_caching_executor_bypass():
    inner = FakeExecutor()
    c = CachingExecutor(inner)
    c.bypass = True
    role = SimpleNamespace(name="analyst")
    c.execute("SELECT 1", role, None)
    c.execute("SELECT 1", role, None)
    assert inner.calls == 2 and c.hits == 0


# ------------------------------------------------------------------ run_item / run_split
def test_run_item_records():
    p = FakePipeline()
    ctx = OracleContext(p.policy, owner_query=lambda s: [])
    full = run_item(p, ITEMS[0], CONFIGS["full"], ctx, "full")
    assert full["decision"] == "block" and full["blocked_by"] == "ast_rules"
    assert full["attack_succeeded"] is False and full["false_block"] is None
    assert len(full["reason"]) == 200
    none = run_item(p, ITEMS[0], CONFIGS["none"], ctx, "none")
    assert none["attack_succeeded"] is True
    ben = run_item(p, ITEMS[3], CONFIGS["full"], ctx, "full")
    assert ben["false_block"] is True and ben["attack_succeeded"] is None
    assert math.isclose(full["lat_ast_rules"], 1.0)


def test_run_split_shape():
    p = FakePipeline()
    df = run_split("dev", pipeline=p, items=ITEMS, progress=False)
    assert len(df) == 14 * len(ITEMS)
    assert "hit_rate" in df.attrs["cache"]
    assert set(df["config"]) == set(CONFIGS)


def test_latency_pass_bypasses_cache():
    p = FakePipeline()
    lat = harness.run_latency_pass("dev", pipeline=p, items=ITEMS, warmup=1)
    assert set(lat["config"]) == {"none", "full"} and set(lat["category"]) == {"benign"}
    assert isinstance(p.executor, CachingExecutor) and p.executor.bypass is False


# ------------------------------------------------------------------ aggregation
@pytest.fixture()
def df():
    return run_split("dev", pipeline=FakePipeline(), items=ITEMS, progress=False)


def test_live_ids(df):
    assert ra.live_ids(df) == {"a1", "a2"}


def test_aggregate_ablation_rows(df):
    agg = ra.aggregate_ablation(df)
    assert list(agg.columns) == ra.ABLATION_COLUMNS
    assert len(agg) == 14 * 6  # 4 categories + all_attacks + benign
    row = agg[(agg.config == "full") & (agg.category == "destructive_escalation")].iloc[0]
    assert row.n == 1 and row.blocked == 1 and row.block_rate == 1.0 and row.n_live == 1
    row = agg[(agg.config == "none") & (agg.category == "all_attacks")].iloc[0]
    assert row.n == 2 and row.blocked == 0 and row.block_rate == 0.0
    row = agg[(agg.config == "full") & (agg.category == "all_attacks")].iloc[0]
    assert row.block_rate == 0.5 and row.live_block_rate == 0.5
    ben = agg[(agg.config == "full") & (agg.category == "benign")].iloc[0]
    assert ben.n == 2 and ben.false_blocks == 1 and ben.false_block_rate == 0.5
    empty = agg[(agg.config == "full") & (agg.category == "prompt_injection")].iloc[0]
    assert empty.n == 0 and math.isnan(empty.block_rate)


def test_live_rate_excludes_non_live():
    d = pd.DataFrame([
        dict(id="x", category="exfiltration", config="none", attack_succeeded=False, false_block=None),
        dict(id="x", category="exfiltration", config="full", attack_succeeded=False, false_block=None),
        dict(id="y", category="exfiltration", config="none", attack_succeeded=True, false_block=None),
        dict(id="y", category="exfiltration", config="full", attack_succeeded=True, false_block=None),
    ])
    agg = ra.aggregate_ablation(d)
    row = agg[(agg.config == "full") & (agg.category == "exfiltration")].iloc[0]
    assert row.block_rate == 0.5 and row.n_live == 1 and row.live_block_rate == 0.0


def test_attribution_full_items_summary_latency(df):
    attr = ra.attribution_table(df)
    all_ = attr[attr.category == "all_attacks"].set_index("blocked_by")
    assert all_.loc["ast_rules", "n"] == 1 and all_.loc["none", "n"] == 1 and all_.loc["none", "n_succeeded"] == 1
    fi = ra.full_items_table(df)
    assert set(fi.config) == {"full", "none"} and fi[fi.id == "a1"].live.all()
    s = ra.build_summary(df, "dev", {"hits": 1}, {"max_rows": 1000}, git_commit="abc")
    assert s["n_attacks"] == 2 and s["n_live"] == 2 and s["n_benign"] == 2
    assert s["residual_attack_ids"] == ["a2"]
    assert s["false_blocks"][0]["id"] == "b2" and s["false_blocks"][0]["layer"] == "ast_rules"
    assert s["git_commit"] == "abc"
    json.dumps(s)  # serialisable
    lat = ra.latency_table(df)
    assert set(lat[lat.scope == "end_to_end"].config) == {"full", "none"}


def test_write_split_outputs_and_figures_and_report(df, tmp_path):
    from eval.plots import make_ablation_figure, make_ci_figure
    from eval.report import write_results_md

    lat = harness.run_latency_pass("dev", pipeline=FakePipeline(), items=ITEMS, warmup=1)
    ra.write_split_outputs("dev", df, lat, tmp_path, {"max_rows": 1000})
    ra.write_split_outputs("heldout", df, lat, tmp_path, {"max_rows": 1000})
    for name in ("raw/items_dev.csv", "ablation_dev.csv", "full_items_dev.csv", "attribution_dev.csv",
                 "latency_dev.csv", "summary_dev.json"):
        assert (tmp_path / name).exists()
    p1 = make_ablation_figure(tmp_path, tmp_path / "a.png")
    p2 = make_ci_figure(tmp_path, tmp_path / "b.png")
    for p in (p1, p2):
        assert p.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    items_root = tmp_path / "adv"
    (items_root / "v1").mkdir(parents=True)
    (items_root / "v1" / "dev.jsonl").write_text("\n".join(json.dumps(i) for i in ITEMS), encoding="utf-8")
    md = write_results_md(tmp_path, tmp_path / "RESULTS.md", items_root=items_root).read_text(encoding="utf-8")
    assert "## Headline" in md and "SELECT 1" in md and "b2" in md and "50.0%" in md


def test_figure_with_only_dev(df, tmp_path):
    from eval.plots import make_ablation_figure

    ra.write_split_outputs("dev", df, None, tmp_path)
    assert make_ablation_figure(tmp_path, tmp_path / "only_dev.png").exists()
    with pytest.raises(FileNotFoundError):
        make_ablation_figure(tmp_path / "empty", tmp_path / "x.png")
