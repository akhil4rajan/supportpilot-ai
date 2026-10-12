"""AnthropicLLM against a stubbed SDK client (no network): block parsing and request shape."""
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from app.llm.anthropic_client import AnthropicLLM
from app.llm.base import LLMError


class FakeMessages:
    def __init__(self, resp=None, exc=None):
        self.resp, self.exc, self.kwargs = resp, exc, None

    async def create(self, **kw):
        self.kwargs = kw
        if self.exc:
            raise self.exc
        return self.resp


def llm(resp=None, exc=None):
    m = FakeMessages(resp, exc)
    return AnthropicLLM("claude-opus-5-5", 1024, "medium", client=SimpleNamespace(messages=m)), m


async def test_parses_text_and_tool_use_and_keeps_raw_content():
    blocks = [
        SimpleNamespace(type="thinking", thinking="", signature="sig"),
        SimpleNamespace(type="text", text="Let me check."),
        SimpleNamespace(type="tool_use", id="toolu_1", name="get_order_status", input={"order_id": "ACM-1001"}),
    ]
    client, m = llm(SimpleNamespace(content=blocks, stop_reason="tool_use"))
    r = await client.complete(system="sys", messages=[{"role": "user", "content": "hi"}], tools=[{"name": "x"}])
    assert r.text == "Let me check." and r.stop_reason == "tool_use"
    assert r.tool_calls[0].name == "get_order_status" and r.tool_calls[0].input == {"order_id": "ACM-1001"}
    assert r.content == blocks  # thinking block preserved for the next loop step
    assert m.kwargs["model"] == "claude-opus-5-5" and m.kwargs["output_config"] == {"effort": "medium"}
    assert "tool_choice" not in m.kwargs and "temperature" not in m.kwargs  # removed/forbidden on current models


async def test_api_errors_become_llm_error():
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    err = anthropic.APIConnectionError(request=req)
    client, _ = llm(exc=err)
    with pytest.raises(LLMError):
        await client.complete(system="s", messages=[], tools=[])
