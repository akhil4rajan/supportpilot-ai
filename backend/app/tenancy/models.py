"""Declarative per-customer (tenant) configuration. One YAML file per customer."""
from __future__ import annotations

import os
from typing import Any, Literal

from pydantic import BaseModel, Field


def env(name: str | None) -> str | None:
    """Secrets are never stored in tenant YAML; YAML names the env var that holds them."""
    return os.environ.get(name) if name else None


class Branding(BaseModel):
    title: str = "Support"
    greeting: str = "Hi! How can I help you today?"
    primary_color: str = "#4f46e5"
    launcher_label: str = "Help"


class Persona(BaseModel):
    assistant_name: str = "Assistant"
    tone: str = "friendly, concise and professional"
    extra_instructions: str = ""  # appended to the system prompt (company-specific policy etc.)


class KnowledgeConfig(BaseModel):
    path: str = "knowledge"  # relative to the tenant directory
    min_coverage: float = Field(0.35, ge=0, le=1)  # >= this: confident match
    weak_coverage: float = Field(0.10, ge=0, le=1)  # [weak, min): "possible" match, flagged low-confidence; < weak: nothing
    top_k: int = 4
    # Customer phrasing -> canonical term in the docs. {"refund": ["money back", "reimburse"]}
    synonyms: dict[str, list[str]] = Field(default_factory=dict)


class HttpOrdersConfig(BaseModel):
    base_url: str
    api_key_env: str | None = None
    # Both URLs receive the *verified* customer id; the tenant API must enforce ownership too.
    get_path: str = "/customers/{customer_id}/orders/{order_id}"
    list_path: str = "/customers/{customer_id}/orders"
    field_map: dict[str, str] = Field(default_factory=dict)  # canonical_field -> your_field
    timeout_seconds: float = 8.0


class OrdersConfig(BaseModel):
    provider: Literal["none", "demo", "http"] = "none"
    demo_file: str = "orders.json"
    http: HttpOrdersConfig | None = None


class JiraConfig(BaseModel):
    server: str
    email_env: str = "JIRA_EMAIL"
    api_token_env: str = "JIRA_API_TOKEN"
    project_key: str
    issue_type: str = "Task"
    labels: list[str] = Field(default_factory=lambda: ["supportpilot"])
    priority_map: dict[str, str] = Field(
        default_factory=lambda: {"low": "Low", "normal": "Medium", "high": "High", "urgent": "Highest"}
    )
    set_priority: bool = True


class TicketsConfig(BaseModel):
    provider: Literal["none", "memory", "jira"] = "none"
    jira: JiraConfig | None = None


class HandoffConfig(BaseModel):
    webhook_url_env: str | None = None  # Slack-compatible incoming webhook, optional
    max_unresolved_turns: int = Field(2, ge=1)  # auto-escalate after N turns the KB couldn't answer
    waiting_message: str = (
        "I've passed your conversation to our support team. A human agent will join this chat shortly."
    )


class AgentAccount(BaseModel):
    id: str
    name: str
    api_key_env: str  # env var holding this human agent's console key


class Tenant(BaseModel):
    id: str
    name: str
    public_key: str  # identifies the tenant from the browser; not a secret
    allowed_origins: list[str] = Field(default_factory=list)
    user_token_secret_env: str | None = None  # shared HS256 secret with the customer's backend
    branding: Branding = Branding()
    persona: Persona = Persona()
    knowledge: KnowledgeConfig = KnowledgeConfig()
    orders: OrdersConfig = OrdersConfig()
    tickets: TicketsConfig = TicketsConfig()
    handoff: HandoffConfig = HandoffConfig()
    agents: list[AgentAccount] = Field(default_factory=list)

    # filled by the loader
    root: Any = Field(default=None, exclude=True)
