"""Service settings, read once from environment variables (set by the Helm ConfigMap and Secret)."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    llm_provider: str
    policy_version: str
    session_ttl_seconds: int
    max_sessions: int
    app_version: str
    log_level: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            llm_provider=os.getenv("LLM_PROVIDER", "gemini").strip().lower(),
            policy_version=os.getenv("AGENT_POLICY_VERSION", "latest").strip(),
            session_ttl_seconds=int(os.getenv("SESSION_TTL_SECONDS", "1800")),
            max_sessions=int(os.getenv("MAX_SESSIONS", "500")),
            app_version=os.getenv("APP_VERSION", "dev"),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )
