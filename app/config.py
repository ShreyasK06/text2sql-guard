"""Runtime settings, all from environment variables (see .env.example). No secrets in code."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POLICY_PATH = REPO_ROOT / "policies" / "policy.yaml"

# Local development default for the throwaway docker-compose database (synthetic data only).
_DEV_PASSWORD = "t2s_local_dev_only"


@dataclass(frozen=True)
class Settings:
    db_host: str
    db_port: int
    db_name: str
    owner_user: str
    owner_password: str
    role_password: str          # shared by t2s_analyst / t2s_manager / t2s_admin in the dev database
    watchdog_ms: int            # hard cap the harness puts on UNSANDBOXED execution
    unsandboxed_row_cap: int    # memory safety cap when the sandbox layer is off
    llm_provider: str           # mock | anthropic | gemini
    llm_model: str | None
    max_retries: int
    audit_path: Path
    policy_path: Path

    def dsn(self, user: str, password: str) -> str:
        return (
            f"host={self.db_host} port={self.db_port} dbname={self.db_name} "
            f"user={user} password={password} connect_timeout=5"
        )

    def owner_dsn(self) -> str:
        return self.dsn(self.owner_user, self.owner_password)

    def role_dsn(self, db_role: str) -> str:
        return self.dsn(db_role, self.role_password)


def get_settings() -> Settings:
    env = os.environ.get
    return Settings(
        db_host=env("T2S_DB_HOST", "127.0.0.1"),
        db_port=int(env("T2S_DB_PORT", "54329")),
        db_name=env("T2S_DB_NAME", "t2s"),
        owner_user=env("T2S_OWNER_USER", "t2s_owner"),
        owner_password=env("T2S_OWNER_PASSWORD", _DEV_PASSWORD),
        role_password=env("T2S_ROLE_PASSWORD", _DEV_PASSWORD),
        watchdog_ms=int(env("T2S_WATCHDOG_MS", "3000")),
        unsandboxed_row_cap=int(env("T2S_UNSANDBOXED_ROW_CAP", "20000")),
        llm_provider=env("T2S_LLM_PROVIDER", "mock"),
        llm_model=env("T2S_LLM_MODEL") or None,
        max_retries=int(env("T2S_MAX_RETRIES", "2")),
        audit_path=Path(env("T2S_AUDIT_PATH", str(REPO_ROOT / "audit_logs" / "audit.jsonl"))),
        policy_path=Path(env("T2S_POLICY_PATH", str(DEFAULT_POLICY_PATH))),
    )
