"""OpenAI-compatible / NVIDIA provider: offline tests with httpx.MockTransport. No live call is ever made."""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.main import app, get_llm_provider, get_pipeline
from app.generation.llm_info import llm_models, llm_status
from app.generation.openai_compat import OpenAICompatibleProvider, suggest_model
from app.generation.prompt import GenerationError, build_prompt, extract_sql
from app.generation.provider import (
    AuthError,
    ModelNotConfiguredError,
    ModelNotFoundError,
    ProviderError,
    ProviderNetworkError,
    ProviderServerError,
    RateLimitedError,
    ScriptedProvider,
    get_provider,
)
from app.generation.ratelimit import RateLimiter
from app.generation.verifier import self_check
from app.guards.policy import load_policy
from app.pipeline import Pipeline

KEY = "nvapi-SECRETSECRETSECRET1234567890"


def ok_chat(content="```sql\nSELECT 1\n```"):
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}]})


def make(handler, *, model="m1", sleeps=None, **kw):
    sleeps = [] if sleeps is None else sleeps
    client = httpx.Client(transport=httpx.MockTransport(handler))
    prov = OpenAICompatibleProvider(KEY, "https://example.test/v1/", model, name="nvidia", client=client,
                                    sleep=sleeps.append, limiter=RateLimiter(1000), **kw)
    return prov, sleeps


# ------------------------------------------------------------------ requests
def test_success_request_shape():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"], seen["auth"], seen["body"] = str(req.url), req.headers["authorization"], json.loads(req.content)
        return ok_chat()

    prov, _ = make(handler)
    assert prov.complete("sys", "hello") == "```sql\nSELECT 1\n```"
    assert seen["url"] == "https://example.test/v1/chat/completions"
    assert seen["auth"] == f"Bearer {KEY}"
    b = seen["body"]
    assert b["model"] == "m1" and b["temperature"] == 0 and 0 < b["max_tokens"] <= 2048
    assert b["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "hello"}]


def test_key_never_in_repr_or_errors():
    prov, _ = make(lambda r: httpx.Response(401, text=f"bad key {KEY}"))
    assert "SECRET" not in repr(prov)
    with pytest.raises(AuthError) as ei:
        prov.complete("s", "p")
    assert "SECRET" not in str(ei.value) and "SECRET" not in json.dumps(ei.value.to_dict())


@pytest.mark.parametrize("status", [401, 403])
def test_bad_key(status):
    prov, sleeps = make(lambda r: httpx.Response(status))
    with pytest.raises(AuthError) as ei:
        prov.complete("s", "p")
    assert ei.value.code == "bad_key" and sleeps == []


def test_bad_model_404():
    prov, _ = make(lambda r: httpx.Response(404))
    with pytest.raises(ModelNotFoundError, match="/llm/models"):
        prov.complete("s", "p")


def test_429_retry_after_then_success():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(429, headers={"retry-after": "3"}) if len(calls) == 1 else ok_chat("SELECT 2")

    prov, sleeps = make(handler)
    assert prov.complete("s", "p") == "SELECT 2"
    assert sleeps == [3.0] and len(calls) == 2


def test_429_persistent_gives_structured_error():
    prov, sleeps = make(lambda r: httpx.Response(429, headers={"retry-after": "2"}), max_attempts=3)
    with pytest.raises(RateLimitedError) as ei:
        prov.complete("s", "p")
    assert ei.value.retry_after == 2.0 and "retrying in 2s" in str(ei.value)
    assert ei.value.to_dict() == {"code": "rate_limited", "message": str(ei.value), "retry_after_s": 2.0}
    assert len(sleeps) == 2


def test_429_long_retry_after_is_not_slept():
    prov, sleeps = make(lambda r: httpx.Response(429, headers={"retry-after": "600"}))
    with pytest.raises(RateLimitedError) as ei:
        prov.complete("s", "p")
    assert sleeps == [] and ei.value.retry_after == 600.0


def test_5xx_backoff_then_success_and_exhaustion():
    calls = []

    def flaky(req):
        calls.append(1)
        return httpx.Response(503) if len(calls) < 3 else ok_chat("SELECT 3")

    prov, sleeps = make(flaky)
    assert prov.complete("s", "p") == "SELECT 3"
    assert sleeps == [2.0, 4.0]

    prov, sleeps = make(lambda r: httpx.Response(502))
    with pytest.raises(ProviderServerError):
        prov.complete("s", "p")
    assert len(sleeps) == 2


def test_network_error_is_typed_and_sanitised():
    def boom(req):
        raise httpx.ConnectError(f"failed for {KEY}")

    prov, _ = make(boom)
    with pytest.raises(ProviderNetworkError) as ei:
        prov.complete("s", "p")
    assert "SECRET" not in str(ei.value) and ei.value.code == "network"


def test_malformed_and_empty_responses():
    prov, _ = make(lambda r: httpx.Response(200, json={"nope": 1}))
    with pytest.raises(ProviderError, match="malformed"):
        prov.complete("s", "p")
    prov, _ = make(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": None}}]}))
    with pytest.raises(ProviderError, match="no content"):
        prov.complete("s", "p")


# -------------------------------------------------------------------- models
MODELS = {"data": [{"id": "acme/embed-v2"}, {"id": "acme/code-llama-70b-instruct"},
                   {"id": "acme/plain-chat"}, {"id": "acme/coder-33b-instruct"}]}


def test_suggest_model_prefers_code_instruct_and_skips_non_chat():
    assert suggest_model([m["id"] for m in MODELS["data"]]) == "acme/coder-33b-instruct"
    assert suggest_model(["acme/embed-v2", "acme/rerank-1"]) is None
    assert suggest_model([]) is None


def test_model_resolved_from_live_list_when_unset():
    seen = []

    def handler(req):
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json=MODELS)
        seen.append(json.loads(req.content)["model"])
        return ok_chat()

    prov, _ = make(handler, model=None)
    prov.complete("s", "p")
    prov.complete("s", "p")
    assert seen == ["acme/coder-33b-instruct"] * 2


def test_no_suitable_model_tells_user_to_pick():
    prov, _ = make(lambda r: httpx.Response(200, json={"data": [{"id": "x/embed"}]}), model=None)
    with pytest.raises(ModelNotConfiguredError, match="NVIDIA_MODEL"):
        prov.complete("s", "p")


def test_explicit_model_wins_over_suggestion():
    seen = []
    prov, _ = make(lambda r: (seen.append(json.loads(r.content)["model"]), ok_chat())[1], model="my/model")
    prov.complete("s", "p")
    assert seen == ["my/model"]


# ------------------------------------------------------------------- limiter
def test_limiter_sleeps_when_wait_is_short_and_raises_when_long():
    t = [0.0]
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        t[0] += s

    lim = RateLimiter(2, max_wait=30, clock=lambda: t[0], sleep=sleep)
    lim.acquire()
    t[0] = 40.0
    lim.acquire()
    assert lim.remaining() == 0
    lim.acquire()  # oldest hit is 40 s old: wait ~20 s <= 30, sleeps then succeeds
    assert len(sleeps) == 1 and 19 < sleeps[0] < 21
    lim2 = RateLimiter(1, max_wait=5, clock=lambda: 0.0, sleep=sleep)
    lim2.acquire()
    with pytest.raises(RateLimitedError) as ei:
        lim2.acquire()
    assert ei.value.retry_after == 60.0 and "retrying in 60s" in str(ei.value)


def test_limiter_window_recovers():
    t = [0.0]
    lim = RateLimiter(1, clock=lambda: t[0], sleep=lambda s: None)
    lim.acquire()
    assert lim.remaining() == 0
    t[0] = 61.0
    assert lim.remaining() == 1


def test_provider_uses_limiter_before_each_request():
    t = [0.0]
    lim = RateLimiter(3, max_wait=1, clock=lambda: t[0], sleep=lambda s: None)
    client = httpx.Client(transport=httpx.MockTransport(lambda r: ok_chat()))
    prov = OpenAICompatibleProvider(KEY, "https://example.test/v1", "m", client=client, limiter=lim)
    prov.complete("s", "p")
    assert prov.busy()  # 2 left: optional calls should skip
    prov.complete("s", "p")
    prov.complete("s", "p")
    with pytest.raises(RateLimitedError):
        prov.complete("s", "p")


# ---------------------------------------------------------------- extraction
@pytest.mark.parametrize("raw, want", [
    ("<think>I should use orders.\nSELECT nothing</think>\n```sql\nSELECT 1\n```", "SELECT 1"),
    ("reasoning with no opener\n</think>\nSELECT 2 FROM t", "SELECT 2 FROM t"),
    ("```SQL\nSELECT a FROM t;\n```\nThis query counts rows.", "SELECT a FROM t;"),
    ("Sure! Here you go:\n\nSELECT a\nFROM t\nWHERE x = 1;\n\nHope this helps!", "SELECT a\nFROM t\nWHERE x = 1;"),
    ("```\nWITH c AS (SELECT 1) SELECT * FROM c\n```", "WITH c AS (SELECT 1) SELECT * FROM c"),
    ("```sql\nSELECT 5 FROM t", "SELECT 5 FROM t"),                      # truncated, unterminated fence
    ("```text\nnotes\n```\n```sql\nSELECT 6\n```", "SELECT 6"),            # picks the block that is a query
    ("```sql\nSELECT 1; DROP TABLE users;\n```", "SELECT 1; DROP TABLE users;"),  # left for the validator
])
def test_extraction(raw, want):
    assert extract_sql(raw) == want


@pytest.mark.parametrize("raw", ["<think>only thinking, cut off", "CANNOT_ANSWER: nope",
                                 "<think>x</think>CANNOT_ANSWER: no column", "```sql\n```"])
def test_extraction_rejects(raw):
    with pytest.raises(GenerationError):
        extract_sql(raw)


def test_few_shot_never_names_hidden_columns():
    analyst = load_policy().role("analyst")
    visible = analyst.visible_schema()
    reduced = {t: [c for c in cols if not (t == "orders" and c == "region")] for t, cols in visible.items()}
    _, prompt = build_prompt("revenue?", analyst, reduced)
    assert "by region" not in prompt and "GROUP BY region" not in prompt
    _, full = build_prompt("revenue?", analyst, visible)
    assert "SELECT region, sum(total_amount)" in full


def test_system_prompt_demands_single_fenced_select():
    system, _ = build_prompt("q", SimpleNamespace(name="analyst"), {"orders": ["id"]})
    assert "```sql" in system and "ONE" in system and "SELECT" in system


# -------------------------------------------------------------- verifier / pipeline
def test_self_check_skips_when_rate_limited_or_busy():
    class Limited:
        name = "nvidia"

        def complete(self, s, p):
            raise RateLimitedError("rate limited, retrying in 9s", retry_after=9)

    assert "skipped" in self_check("q", "SELECT 1", Limited())["explanation"]

    class Busy(ScriptedProvider):
        name = "nvidia"
        busy = staticmethod(lambda: True)

    b = Busy(["YES"])
    assert self_check("q", "SELECT 1", b)["verdict"] == "unverified" and b.calls == []


def test_think_block_in_verifier_reply():
    assert self_check("q", "s", ScriptedProvider(["<think>hmm no</think>YES. fine"]))["verdict"] == "yes"


def test_pipeline_surfaces_rate_limit_as_structured_error():
    class Limited:
        name = "nvidia"

        def complete(self, s, p):
            raise RateLimitedError("rate limited, retrying in 12s", retry_after=12)

    res = Pipeline(load_policy(), executor=object(), provider=Limited()).run("total revenue by region", "analyst")
    assert res.decision == "error" and res.rule_id == "llm.rate_limited"
    assert res.llm_error == {"code": "rate_limited", "message": "rate limited, retrying in 12s", "retry_after_s": 12.0}
    assert res.rows == []


# --------------------------------------------------------------- config / API
def settings(**kw):
    base = dict(llm_provider="nvidia", llm_model=None, llm_base_url=None, rate_limit_per_min=30)
    return SimpleNamespace(**{**base, **kw})


def test_get_provider_nvidia_and_openai_compatible(monkeypatch):
    for v in ("NVIDIA_API_KEY", "NVIDIA_MODEL", "LLM_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    with pytest.raises(ProviderError, match="NVIDIA_API_KEY"):
        get_provider(settings())
    monkeypatch.setenv("NVIDIA_API_KEY", KEY)
    monkeypatch.setenv("NVIDIA_MODEL", "x/y")
    p = get_provider(settings())
    assert p.name == "nvidia" and p.model == "x/y" and p.base_url == "https://integrate.api.nvidia.com/v1"
    assert get_provider(settings()) is p  # shared, so one limiter covers the whole process
    with pytest.raises(ProviderError, match="LLM_API_KEY"):
        get_provider(settings(llm_provider="openai_compatible", llm_base_url="https://h/v1"))
    monkeypatch.setenv("LLM_API_KEY", "k")
    with pytest.raises(ProviderError, match="LLM_BASE_URL"):
        get_provider(settings(llm_provider="openai_compatible"))
    q = get_provider(settings(llm_provider="openai_compatible", llm_base_url="https://h/v1", llm_model="m"))
    assert q.name == "openai_compatible" and q.model == "m"


def test_settings_env(monkeypatch):
    from app.config import get_settings

    for v in ("LLM_PROVIDER", "T2S_LLM_PROVIDER", "LLM_MODEL", "T2S_LLM_MODEL", "LLM_RATE_LIMIT_PER_MIN"):
        monkeypatch.delenv(v, raising=False)
    assert get_settings().llm_provider == "mock" and get_settings().rate_limit_per_min == 30
    monkeypatch.setenv("T2S_LLM_PROVIDER", "anthropic")
    assert get_settings().llm_provider == "anthropic"
    monkeypatch.setenv("LLM_PROVIDER", "NVIDIA")
    monkeypatch.setenv("LLM_RATE_LIMIT_PER_MIN", "10")
    assert get_settings().llm_provider == "nvidia" and get_settings().rate_limit_per_min == 10


def test_status_mock_and_unconfigured(monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    s = llm_status(settings(llm_provider="mock"))
    assert (s["provider"], s["configured"], s["mode"]) == ("mock", False, "mock")
    assert "sent to the configured provider" in s["privacy_note"]
    s = llm_status(settings())
    assert s["configured"] is False and s["mode"] == "mock" and "NVIDIA_API_KEY" in s["detail"]


def test_status_live_has_no_key(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", KEY)
    monkeypatch.setenv("NVIDIA_MODEL", "acme/coder-33b-instruct")
    s = llm_status(settings())
    assert (s["provider"], s["model"], s["configured"], s["mode"]) == ("nvidia", "acme/coder-33b-instruct", True, "live")
    assert "role-visible schema" in s["privacy_note"] and "integrate.api.nvidia.com" in s["privacy_note"]
    assert "SECRET" not in json.dumps(s) and "nvapi" not in json.dumps(s)


@pytest.fixture
def client():
    app.dependency_overrides[get_pipeline] = lambda: SimpleNamespace(policy=load_policy(), audit=None)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_api_status_and_models_without_key(client, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "nvidia")
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    app.dependency_overrides[get_llm_provider] = lambda: None
    assert client.get("/llm/status").json()["configured"] is False
    body = client.get("/llm/models").json()
    assert body["models"] == [] and "NVIDIA_API_KEY" in body["reason"]


def test_api_models_live_and_error_paths_do_not_leak(client, monkeypatch, caplog):
    monkeypatch.setenv("LLM_PROVIDER", "nvidia")
    monkeypatch.setenv("NVIDIA_API_KEY", KEY)
    monkeypatch.setenv("NVIDIA_MODEL", "acme/gone")
    prov, _ = make(lambda r: httpx.Response(200, json=MODELS))
    app.dependency_overrides[get_llm_provider] = lambda: prov
    with caplog.at_level(logging.DEBUG):
        st = client.get("/llm/status")
        ms = client.get("/llm/models").json()
        assert st.json()["mode"] == "live"
        assert ms["suggested"] == "acme/coder-33b-instruct" and "acme/plain-chat" in ms["models"]
        assert "acme/gone" in ms["reason"]  # configured model missing from the live list

        bad, _ = make(lambda r: httpx.Response(401, text=KEY))
        app.dependency_overrides[get_llm_provider] = lambda: bad
        err = client.get("/llm/models").json()
        assert err["models"] == [] and err["error"]["code"] == "bad_key"

        limited, _ = make(lambda r: httpx.Response(429, headers={"retry-after": "7"}), max_attempts=1)
        app.dependency_overrides[get_llm_provider] = lambda: limited
        rl = client.get("/llm/models").json()
        assert rl["error"] == {"code": "rate_limited", "message": "rate limited, retrying in 7s", "retry_after_s": 7.0}
        everything = json.dumps([st.json(), ms, err, rl]) + caplog.text
    assert "SECRET" not in everything and "nvapi" not in everything


def test_llm_models_unsupported_provider():
    out = llm_models(SimpleNamespace(), {"provider": "anthropic", "configured": True, "model": "m"})
    assert out["models"] == [] and "does not support" in out["reason"]
