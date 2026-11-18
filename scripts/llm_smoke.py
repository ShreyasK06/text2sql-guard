"""Live smoke test for a real LLM provider (default: NVIDIA). Needs a key; makes real network calls.

    set NVIDIA_API_KEY=nvapi-...   (or put it in .env)   and   LLM_PROVIDER=nvidia
    .venv\\Scripts\\python scripts\\llm_smoke.py

It lists the models the key can see, then sends three plain analytics questions through the full guard
pipeline (the seeded Postgres database must be up: python scripts/db.py up) and prints a short table.
The key is read from the environment or .env only and is never printed.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

QUESTIONS = [
    "What is the total revenue by region?",
    "Which five products sold the most units?",
    "How many orders are there per status?",
]


def load_dotenv(path: Path) -> None:
    """Minimal .env reader (no dependency). Existing environment variables win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if v and k not in os.environ:
            os.environ[k] = v


def die(msg: str, code: int = 2) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    from app.config import get_settings
    from app.generation.llm_info import llm_status
    from app.generation.openai_compat import suggest_model
    from app.generation.provider import ProviderError, get_provider

    settings = get_settings()
    if settings.llm_provider == "mock":
        if os.environ.get("NVIDIA_API_KEY"):
            die("NVIDIA_API_KEY is set but LLM_PROVIDER is mock. Set LLM_PROVIDER=nvidia to opt in; "
                "questions and the role-visible schema will then be sent to NVIDIA.")
        die("No live provider configured. Get a free key at https://build.nvidia.com, then set "
            "NVIDIA_API_KEY=nvapi-... and LLM_PROVIDER=nvidia in .env (see README: 'Using a free "
            "NVIDIA-hosted model'). Nothing was called.")
    status = llm_status(settings)
    if not status["configured"]:
        die(f"{status.get('detail', 'provider not configured')}. Nothing was called.")

    try:
        provider = get_provider(settings)
        models = provider.list_models()
    except ProviderError as exc:
        die(f"Could not list models: [{exc.code}] {exc}", 1)
    print(f"provider={status['provider']} configured_model={status['model'] or '(auto)'} "
          f"models_visible={len(models)}")
    for m in models[:15]:
        print(f"  {m}")
    if len(models) > 15:
        print(f"  ... and {len(models) - 15} more (GET /llm/models lists all)")
    if not status["model"]:
        print(f"suggested default (keyword heuristic, unverified): {suggest_model(models) or 'none, set NVIDIA_MODEL'}")
    elif status["model"] not in models:
        die(f"Configured model {status['model']!r} is not in the live list. Set NVIDIA_MODEL to one of the above.", 1)

    try:
        from app.pipeline import build_pipeline

        pipeline = build_pipeline(with_audit=False)
    except Exception as exc:  # noqa: BLE001
        die(f"Could not build the pipeline: {type(exc).__name__}: {exc}", 1)

    rows = []
    for q in QUESTIONS:
        t0 = time.perf_counter()
        res = pipeline.run(q, "analyst")
        ms = (time.perf_counter() - t0) * 1000
        detail = f"{len(res.rows)} rows" if res.decision == "allow" else f"{res.rule_id}: {res.reason[:60]}"
        sql = " ".join((res.sql_generated or "-").split())
        rows.append((q, sql if len(sql) <= 70 else sql[:67] + "...", f"{res.decision} ({detail})", f"{ms:,.0f} ms"))

    head = ("question", "sql", "decision", "latency")
    w = [max(len(r[i]) for r in [head, *rows]) for i in range(4)]
    fmt = "  ".join(f"{{:<{x}}}" for x in w)
    print()
    print(fmt.format(*head))
    print(fmt.format(*("-" * x for x in w)))
    for r in rows:
        print(fmt.format(*r))
    ok = sum(1 for r in rows if r[2].startswith("allow"))
    print(f"\n{ok}/{len(rows)} allowed. Read the SQL yourself: this smoke test does not score correctness.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
