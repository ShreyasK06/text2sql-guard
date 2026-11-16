"""Append-only JSONL audit log. Result rows are never written (they may contain PII)."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.types import PipelineResult


def _safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_safe(v) for v in value]
    return str(value)


class AuditLogger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def log(self, result: PipelineResult) -> dict:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "request_id": result.request_id,
            "role": result.role,
            "region": result.region,
            "question": result.question,
            "sql_generated": result.sql_generated,
            "sql_executed": result.sql_executed,
            "decision": result.decision,
            "blocked_by": result.blocked_by,
            "rule_id": result.rule_id,
            "reason": result.reason,
            "guard_results": [_safe(g.to_dict()) for g in result.guard_results],
            "retries": result.retries,
            "correction_log": _safe(result.correction_log),
            "row_count": len(result.rows),
            "masked_columns": list(result.masked_columns),
            "redactions": result.redactions,
            "latency_ms": round(result.latency_ms, 3),
            "layer_latency_ms": {k: round(v, 3) for k, v in result.layer_latency_ms.items()},
            "config": result.config,
        }
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return record

    def tail(self, n: int = 50) -> list[dict]:
        if not self.path.exists():
            return []
        with self._lock:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        out: list[dict] = []
        for line in lines[-n:]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
