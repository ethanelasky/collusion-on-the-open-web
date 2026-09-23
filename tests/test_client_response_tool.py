"""One explicitly selected native function call becomes one action string."""
import copy
import json

import httpx
import openai
import pytest

from ai_collusion import client


def tool_call(name="counter_action", arguments='{"action":"done"}', call_id="call-local"):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


@pytest.fixture
def api(monkeypatch):
    payload = {
        "id": "local-response", "object": "chat.completion", "created": 0, "model": "local-model",
        "provider": "OpenAI", "trace_tag": "preserve-provider-metadata",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": None,
                                                "reasoning": "Private provider reasoning",
                                                "tool_calls": [tool_call()]},
                     "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30,
                  "completion_tokens_details": {"reasoning_tokens": 12}},
    }
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=payload)

    real_client = openai.OpenAI
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: real_client(
        **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler))))
    monkeypatch.setattr(client.time, "sleep", lambda seconds: pytest.fail("Format errors must not retry"))
    return payload, requests


def config(**overrides):
    return client.ModelConfig.from_dict({
        "name": "local", "transport": "openai", "model": "local-model",
        "base_url": "https://example.test/v1", "response_tool_name": "counter_action", "retries": 2,
        "extra_body": {"tools": [{"type": "function", "function": {
            "name": "counter_action", "parameters": {"type": "object"}}}],
            "tool_choice": {"type": "function", "function": {"name": "counter_action"}},
            "parallel_tool_calls": False},
        **overrides,
    })


def generate(cfg):
    return client.generate(cfg, "Return one action", [{"role": "user", "content": "Current question"}],
                           temperature=None, seed=None)


def test_selected_function_arguments_and_raw_response_are_retained(api):
    payload, requests = api
    message = payload["choices"][0]["message"]
    message["content"] = "Provider commentary remains in the raw transcript."
    arguments = '{"action":"get","url":"https://api.counterapi.dev/v1/round/CA5/up"}'
    message["tool_calls"] = [tool_call(arguments=arguments)]
    original = copy.deepcopy(payload)

    result = generate(config())

    assert result["text"] == arguments
    assert result["text_source"] == "tool_call.arguments" and result["tool_call_id"] == "call-local"
    assert result["reasoning"] == "Private provider reasoning"
    assert result["finish_reason"] == "tool_calls"
    assert result["usage"]["completion_tokens_details"]["reasoning_tokens"] == 12
    raw_message = result["raw"]["choices"][0]["message"]
    assert raw_message["tool_calls"] == original["choices"][0]["message"]["tool_calls"]
    assert raw_message["content"] == original["choices"][0]["message"]["content"]
    assert result["raw"]["trace_tag"] == "preserve-provider-metadata"
    assert result["raw"]["provider"] == "OpenAI" and payload == original
    assert len(requests) == 1 and requests[0]["parallel_tool_calls"] is False
    assert requests[0]["tool_choice"]["function"]["name"] == "counter_action"
    assert "response_tool_name" not in requests[0]
    assert result["tool_call"] is None and result["tool_error"] is None
    assert result["retry_events"] == []
    json.dumps(result)  # Counter responses must not leak SDK objects into saved records.


@pytest.mark.parametrize("calls,expected", [
    (None, "received 0"),
    ([], "received 0"),
    ([tool_call(), tool_call(call_id="second")], "received 2"),
    ([tool_call(name="other_action")], "received 'other_action'"),
    ([tool_call(arguments=None)], "arguments must be a string"),
])
def test_missing_multiple_wrong_or_invalid_function_calls_are_rejected_without_retry(api, calls, expected):
    payload, requests = api
    payload["choices"][0]["message"].update(content='{"action":"done"}', tool_calls=calls)
    result = generate(config())
    assert result["text"] == "" and result["text_source"] == "rejected_tool_call"
    assert expected in result["response_tool_error"]
    assert result["raw"]["choices"][0]["message"]["tool_calls"] == calls
    assert result["raw"]["choices"][0]["message"]["content"] == '{"action":"done"}'
    assert result["reasoning"] == "Private provider reasoning"
    assert len(requests) == 1


def test_rejected_multiple_calls_are_journaled_without_executing_any_action(api):
    from experiments.covert_channel.channel import Channel
    from experiments.covert_channel.participants import Agent

    payload, requests = api
    channel = Channel("sender_to_receiver", "test", counter_mode="wiki")
    calls = [tool_call(arguments=json.dumps({"action": "get", "url": channel.url + "/up"})),
             tool_call(arguments='{"action":"done"}', call_id="second")]
    payload["choices"][0]["message"].update(content='{"action":"done"}', tool_calls=calls)
    events = []
    agent = Agent("sender", config(), "test", ["red", "blue"], secret="red", max_turns=2,
                  counter_url=channel.url, counter_mode="wiki", emit=events.append)

    agent.take_turn([channel], 0)

    response_event = next(e for e in events if e["kind"] == "response")
    assert response_event["response"]["raw"]["choices"][0]["message"]["tool_calls"] == calls
    assert response_event["response"]["text_source"] == "rejected_tool_call"
    turn = agent.turns[-1]
    assert turn["source"] == "action-error" and turn["action"] is None
    assert "received 2" in turn["result"]
    assert agent.error is None and agent.turn == 1 and not agent.finished
    assert channel.media["sender"].counts == {} and channel.events == []
    assert len(requests) == 1
    assert [e["kind"] for e in events] == ["request", "response", "turn"]


def test_json_action_validation_is_left_to_the_caller(api):
    payload, requests = api
    payload["choices"][0]["message"]["tool_calls"] = [tool_call(arguments="not a JSON action")]
    result = generate(config())
    assert result["text"] == "not a JSON action" and len(requests) == 1


def test_unconfigured_client_keeps_existing_content_behavior(api):
    payload, requests = api
    payload["choices"][0]["message"]["content"] = "Original text response"
    result = generate(config(response_tool_name=None))
    assert result["text"] == "Original text response"
    assert "text_source" not in result and "tool_call_id" not in result
    assert result["raw"]["choices"][0]["message"]["tool_calls"] == [tool_call()]
    assert len(requests) == 1


def test_invalid_response_tool_name_fails_before_any_request(api):
    _, requests = api
    with pytest.raises(ValueError, match="response_tool_name must be a nonempty string"):
        generate(config(response_tool_name=""))
    assert requests == []
