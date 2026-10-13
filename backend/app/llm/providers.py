"""Provider registry: LLM_PROVIDER=<name> picks a preset (base URL + API-key env var). Everything is overridable with
LLM_BASE_URL / LLM_API_KEY, and `openai_compatible` points at any other endpoint."""
from __future__ import annotations

import os
from dataclasses import dataclass

from app.config import Settings

from .base import LLMClient
from .openai_compat import OpenAICompatLLM


@dataclass(frozen=True)
class Preset:
    base_url: str
    key_env: str


PRESETS: dict[str, Preset] = {
    "openai": Preset("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "gemini": Preset("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),  # Google
    "meta": Preset("https://api.llama.com/compat/v1", "LLAMA_API_KEY"),  # Meta's Llama API
    "groq": Preset("https://api.groq.com/openai/v1", "GROQ_API_KEY"),  # hosted Llama and others
    "together": Preset("https://api.together.xyz/v1", "TOGETHER_API_KEY"),  # hosted Llama and others
    "fireworks": Preset("https://api.fireworks.ai/inference/v1", "FIREWORKS_API_KEY"),
    "mistral": Preset("https://api.mistral.ai/v1", "MISTRAL_API_KEY"),
    "deepseek": Preset("https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
    "openrouter": Preset("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "ollama": Preset("http://localhost:11434/v1", ""),  # self-hosted Llama etc., no key
    "vllm": Preset("http://localhost:8000/v1", ""),  # self-hosted
    "openai_compatible": Preset("", "LLM_API_KEY"),  # anything else: set LLM_BASE_URL
}
NATIVE = {"anthropic"}
SUPPORTED = sorted(NATIVE | PRESETS.keys())


def build_client(provider: str, model: str, s: Settings) -> LLMClient:
    """`model` is required for every provider except anthropic (which has a default)."""
    if provider == "anthropic":
        from .anthropic_client import AnthropicLLM
        return AnthropicLLM(model or s.anthropic_model, s.llm_max_tokens, s.anthropic_effort)
    preset = PRESETS.get(provider)
    if preset is None:
        raise ValueError(f"unknown LLM_PROVIDER {provider!r}; choose one of: demo, simulated, {', '.join(SUPPORTED)}")
    if not model:
        raise ValueError(f"LLM_PROVIDER={provider} needs LLM_MODEL (the provider's model id)")
    base_url = s.llm_base_url or preset.base_url
    if not base_url:
        raise ValueError(f"LLM_PROVIDER={provider} needs LLM_BASE_URL")
    api_key = s.llm_api_key or (os.environ.get(preset.key_env, "") if preset.key_env else "")
    if not api_key and preset.key_env and not provider.startswith("openai_c"):
        raise ValueError(f"LLM_PROVIDER={provider} needs {preset.key_env} (or LLM_API_KEY)")
    return OpenAICompatLLM(model, s.llm_max_tokens, base_url, api_key, timeout=s.llm_call_timeout_seconds)
