"""Question -> SQL: schema retrieval, prompt, provider call, extraction."""
from __future__ import annotations

from app.generation.prompt import GenerationError, build_prompt, extract_sql
from app.generation.schema_retrieval import retrieve_schema

__all__ = ["GenerationError", "generate_sql"]


def generate_sql(question: str, role, policy, provider, feedback=None) -> str:
    schema = retrieve_schema(question, role, policy)
    system, prompt = build_prompt(question, role, schema, feedback)
    return extract_sql(provider.complete(system, prompt))
