"""Refuse to start in production with the well-known demo credentials that ship in .env.example and tenants/acme."""
from __future__ import annotations

from app.config import Settings
from app.tenancy.models import env
from app.tenancy.registry import TenantRegistry

DEMO_SECRETS = frozenset({
    "change-me-admin",
    "dev-only-shared-secret-change-me-32chars",
    "sam-demo-key",
    "jo-demo-key",
    "dev-only-wattlebay-secret-change-me-32chars",
    "wb-sam-demo-key",
    "wb-jo-demo-key",
})


def demo_credentials_in_use(settings: Settings, registry: TenantRegistry) -> list[str]:
    """Names of the settings/env vars that still hold a published demo value."""
    found: list[str] = []
    if settings.admin_api_key in DEMO_SECRETS:
        found.append("ADMIN_API_KEY")
    for rt in registry.all():
        t = rt.tenant
        names = [t.user_token_secret_env, *(a.api_key_env for a in t.agents)]
        found += [f"{n} (tenant {t.id})" for n in names if n and env(n) in DEMO_SECRETS]
    return found


def assert_safe_for_prod(settings: Settings, registry: TenantRegistry) -> None:
    if settings.is_dev:
        return
    bad = demo_credentials_in_use(settings, registry)
    if bad:
        raise RuntimeError(
            "ENV=prod but these still use the public demo values from the repo: " + ", ".join(bad)
            + ". Generate real secrets (e.g. `openssl rand -base64 48`) or remove the demo tenant."
        )
