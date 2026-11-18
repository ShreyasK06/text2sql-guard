"""HTTP API for text2sql-guard. Run with: uvicorn app.api.main:app

SECURITY NOTE: there is NO authentication in this demo. `role` and `region` are
asserted by the caller in the request body and are trusted as given. A real
deployment must derive role and region from an authenticated session (for example
a verified token), never from client-supplied fields.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import decimal
import os
import uuid
from functools import lru_cache
from typing import Any

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.schemas import CatalogResponse, HealthResponse, PolicyResponse, QueryRequest, QueryResponse
from app.types import GuardConfig, PipelineResult

app = FastAPI(title="text2sql-guard", version="0.1.0")

# CORS is open only to localhost origins, so `npm run dev` (Vite on :5173) can call this API.
# The built front end is served from the same origin and needs no CORS.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_methods=["GET", "POST"],
    allow_headers=["content-type"],
)


@lru_cache(maxsize=1)
def get_pipeline():
    """Build the pipeline lazily, once. Tests override this dependency."""
    from app.pipeline import build_pipeline

    return build_pipeline()


def jsonable(value: Any) -> Any:
    """Make a database value JSON-safe: Decimal -> float, dates -> ISO strings."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, decimal.Decimal):
        return float(value) if value.is_finite() else str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return value.total_seconds()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [jsonable(v) for v in value]
    return str(value)


def _to_response(res: PipelineResult) -> dict[str, Any]:
    return {
        "request_id": res.request_id,
        "decision": res.decision,
        "blocked_by": res.blocked_by,
        "rule_id": res.rule_id,
        "reason": res.reason,
        "sql_generated": res.sql_generated,
        "sql_executed": res.sql_executed,
        "columns": list(res.columns),
        "rows": [[jsonable(c) for c in row] for row in res.rows],
        "row_count": len(res.rows),
        "masked_columns": list(res.masked_columns),
        "redactions": res.redactions,
        "retries": res.retries,
        "correction_log": jsonable(res.correction_log),
        "self_check": jsonable(res.self_check),
        "llm_error": jsonable(res.llm_error),
        "latency_ms": round(res.latency_ms, 3),
        "layer_latency_ms": {k: round(v, 3) for k, v in res.layer_latency_ms.items()},
        "guard_results": jsonable([g.to_dict() for g in res.guard_results]),
        "config": res.config,
    }


def _db_up() -> bool:
    try:
        import psycopg

        from app.config import get_settings

        with psycopg.connect(get_settings().owner_dsn(), connect_timeout=2) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


@app.get("/health", response_model=HealthResponse)
def health() -> dict[str, Any]:
    from app.config import get_settings

    return {"status": "ok", "db": _db_up(), "provider": get_settings().llm_provider}


def get_llm_provider():
    """The configured provider (shared with the pipeline), or None when it cannot be built. Tests override this."""
    from app.config import get_settings
    from app.generation.provider import ProviderError, get_provider

    try:
        return get_provider(get_settings())
    except ProviderError:
        return None


@app.get("/llm/status")
def llm_status_endpoint() -> dict[str, Any]:
    from app.config import get_settings
    from app.generation.llm_info import llm_status

    return llm_status(get_settings())


@app.get("/llm/models")
def llm_models_endpoint(provider=Depends(get_llm_provider)) -> dict[str, Any]:
    from app.config import get_settings
    from app.generation.llm_info import llm_models, llm_status

    return llm_models(provider, llm_status(get_settings()))


@app.get("/policy/{role}", response_model=PolicyResponse)
def policy_for_role(role: str, pipeline=Depends(get_pipeline)) -> dict[str, Any]:
    try:
        rp = pipeline.policy.role(role)
    except Exception:
        raise HTTPException(status_code=404, detail=f"unknown role: {role!r}") from None
    plain = {t: sorted(cols) for t, cols in sorted(rp.tables.items()) if cols}
    masked = {t: dict(sorted(m.items())) for t, m in sorted(rp.masked.items())}
    return {
        "role": rp.name,
        "description": rp.description,
        "row_scope": rp.row_scope,
        "plain_columns": plain,
        "masked_columns": masked,
        "visible_tables": sorted(rp.visible_schema()),
    }


@app.get("/catalog", response_model=CatalogResponse)
def catalog(pipeline=Depends(get_pipeline)) -> dict[str, Any]:
    """Full schema catalog plus the role list, so a client can show what each role does NOT see."""
    pol = pipeline.policy
    return {
        "roles": [
            {"name": r.name, "description": r.description, "row_scope": r.row_scope}
            for r in pol.roles.values()
        ],
        "schema": {t: list(cols) for t, cols in pol.schema.items()},
        "sensitive_columns": dict(pol.sensitive_columns),
        "limits": dataclasses.asdict(pol.limits),
    }


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest, pipeline=Depends(get_pipeline)) -> dict[str, Any]:
    if req.layers is not None and os.environ.get("T2S_ALLOW_LAYER_OVERRIDE") != "1":
        # Switching guard layers off is an ablation/demo feature and must never be caller-controlled by default.
        raise HTTPException(status_code=403, detail="layer override is disabled (set T2S_ALLOW_LAYER_OVERRIDE=1)")
    config = GuardConfig.all() if req.layers is None else GuardConfig.only(*req.layers)
    res = pipeline.run(
        req.question,
        req.role,
        req.region,
        sql_override=req.sql,
        config=config,
    )
    return _to_response(res)


@app.get("/audit")
def audit(n: int = 50, pipeline=Depends(get_pipeline)) -> list[dict[str, Any]]:
    n = max(1, min(n, 500))
    log = getattr(pipeline, "audit", None)
    if log is None:
        return []
    return jsonable(log.tail(n))


# ---------------------------------------------------------------- web app
# The React front end (frontend/, built with `npm run build`) is served at "/" when frontend/dist
# exists. It uses hash routing, so no SPA fallback is needed. Registered last so every API route wins.
FRONTEND_DIST = Path(os.environ.get("T2S_FRONTEND_DIST") or Path(__file__).resolve().parent.parent.parent / "frontend" / "dist")


def mount_frontend(application: FastAPI, dist: Path) -> bool:
    """Serve a built front end at "/". Returns False (and mounts nothing) when there is no build."""
    if not (dist / "index.html").is_file():
        return False
    application.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return True


mount_frontend(app, FRONTEND_DIST)
