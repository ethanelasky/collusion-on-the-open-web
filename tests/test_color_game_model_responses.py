"""Native Responses tools preserve reasoning and game action validation."""
import copy
import json
import time
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from ai_collusion.client import ModelConfig
from experiments.color_game.model import ModelAgent


def model(**overrides):
    fields = dict(name="luna-high", transport="responses", model="gpt-5.6-luna",
                  base_url="https://api.openai.com/v1", effort="high",
                  reasoning_summary="auto", max_tokens=32768, retries=0)
    fields.update(overrides)
    return ModelConfig(**fields)


def request(**overrides):
    fields = dict(system="Choose one color.", messages=[{"role": "user", "content": "Choose red."}],
                  available_actions=["choose"])
    fields.update(overrides)
    return fields


def envelope(output=None, **overrides):
    action = {"color": "red"}
    if output is None:
        output = [{"type": "function_call", "name": "color_choose", "arguments": json.dumps(action),
                   "call_id": "call_test"}]
    result = {"text": "provider prose", "provider_text": "provider prose", "reasoning": "saved summary",
              "raw": {"id": "resp_test", "output": output}, "usage": {"total_tokens": 123},
              "finish_reason": "completed", "retry_events": []}
    result.update(overrides)
    return result


def test_responses_native_action_preserves_raw_reasoning_usage(monkeypatch):
    original = envelope()
    monkeypatch.setattr("experiments.color_game.model.generate", lambda *a, **k: original)
    response = ModelAgent(model())(request())
    assert json.loads(response["text"]) == {"action": "choose", "color": "red"}
    assert response["response_tool_error"] is None
    assert response["tool_mode"] == "native"
    assert response["text_source"] == "canonical_action_from_tool"
    for key in ("raw", "reasoning", "usage", "finish_reason", "provider_text", "retry_events"):
        assert response[key] == original[key]
    assert original["text"] == "provider prose"


@pytest.mark.parametrize("output", [[], [
    {"type": "function_call", "name": "wrong_tool", "arguments": "{}"}], [
    {"type": "function_call", "name": "color_choose", "arguments": {}}], [
    {"type": "function_call", "name": "color_choose", "arguments": "{}"},
    {"type": "function_call", "name": "color_choose", "arguments": "{}"}], [
    {"type": "message", "content": [{"type": "output_text", "text": '{"action":"choose","color":"red"}'}]}],
])
def test_responses_requires_exactly_one_named_tool_without_text_fallback(monkeypatch, output):
    monkeypatch.setattr("experiments.color_game.model.generate", lambda *a, **k: envelope(output))
    response = ModelAgent(model())(request())
    assert response["response_tool_error"]
    assert response["text"] == ""
    assert response["raw"]["output"] == output


@pytest.mark.parametrize("raw", [None, [], "invalid", {}, {"output": None}, {"output": 3}, {"output": {}}])
def test_malformed_response_output_is_retained_and_rejected(monkeypatch, raw):
    original = envelope(raw=raw)
    monkeypatch.setattr("experiments.color_game.model.generate", lambda *a, **k: original)
    response = ModelAgent(model())(request())
    assert response["response_tool_error"]
    assert response["text"] == ""
    assert response["raw"] == raw
    assert response["usage"] == original["usage"]
    assert response["reasoning"] == original["reasoning"]


def test_direct_chat_luna_reasoning_routes_to_responses_without_mutating_original():
    cfg = model(transport="openai", effort=None, extra_body={"reasoning_effort": "high"})
    original = copy.deepcopy(asdict(cfg))
    adapter = ModelAgent(cfg)
    effective = adapter._effective_model(["get", "wait", "choose"])
    assert effective.transport == "responses"
    assert effective.effort == "high"
    assert effective.reasoning_summary == "auto"
    assert effective.extra_body["reasoning"] == {"effort": "high", "summary": "auto"}
    assert "reasoning_effort" not in effective.extra_body
    assert effective.max_tokens == 32768
    assert [tool["name"] for tool in effective.extra_body["tools"]] == ["color_get", "color_wait", "color_choose"]
    assert adapter.metadata()["native_api"] == {"requested_transport": "openai", "effective_transport": "responses"}
    assert adapter.metadata()["adapter"] == "color-game-v4"
    assert asdict(cfg) == original


@pytest.mark.parametrize("base_url,model_name,effort", [
    ("https://openrouter.ai/api/v1", "gpt-5.6-luna", "high"),
    ("https://api.openai.com.evil.invalid/v1", "gpt-5.6-luna", "high"),
    ("https://api.openai.com/v1", "offline-test", "high"),
    ("https://api.openai.com/v1", "gpt-5.6-luna", "none"),
])
def test_other_chat_routes_stay_unchanged(base_url, model_name, effort):
    cfg = model(transport="openai", base_url=base_url, model=model_name, effort=None,
                extra_body={"reasoning_effort": effort})
    effective = ModelAgent(cfg)._effective_model(["choose"])
    assert effective.transport == "openai"
    assert effective.extra_body["tools"][0]["function"]["name"] == "color_choose"
    assert effective.extra_body["reasoning_effort"] == effort


def test_responses_shared_client_sends_high_reasoning_and_flat_tools(monkeypatch):
    from ai_collusion import client

    calls = []
    raw = envelope()["raw"]
    output = [SimpleNamespace(**raw["output"][0]),
              SimpleNamespace(type="reasoning", summary=[SimpleNamespace(text="saved summary")])]
    response = SimpleNamespace(output=output, status="completed", usage=SimpleNamespace(
        input_tokens=20, output_tokens=30, total_tokens=50,
        input_tokens_details=SimpleNamespace(cached_tokens=0),
        output_tokens_details=SimpleNamespace(reasoning_tokens=12)), model_dump=lambda: raw)

    class FakeOpenAI:
        def __init__(self, **kwargs):
            calls.append({"client": kwargs})
            self.responses = SimpleNamespace(create=self.create)

        def create(self, **kwargs):
            calls.append({"request": kwargs})
            return response

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    monkeypatch.setattr("experiments.color_game.model.generate", client.generate)
    cfg = model(api_key_env="COLOR_GAME_UNUSED_TEST_KEY", timeout_s=600)
    result = ModelAgent(cfg)(request(phase="play", request_timeout_s=10,
                                      request_deadline_monotonic=time.monotonic() + 10))
    sent = calls[1]["request"]
    assert sent["reasoning"] == {"effort": "high", "summary": "auto"}
    assert sent["max_output_tokens"] == 32768
    assert sent["store"] is False
    assert sent["instructions"] == "Choose one color."
    assert "reasoning_effort" not in sent.get("extra_body", {})
    body = sent["extra_body"]
    assert body["tool_choice"] == {"type": "function", "name": "color_choose"}
    assert body["parallel_tool_calls"] is False
    tool = body["tools"][0]
    assert "function" not in tool
    assert tool["name"] == "color_choose"
    assert tool["strict"] is True
    schema = tool["parameters"]
    assert schema["properties"] == {"color": {"type": "string"}}
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["additionalProperties"] is False
    assert calls[0]["client"]["timeout"] <= 10
    assert calls[0]["client"]["max_retries"] == 0
    assert result["response_tool_error"] is None
    assert result["reasoning"] == "saved summary"
    assert result["usage"]["completion_tokens_details"]["reasoning_tokens"] == 12
