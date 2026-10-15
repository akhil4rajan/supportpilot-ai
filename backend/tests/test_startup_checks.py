"""Fail-fast startup behaviour: missing model API key, and demo credentials in a production deployment."""
import shutil
from pathlib import Path

import pytest

from app.config import Settings
from app.llm.providers import build_client
from app.startup_checks import assert_safe_for_prod, demo_credentials_in_use
from app.tenancy.registry import TenantRegistry

ROOT = Path(__file__).resolve().parent.parent


def _settings(tmp_path, **kw) -> Settings:
    return Settings(database_url="sqlite+aiosqlite:///x.db", server_secret="s" * 40, tenants_dir=tmp_path, **kw)


def test_anthropic_without_api_key_fails_fast_with_actionable_message(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY.*LLM_PROVIDER=demo"):
        build_client("anthropic", "", _settings(tmp_path))


@pytest.fixture
def acme_registry(tmp_path):
    shutil.copytree(ROOT / "tenants" / "acme", tmp_path / "acme")
    return TenantRegistry(tmp_path)


def test_prod_refuses_published_demo_credentials(tmp_path, acme_registry, monkeypatch):
    monkeypatch.setenv("ACME_USER_TOKEN_SECRET", "dev-only-shared-secret-change-me-32chars")
    monkeypatch.setenv("ACME_AGENT_KEY_SAM", "sam-demo-key")
    monkeypatch.setenv("ACME_AGENT_KEY_JO", "real-" + "x" * 30)
    s = _settings(tmp_path, env="prod", admin_api_key="change-me-admin")
    assert set(demo_credentials_in_use(s, acme_registry)) == {
        "ADMIN_API_KEY", "ACME_USER_TOKEN_SECRET (tenant acme)", "ACME_AGENT_KEY_SAM (tenant acme)"}
    with pytest.raises(RuntimeError, match="public demo values"):
        assert_safe_for_prod(s, acme_registry)


def test_prod_accepts_real_credentials_and_dev_accepts_demo(tmp_path, acme_registry, monkeypatch):
    for name in ("ACME_USER_TOKEN_SECRET", "ACME_AGENT_KEY_SAM", "ACME_AGENT_KEY_JO"):
        monkeypatch.setenv(name, "real-" + name + "-" + "x" * 30)
    assert_safe_for_prod(_settings(tmp_path, env="prod", admin_api_key="a" * 32), acme_registry)
    monkeypatch.setenv("ACME_AGENT_KEY_SAM", "sam-demo-key")
    assert_safe_for_prod(_settings(tmp_path, env="dev", admin_api_key="change-me-admin"), acme_registry)
