"""Any OpenAI-compatible chat-completions endpoint: OpenAI, Meta Llama (via Llama API / Together / Groq / Fireworks / Ollama /
vLLM), Google Gemini, Mistral, DeepSeek, OpenRouter, Azure OpenAI... Only `httpx` is needed (already a dependency).

The orchestrator speaks one canonical, Anthropic-shaped message format (text / tool_use / tool_result blocks); this client
translates it to and from the chat-completions wire format, so switching provider never touches orchestration code."""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from .base import LLMError, LLMResponse, ToolCall

log = logging.getLogger(__name__)
_STOP = {"stop": "end_turn", "tool_calls": "tool_use", "function_call": "tool_use", "length": "max_tokens", "content_filter": "refusal"}


def _get(b: Any, key: str, default: Any = None) -> Any:
    return b.get(key, default) if isinstance(b, dict) else getattr(b, key, default)


def to_openai_messages(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages:
        c = m["content"]
        if isinstance(c, str):
            out.append({"role": m["role"], "content": c})
            continue
        if m["role"] == "assistant":
            text = "".join(_get(b, "text", "") for b in c if _get(b, "type") == "text")
            calls = [
                {"id": _get(b, "id"), "type": "function", "function": {"name": _get(b, "name"), "arguments": json.dumps(_get(b, "input", {}))}}
                for b in c if _get(b, "type") == "tool_use"
            ]
            msg: dict[str, Any] = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        else:  # user turn made of tool_result / text blocks
            for b in c:
                if _get(b, "type") == "tool_result":
                    body = _get(b, "content", "")
                    body = body if isinstance(body, str) else json.dumps(body, default=str)
                    out.append({"role": "tool", "tool_call_id": _get(b, "tool_use_id"), "content": ("ERROR: " + body) if _get(b, "is_error") else body})
                elif _get(b, "type") == "text":
                    out.append({"role": "user", "content": _get(b, "text", "")})
    return out


def to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""), "parameters": t["input_schema"]}} for t in tools]


class OpenAICompatLLM:
    def __init__(self, model: str, max_tokens: int, base_url: str, api_key: str = "", timeout: float = 60.0,
                 client: httpx.AsyncClient | None = None, extra_headers: dict[str, str] | None = None) -> None:
        self.model, self.max_tokens = model, max_tokens
        headers = {"Content-Type": "application/json", **(extra_headers or {})}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = client or httpx.AsyncClient(base_url=base_url.rstrip("/") + "/", headers=headers, timeout=timeout)

    async def complete(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], static_system_len: int = 0
    ) -> LLMResponse:
        # static_system_len is ignored: OpenAI-style providers cache a shared prompt prefix automatically.
        body: dict[str, Any] = {"model": self.model, "messages": to_openai_messages(system, messages), "max_tokens": self.max_tokens}
        if tools:
            body["tools"] = to_openai_tools(tools)
        try:
            r = await self._client.post("chat/completions", json=body)
        except httpx.TimeoutException as e:
            raise LLMError("model API timeout", "timeout") from e
        except httpx.HTTPError as e:
            raise LLMError("cannot reach model API", "error") from e
        if r.status_code >= 400:
            log.error("model API error %s: %s", r.status_code, r.text[:300])
            raise LLMError(f"model API error {r.status_code}", "overload" if r.status_code in (429, 503, 529) else "error")
        try:
            data = r.json()
            choice = data["choices"][0]
            msg = choice["message"]
            calls = [ToolCall(c["id"], c["function"]["name"], json.loads(c["function"].get("arguments") or "{}")) for c in msg.get("tool_calls") or []]
        except (KeyError, IndexError, ValueError, TypeError) as e:
            raise LLMError("malformed model API response", "error") from e
        text = (msg.get("content") or "").strip()
        content: list[Any] = ([{"type": "text", "text": text}] if text else []) + [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in calls]
        u = data.get("usage") or {}
        return LLMResponse(
            text=text, tool_calls=calls, content=content, stop_reason="tool_use" if calls else _STOP.get(choice.get("finish_reason"), "end_turn"),
            input_tokens=u.get("prompt_tokens", 0) or 0, output_tokens=u.get("completion_tokens", 0) or 0,
            cache_read_tokens=(u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0,
        )
