"""Read-only description of the configured LLM provider, for GET /llm/status and /llm/models.

Never includes the API key or any part of it.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

from app.generation.openai_compat import NVIDIA_BASE_URL, suggest_model
from app.generation.provider import ProviderError

_KEY_VARS = {"nvidia": "NVIDIA_API_KEY", "openai_compatible": "LLM_API_KEY",
             "anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY"}
_LABELS = {"nvidia": "NVIDIA", "openai_compatible": "OpenAI-compatible", "anthropic": "Anthropic",
           "gemini": "Gemini", "mock": "Mock"}


def _destination(kind: str, settings) -> str:
    if kind == "nvidia":
        return urlparse(getattr(settings, "llm_base_url", None) or NVIDIA_BASE_URL).netloc
    if kind == "openai_compatible":
        return urlparse(getattr(settings, "llm_base_url", None) or "").netloc or "the configured endpoint"
    return {"anthropic": "api.anthropic.com", "gemini": "generativelanguage.googleapis.com"}.get(kind, "")


def llm_status(settings) -> dict:
    kind = (settings.llm_provider or "mock").lower()
    var = _KEY_VARS.get(kind)
    configured = bool(var and os.environ.get(var))
    if kind == "openai_compatible" and not getattr(settings, "llm_base_url", None):
        configured = False
    if kind == "mock":
        model = "mock"
    elif kind == "nvidia":
        model = os.environ.get("NVIDIA_MODEL") or settings.llm_model
    else:
        model = settings.llm_model
    if configured:
        note = (f"Questions and the role-visible schema (only the tables and columns your role may see) are sent "
                f"to the configured provider ({_LABELS.get(kind, kind)}, {_destination(kind, settings)}). "
                f"Query results are not sent, but the optional self-check sends the question and the generated SQL.")
    else:
        note = ("Questions and the role-visible schema would be sent to the configured provider if a live one "
                "were enabled. With the mock provider nothing leaves this machine.")
    out = {"provider": kind, "model": model, "configured": configured,
           "mode": "live" if configured else "mock", "privacy_note": note}
    if kind != "mock" and not configured:
        out["detail"] = f"LLM_PROVIDER={kind} but {var or 'its key'} is not set"
    return out


def llm_models(provider, status: dict) -> dict:
    """Model list from the provider's /models endpoint, or an empty list with a reason."""
    base = {"provider": status["provider"], "models": [], "suggested": None, "reason": None, "error": None}
    if provider is None or not status["configured"]:
        base["reason"] = status.get("detail") or "No API key is configured, so no live model list is available."
        return base
    lister = getattr(provider, "list_models", None)
    if not callable(lister):
        base["reason"] = f"The {status['provider']} provider does not support model discovery."
        return base
    try:
        ids = lister()
    except ProviderError as exc:
        base["reason"] = str(exc)
        base["error"] = exc.to_dict()
        return base
    base["models"] = ids
    base["suggested"] = suggest_model(ids)
    if not ids:
        base["reason"] = "The provider returned an empty model list."
    elif status["model"] and status["model"] not in ids:
        base["reason"] = f"The configured model {status['model']!r} is not in the live list; pick one from it."
    return base
