from copy import deepcopy
from types import SimpleNamespace

import pytest

from ai_collusion.env import parse_call
from ai_collusion.native_tools import TOOL_DEFINITIONS, enabled, provider_messages, response_text


def test_paired_calls_become_native_history_without_changing_source():
    messages = [
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": 'fetching\nweb_fetch("https://example.org")'},
        {"role": "user", "content": "RESULT fetched page"},
        {"role": "assistant", "content": "Waiting for more information."},
        {"role": "user", "content": "RESULT no call"},
    ]
    before = deepcopy(messages)
    converted = provider_messages(messages)
    assert messages == before
    assert converted[1]["tool_calls"][0]["function"]["name"] == "web_fetch"
    assert converted[2]["role"] == "tool"
    assert converted[2]["tool_call_id"] == converted[1]["tool_calls"][0]["id"]
    assert converted[2]["content"] == messages[2]["content"]
    assert converted[3:] == messages[3:]


def test_native_call_round_trips_multiline_and_quoted_argument():
    import json
    arg = 'python - <<\'PY\'\nprint("hello")\nPY'
    call = SimpleNamespace(function=SimpleNamespace(name="shell", arguments=json.dumps({"arg": arg})))
    converted = response_text("Run Python.", [call])
    parsed = parse_call(converted)
    assert parsed.tool == "shell" and parsed.arg == arg
    assert converted.startswith("Run Python.\n")


def test_unavailable_or_multiple_calls_are_not_silently_executed():
    call = SimpleNamespace(function=SimpleNamespace(name="delete_everything", arguments='{"arg":"x"}'))
    with pytest.raises(ValueError, match="Native tool requires"):
        response_text("", [call])
    with pytest.raises(ValueError, match="exactly one"):
        response_text("", [call, call])
    assert response_text("plain reply", []) == "plain reply"
    assert not enabled({})
    assert enabled({"tools": TOOL_DEFINITIONS})


def test_client_sends_native_schema_and_extracts_call(monkeypatch):
    import json
    import openai
    from ai_collusion.client import ModelConfig, generate

    captured = {}
    message = SimpleNamespace(content=None, model_extra={}, tool_calls=[SimpleNamespace(
        function=SimpleNamespace(name="wait", arguments=json.dumps({"arg": "10"})))])
    response = SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="tool_calls")],
                               usage=None, model_dump=lambda: {})
    def create(**kwargs):
        captured.update(kwargs)
        return response
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    config = ModelConfig(name="test", transport="openai", model="test",
                         extra_body={"tools": TOOL_DEFINITIONS, "tool_choice": "auto", "parallel_tool_calls": False})
    result = generate(config, "unchanged system", [{"role": "user", "content": "task"}],
                      temperature=None, seed=5)
    assert captured["messages"][0]["content"] == "unchanged system"
    assert captured["extra_body"]["tool_choice"] == "auto"
    assert result["text"] == 'wait("10")'
