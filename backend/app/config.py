"""Process-wide settings (env vars / .env). Per-customer settings live in tenant YAML files."""
from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    env: str = "dev"  # dev | prod
    database_url: str = "sqlite+aiosqlite:///./supportpilot.db"
    tenants_dir: Path = BACKEND_DIR / "tenants"

    # Signs widget-session and agent-console tokens. MUST be set (and stable) in prod.
    server_secret: str = ""
    # Protects /v1/admin/* (tenant reload, knowledge upload). Empty = admin API disabled.
    admin_api_key: str = ""

    # anthropic | openai | gemini | meta | groq | together | fireworks | mistral | deepseek | openrouter | ollama | vllm |
    # openai_compatible | demo (keyword-based, no API key; for trying the UI). See app/llm/providers.py.
    llm_provider: str = "anthropic"
    llm_model: str = ""  # provider's model id; required except for anthropic (falls back to ANTHROPIC_MODEL)
    llm_base_url: str = ""  # override a preset's endpoint (Azure, a proxy, a self-hosted server...)
    llm_api_key: str = ""  # generic key; otherwise the provider's own env var (OPENAI_API_KEY, GEMINI_API_KEY, ...)
    # Optional secondary model used when the primary is overloaded/timing out. May be a DIFFERENT provider.
    llm_fallback_provider: str = ""  # defaults to llm_provider
    llm_fallback_model: str = ""
    anthropic_model: str = "claude-opus-5-5"  # default model when llm_provider=anthropic
    anthropic_effort: str = "medium"  # low | medium | high ...
    anthropic_fallback_model: str = ""  # legacy alias of llm_fallback_model for the anthropic provider
    llm_max_tokens: int = Field(default=2048, ge=256)
    llm_max_tool_steps: int = Field(default=6, ge=1)

    redis_url: str = ""  # set to run multiple replicas (shared WebSocket fan-out + rate limits)
    llm_max_concurrency: int = Field(default=64, ge=1)  # concurrent model calls per replica
    llm_queue_timeout_seconds: float = 20.0  # waiting for a model slot longer than this => degrade to human
    llm_call_timeout_seconds: float = 60.0
    db_pool_size: int = 10
    db_max_overflow: int = 20
    metrics_token: str = ""  # if set, /metrics requires "Authorization: Bearer <token>"
    log_format: str = "text"  # text | json
    outbox_poll_seconds: float = 5.0
    outbox_max_attempts: int = 8
    run_outbox_worker: bool = True  # set false on web replicas if you run a dedicated worker

    session_ttl_minutes: int = 4 * 60
    agent_token_ttl_minutes: int = 8 * 60
    messages_per_minute: int = 20
    max_message_chars: int = 4000

    @property
    def is_dev(self) -> bool:
        return self.env == "dev"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if not s.server_secret:
        if not s.is_dev:
            raise RuntimeError("SERVER_SECRET must be set when ENV != dev")
        # Dev convenience only: tokens stop working on restart.
        object.__setattr__(s, "server_secret", secrets.token_urlsafe(48))
    return s
