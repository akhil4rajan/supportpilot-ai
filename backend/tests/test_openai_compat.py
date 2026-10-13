"""OpenAI-compatible client + provider registry against a mocked HTTP transport (no network)."""
import json

import httpx
import pytest

from app.config import Settings
from app.llm.base import LLMError
from app.llm.fallback import FallbackLLM
from app.llm.guard import GuardedLLM
from app.llm.openai_compat import OpenAICompatLLM, to_openai_messages
from app.main import build_llm

TOOLS = [{"name": "get_order_status", "description": "d", "input_schema": {"type": "object", "properties": {"order_id": {"type": "string"}}}}]


def client(handler):
    http = httpx.AsyncClient(base_url="http://llm.test/v1/", transport=httpx.MockTransport(handler))
    return OpenAICompatLLM("m1", 256, "http://llm.test/v1", client=http)


async def test_text_reply_and_request_shape():
    seen = {}

    def h(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": " Hello "}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 5, "completion_tokens": 2}})

    r = await client(h).complete(system="sys", messages=[{"role": "user", "content": "hi"}], tools=TOOLS)
    assert r.text == "Hello" and r.stop_reason == "end_turn" and (r.input_tokens, r.output_tokens) == (5, 2)
    assert seen["body"]["messages"][0] == {"role": "system", "content": "sys"}
    assert seen["body"]["tools"][0]["function"]["parameters"] == TOOLS[0]["input_schema"]


async def test_tool_call_roundtrip_uses_canonical_blocks():
    def h(req):
        return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "get_order_status", "arguments": '{"order_id": "ACM-1001"}'}}]}}]})

    r = await client(h).complete(system="s", messages=[], tools=TOOLS)
    assert r.stop_reason == "tool_use" and r.tool_calls[0].input == {"order_id": "ACM-1001"}
    msgs = to_openai_messages("s", [
        {"role": "user", "content": "where is it"},
        {"role": "assistant", "content": r.content},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "{}", "is_error": True}]},
    ])
    assert msgs[2]["tool_calls"][0]["function"]["arguments"] == '{"order_id": "ACM-1001"}'
    assert msgs[3] == {"role": "tool", "tool_call_id": "c1", "content": "ERROR: {}"}


@pytest.mark.parametrize("status,kind", [(429, "overload"), (503, "overload"), (400, "error")])
async def test_http_errors_map_to_llm_error(status, kind):
    with pytest.raises(LLMError) as e:
        await client(lambda req: httpx.Response(status, text="x")).complete(system="s", messages=[], tools=[])
    assert e.value.kind == kind


async def test_timeout_and_malformed():
    def boom(req):
        raise httpx.ReadTimeout("t")

    with pytest.raises(LLMError) as e:
        await client(boom).complete(system="s", messages=[], tools=[])
    assert e.value.kind == "timeout"
    with pytest.raises(LLMError):
        await client(lambda req: httpx.Response(200, json={})).complete(system="s", messages=[], tools=[])


def test_build_llm_providers(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    llm = build_llm(Settings(llm_provider="openai", llm_model="gpt-x", _env_file=None))
    assert isinstance(llm, GuardedLLM) and isinstance(llm.inner, OpenAICompatLLM) and llm.inner.model == "gpt-x"
    assert isinstance(build_llm(Settings(llm_provider="ollama", llm_model="llama3", _env_file=None)).inner, OpenAICompatLLM)  # no key needed


def test_cross_provider_fallback_and_config_errors(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("GROQ_API_KEY", "k")
    llm = build_llm(Settings(llm_provider="openai", llm_model="a", llm_fallback_provider="groq", llm_fallback_model="b", _env_file=None))
    assert isinstance(llm, FallbackLLM)
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        build_llm(Settings(llm_provider="openai", llm_model="a", _env_file=None))
    with pytest.raises(ValueError, match="LLM_MODEL"):
        build_llm(Settings(llm_provider="groq", _env_file=None))
    with pytest.raises(ValueError, match="unknown"):
        build_llm(Settings(llm_provider="nope", llm_model="a", _env_file=None))
