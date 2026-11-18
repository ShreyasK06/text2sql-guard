"""OpenAI-compatible chat-completions provider (NVIDIA build.nvidia.com, or any compatible endpoint).

Never called live while building this repo (no key was available); covered by httpx.MockTransport tests.
The API key lives only in a private attribute: it is never put in an error message, repr or log line.
"""
from __future__ import annotations

import email.utils
import os
import re
import time

import httpx

from app.generation.provider import (
    AuthError,
    ModelNotConfiguredError,
    ModelNotFoundError,
    ProviderError,
    ProviderNetworkError,
    ProviderServerError,
    RateLimitedError,
)
from app.generation.ratelimit import RateLimiter

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

# Keyword scoring over the LIVE model list. These are hints for choosing a default, not known-good ids.
_PREFER = (("coder", 4), ("code", 2), ("instruct", 3), ("chat", 1), ("-it", 1))
_AVOID = ("embed", "rerank", "reward", "guard", "safety", "vision", "vlm", "clip", "parse", "retriever",
          "diffusion", "image", "audio", "tts", "asr", "translate", "classif", "pii")


def suggest_model(model_ids: list[str]) -> str | None:
    """Pick a code-capable instruct model from a live list, or None when nothing looks suitable."""
    best, best_score = None, 0
    for mid in sorted(model_ids):
        low = mid.lower()
        if any(a in low for a in _AVOID):
            continue
        score = sum(w for k, w in _PREFER if k in low)
        if score > best_score:
            best, best_score = mid, score
    return best


def _retry_after(resp: httpx.Response) -> float | None:
    raw = (resp.headers.get("retry-after") or "").strip()
    if not raw:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", raw):
        return float(raw)
    try:
        when = email.utils.parsedate_to_datetime(raw)
        return max(0.0, when.timestamp() - time.time())
    except (TypeError, ValueError):
        return None


class OpenAICompatibleProvider:
    def __init__(
        self,
        api_key: str,
        base_url: str = NVIDIA_BASE_URL,
        model: str | None = None,
        *,
        name: str = "openai_compatible",
        client: httpx.Client | None = None,
        limiter: RateLimiter | None = None,
        max_tokens: int = 1024,
        timeout: float = 60.0,
        max_attempts: int = 3,
        max_retry_wait: float = 30.0,
        sleep=time.sleep,
    ):
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.name = name
        self.client = client or httpx.Client(timeout=timeout)
        self.limiter = limiter or RateLimiter()
        self.max_tokens = max_tokens
        self.max_attempts = max(1, max_attempts)
        self.max_retry_wait = max_retry_wait
        self._sleep = sleep

    def __repr__(self) -> str:
        return f"OpenAICompatibleProvider(name={self.name!r}, base_url={self.base_url!r}, model={self.model!r})"

    # ------------------------------------------------------------------ http
    def _request(self, method: str, path: str, json: dict | None = None) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"}
        last: ProviderError | None = None
        for attempt in range(self.max_attempts):
            self.limiter.acquire()
            try:
                resp = self.client.request(method, self.base_url + path, headers=headers, json=json)
            except httpx.HTTPError as exc:
                # exception type only, never its text
                raise ProviderNetworkError(f"{self.name}: network error ({type(exc).__name__})") from None
            status = resp.status_code
            if status // 100 == 2:
                return resp
            if status in (401, 403):
                raise AuthError(f"{self.name}: HTTP {status}, the API key was rejected (check it and its access)")
            if status == 404:
                raise ModelNotFoundError(
                    f"{self.name}: HTTP 404, model or endpoint not found; pick a model id from GET /llm/models"
                )
            if status == 429 or status >= 500:
                wait = _retry_after(resp)
                if status == 429:
                    last = RateLimitedError(f"{self.name}: rate limited by the provider", retry_after=wait)
                else:
                    last = ProviderServerError(f"{self.name}: HTTP {status} from the provider")
                delay = wait if wait is not None else min(2.0 * (2 ** attempt), 16.0)
                if attempt == self.max_attempts - 1 or delay > self.max_retry_wait:
                    break
                self._sleep(delay)
                continue
            raise ProviderError(f"{self.name}: unexpected HTTP {status}")
        assert last is not None
        if isinstance(last, RateLimitedError):
            ra = last.retry_after
            msg = f"rate limited, retrying in {ra:.0f}s" if ra is not None else "rate limited, try again shortly"
            raise RateLimitedError(msg, retry_after=ra)
        raise last

    # ------------------------------------------------------------------- api
    def list_models(self) -> list[str]:
        try:
            data = self._request("GET", "/models").json()["data"]
            return sorted({m["id"] for m in data if isinstance(m, dict) and m.get("id")})
        except (KeyError, TypeError, ValueError):
            raise ProviderError(f"{self.name}: malformed /models response") from None

    def busy(self) -> bool:
        """True when little rate-limit headroom is left; optional calls (self-check) should skip."""
        return self.limiter.remaining() <= 2

    def _resolve_model(self) -> str:
        if self.model:
            return self.model
        picked = suggest_model(self.list_models())
        if not picked:
            raise ModelNotConfiguredError(
                "no model configured and none of the live models looked suitable; "
                "set NVIDIA_MODEL (or LLM_MODEL) to an id from GET /llm/models"
            )
        self.model = picked
        return picked

    def complete(self, system: str, prompt: str) -> str:
        model = self._resolve_model()
        resp = self._request(
            "POST",
            "/chat/completions",
            {
                "model": model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": self.max_tokens,
                "stream": False,
            },
        )
        try:
            content = resp.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError):
            raise ProviderError(f"{self.name}: malformed chat completion response") from None
        if not isinstance(content, str) or not content.strip():
            raise ProviderError(f"{self.name}: the model returned no content")
        return content


def provider_from_env(kind: str, settings, cache: dict | None = None) -> OpenAICompatibleProvider:
    env = os.environ.get
    if kind == "nvidia":
        key = env("NVIDIA_API_KEY")
        if not key:
            raise ProviderError("NVIDIA_API_KEY is not set")
        base = getattr(settings, "llm_base_url", None) or NVIDIA_BASE_URL
        model = env("NVIDIA_MODEL") or getattr(settings, "llm_model", None) or None
        name = "nvidia"
    else:
        key = env("LLM_API_KEY")
        base = getattr(settings, "llm_base_url", None)
        if not key:
            raise ProviderError("LLM_API_KEY is not set")
        if not base:
            raise ProviderError("LLM_BASE_URL is not set")
        model = getattr(settings, "llm_model", None) or None
        name = "openai_compatible"
    ck = (name, base, model, hash(key))
    if cache is not None and ck in cache:
        return cache[ck]
    prov = OpenAICompatibleProvider(
        key, base, model, name=name, limiter=RateLimiter(getattr(settings, "rate_limit_per_min", 30)),
    )
    if cache is not None:
        cache[ck] = prov
    return prov
