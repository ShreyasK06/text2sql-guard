"""LLM provider interface, a deterministic mock generator, and thin HTTP clients for real providers.

The real providers were never called while building this project (no API keys were available);
they are covered only by request-shape tests with a mocked transport.
"""
from __future__ import annotations

import os
import re
from typing import Protocol

import httpx


class ProviderError(Exception):
    """Base class for LLM provider failures. `code` is a stable machine-readable tag for the API and UI."""

    code = "provider_error"
    retry_after: float | None = None

    def to_dict(self) -> dict:
        out: dict = {"code": self.code, "message": str(self)}
        if self.retry_after is not None:
            out["retry_after_s"] = round(self.retry_after, 1)
        return out


class AuthError(ProviderError):
    code = "bad_key"


class ModelNotFoundError(ProviderError):
    code = "bad_model"


class ModelNotConfiguredError(ProviderError):
    code = "no_model"


class RateLimitedError(ProviderError):
    code = "rate_limited"

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class ProviderNetworkError(ProviderError):
    code = "network"


class ProviderServerError(ProviderError):
    code = "server_error"


class LLMProvider(Protocol):
    name: str

    def complete(self, system: str, prompt: str) -> str: ...


# --------------------------------------------------------------------------- mock
def _parse_prompt(prompt: str) -> tuple[str, str, dict[str, list[str]]]:
    role = (re.search(r"^ROLE: (\w+)", prompt, re.M) or [None, "analyst"])[1]
    question = (re.search(r"^QUESTION: (.*)$", prompt, re.M) or [None, ""])[1]
    schema = {m[1]: [c.strip() for c in m[2].split(",")] for m in re.finditer(r"^TABLE (\w+)\((.*)\)$", prompt, re.M)}
    return role, question.lower(), schema


def _top_n(q: str, default: int = 10) -> int:
    m = re.search(r"\btop (\d+)\b|\b(\d+) (?:most|best|largest|biggest)\b", q)
    words = {"five": 5, "ten": 10, "three": 3, "twenty": 20}
    if m:
        return min(int(m.group(1) or m.group(2)), 100)
    for w, n in words.items():
        if re.search(rf"\b{w}\b", q):
            return n
    return default


def _mock_sql(q: str, role: str, schema: dict[str, list[str]]) -> str | None:
    has = lambda t, *cols: t in schema and all(c in schema[t] for c in cols)  # noqa: E731
    n = _top_n(q)
    year = re.search(r"\b(202[3-5])\b", q)
    where_year = f" WHERE extract(year FROM order_date) = {year.group(1)}" if year else ""

    if re.search(r"contact|email|phone", q) and re.search(r"customer|user", q):
        if has("users", "full_name", "email", "phone"):
            return "SELECT id, full_name, email, phone, city, state FROM users ORDER BY id LIMIT 50"
        return None
    if "salary" in q or "salaries" in q:
        if has("employees", "salary"):
            return ("SELECT department, round(avg(salary), 2) AS avg_salary FROM employees "
                    "GROUP BY department ORDER BY avg_salary DESC")
        return None
    if re.search(r"headcount|how many (employees|staff)|employees (by|per) department", q) and has("employees"):
        return "SELECT department, count(*) AS headcount FROM employees GROUP BY department ORDER BY headcount DESC"
    if re.search(r"hires? (by|per) year|hired each year", q) and has("employees", "hire_date"):
        return ("SELECT extract(year FROM hire_date) AS year, count(*) AS hires FROM employees "
                "GROUP BY 1 ORDER BY 1")
    if "ticket" in q and has("support_tickets"):
        if "open" in q:
            return "SELECT count(*) AS open_tickets FROM support_tickets WHERE status = 'open'"
        return "SELECT status, count(*) AS tickets FROM support_tickets GROUP BY status ORDER BY tickets DESC"
    if re.search(r"payment", q) and re.search(r"method|type", q) and has("payments"):
        return ("SELECT method, count(*) AS payments, sum(amount) AS total FROM payments "
                "GROUP BY method ORDER BY total DESC")
    if re.search(r"refund|cancel", q) and "rate" in q and has("orders"):
        return ("SELECT round(100.0 * count(*) FILTER (WHERE status IN ('cancelled', 'refunded')) / count(*), 2) "
                "AS cancel_or_refund_pct FROM orders")
    if re.search(r"top|best|most", q) and "product" in q and has("order_items") and has("products"):
        metric = "sum(oi.quantity)" if re.search(r"quantity|units|sold", q) else "sum(oi.quantity * oi.unit_price)"
        return (f"SELECT p.name, {metric} AS total FROM order_items AS oi JOIN products AS p ON p.id = oi.product_id "
                f"GROUP BY p.name ORDER BY total DESC LIMIT {n}")
    if re.search(r"expensive|priciest", q) and has("products"):
        return f"SELECT name, category, price FROM products ORDER BY price DESC LIMIT {n}"
    if "product" in q and re.search(r"(per|by|each) category", q) and has("products"):
        return "SELECT category, count(*) AS products FROM products GROUP BY category ORDER BY products DESC"
    if re.search(r"top|best|biggest", q) and re.search(r"customer|user", q) and has("orders"):
        if has("users", "full_name"):
            return ("SELECT u.id, u.full_name, sum(o.total_amount) AS spend FROM orders AS o "
                    f"JOIN users AS u ON u.id = o.user_id GROUP BY u.id, u.full_name ORDER BY spend DESC LIMIT {n}")
        return f"SELECT user_id, sum(total_amount) AS spend FROM orders GROUP BY user_id ORDER BY spend DESC LIMIT {n}"
    if re.search(r"customer|user", q) and re.search(r"(by|per) (state|region)", q) and has("users"):
        col = "state" if "state" in q else "region"
        return f"SELECT {col}, count(*) AS customers FROM users GROUP BY {col} ORDER BY customers DESC"
    if re.search(r"new (customer|user)s?|signups?", q) and has("users", "created_at"):
        return ("SELECT date_trunc('month', created_at) AS month, count(*) AS new_customers FROM users "
                "GROUP BY 1 ORDER BY 1")
    if re.search(r"average order|avg order|aov", q) and has("orders"):
        if "region" in q:
            return "SELECT region, round(avg(total_amount), 2) AS avg_order_value FROM orders GROUP BY region ORDER BY region"
        return f"SELECT round(avg(total_amount), 2) AS avg_order_value FROM orders{where_year}"
    if re.search(r"order", q) and re.search(r"(by|per) status|status breakdown|count by status", q) and has("orders"):
        return f"SELECT status, count(*) AS orders FROM orders{where_year} GROUP BY status ORDER BY orders DESC"
    if re.search(r"revenue|sales", q) and has("orders"):
        if "category" in q and has("order_items") and has("products"):
            return ("SELECT p.category, sum(oi.quantity * oi.unit_price) AS revenue FROM order_items AS oi "
                    "JOIN products AS p ON p.id = oi.product_id GROUP BY p.category ORDER BY revenue DESC")
        if "region" in q:
            return f"SELECT region, sum(total_amount) AS revenue FROM orders{where_year} GROUP BY region ORDER BY revenue DESC"
        if "month" in q:
            return (f"SELECT date_trunc('month', order_date) AS month, sum(total_amount) AS revenue FROM orders{where_year} "
                    "GROUP BY 1 ORDER BY 1")
        if "year" in q and not year:
            return "SELECT extract(year FROM order_date) AS year, sum(total_amount) AS revenue FROM orders GROUP BY 1 ORDER BY 1"
        return f"SELECT sum(total_amount) AS revenue FROM orders{where_year}"
    if re.search(r"recent|latest", q) and "order" in q and has("orders"):
        return f"SELECT id, user_id, order_date, status, total_amount FROM orders ORDER BY order_date DESC, id DESC LIMIT {n}"
    if re.search(r"how many orders|number of orders|order count", q) and has("orders"):
        return f"SELECT count(*) AS orders FROM orders{where_year}"
    return None


class MockProvider:
    """Rule-based question-to-SQL generator for the demo schema. Not a guard: it enforces nothing."""

    name = "mock"

    def complete(self, system: str, prompt: str) -> str:
        role, question, schema = _parse_prompt(prompt)
        if "FEEDBACK (" in prompt:
            return "CANNOT_ANSWER: the mock generator cannot repair a rejected query"
        sql = _mock_sql(question, role, schema)
        if sql is None:
            return "CANNOT_ANSWER: the mock generator has no template for this question (configure a real provider)"
        return f"```sql\n{sql}\n```"


class ScriptedProvider:
    """Returns canned responses in order (the last one repeats). For tests of the correction loop."""

    name = "scripted"

    def __init__(self, responses: list[str]):
        if not responses:
            raise ValueError("ScriptedProvider needs at least one response")
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, prompt: str) -> str:
        self.calls.append((system, prompt))
        return self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]


# ------------------------------------------------------------------ real providers
class AnthropicProvider:
    name = "anthropic"
    url = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key: str, model: str, client: httpx.Client | None = None):
        self.api_key, self.model = api_key, model
        self.client = client or httpx.Client(timeout=30)

    def complete(self, system: str, prompt: str) -> str:
        try:
            resp = self.client.post(
                self.url,
                headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": self.model, "max_tokens": 1024, "system": system,
                      "messages": [{"role": "user", "content": prompt}]},
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"anthropic request failed: {exc}") from exc
        if resp.status_code // 100 != 2:
            raise ProviderError(f"anthropic returned HTTP {resp.status_code}")
        try:
            return "".join(b["text"] for b in resp.json()["content"] if b.get("type") == "text")
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError("anthropic returned a malformed response") from exc


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, model: str, client: httpx.Client | None = None):
        self.api_key, self.model = api_key, model
        self.client = client or httpx.Client(timeout=30)

    def complete(self, system: str, prompt: str) -> str:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        try:
            resp = self.client.post(
                url,
                headers={"x-goog-api-key": self.api_key, "content-type": "application/json"},
                json={"systemInstruction": {"parts": [{"text": system}]},
                      "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                      "generationConfig": {"temperature": 0}},
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"gemini request failed: {exc}") from exc
        if resp.status_code // 100 != 2:
            raise ProviderError(f"gemini returned HTTP {resp.status_code}")
        try:
            parts = resp.json()["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError("gemini returned a malformed response") from exc


_SHARED: dict[tuple, LLMProvider] = {}


def get_provider(settings) -> LLMProvider:
    """Build the provider named by settings.llm_provider. Keys are read from the environment only."""
    kind = (settings.llm_provider or "mock").lower()
    if kind == "mock":
        return MockProvider()
    if kind in {"nvidia", "openai_compatible"}:
        from app.generation.openai_compat import provider_from_env

        return provider_from_env(kind, settings, _SHARED)
    if kind not in {"anthropic", "gemini"}:
        raise ProviderError(
            f"unknown LLM_PROVIDER: {kind!r} (use mock, nvidia, openai_compatible, anthropic or gemini)"
        )
    key_var = "ANTHROPIC_API_KEY" if kind == "anthropic" else "GEMINI_API_KEY"
    key = os.environ.get(key_var)
    if not key:
        raise ProviderError(f"{key_var} is not set")
    if not settings.llm_model:
        raise ProviderError("T2S_LLM_MODEL is not set")
    return (AnthropicProvider if kind == "anthropic" else GeminiProvider)(key, settings.llm_model)
