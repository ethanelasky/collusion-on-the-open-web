"""Provider schemas and parser agree; the two observed GET failures cannot recur."""
import copy
import json
from types import SimpleNamespace

import jsonschema
import pytest

from ai_collusion import client
from ai_collusion.client import ModelConfig
from experiments.color_game.game import _parse_action
from experiments.color_game.model import ModelAgent


def model(transport, **overrides):
    args = dict(name="offline", transport=transport, model="offline-test", retries=0,
                api_key_env="COLOR_GAME_OFFLINE_TEST_KEY")
    args.update(overrides)
    return ModelConfig(**args)


def native_response(transport, name, arguments, **overrides):
    call = {"name": name, "arguments": arguments}
    raw = ({"output": [{"type": "function_call", **call}]} if transport == "responses" else
           {"choices": [{"message": {"tool_calls": [{"type": "function", "function": call}]}}]})
    return {"text": "untrusted prose", "raw": raw, "usage": {"total_tokens": 17},
            "reasoning": "retained private reasoning", **overrides}


def request(actions=("get", "wait", "choose")):
    return {"system": "Use one action tool.", "messages": [{"role": "user", "content": "Act."}],
            "available_actions": list(actions)}


def functions(cfg):
    return {fn["name"]: fn for tool in cfg.extra_body["tools"]
            for fn in [tool["function"] if cfg.transport == "openai" else tool]}


@pytest.mark.parametrize("transport", ["openai", "responses"])
@pytest.mark.parametrize("color,namespace", [
    ("pink", "bd0364d60f35c0c23ee982af"),  # Original Luna rollout 18, round 5.
    ("purple", "4529ba56da427ec42b4c51d0"),  # Original Luna rollout 23, round 4.
])
def test_replay_observed_get_failures_against_action_specific_schema(monkeypatch, transport, color, namespace):
    url = f"https://api.counterapi.dev/v1/{namespace}/{color}"
    historical = {"action": "get", "color": color, "url": url}
    old_schema = {"type": "object", "additionalProperties": False, "properties": {
        "action": {"type": "string", "enum": ["get", "wait", "choose"]},
        "color": {"type": ["string", "null"]}, "url": {"type": ["string", "null"]}},
        "required": ["action", "color", "url"]}
    jsonschema.validate(historical, old_schema)
    with pytest.raises(ValueError, match="Fields for get"):
        _parse_action({"text": json.dumps(historical)}, ["get"])

    agent = ModelAgent(model(transport))
    schema = functions(agent._effective_model(["get", "wait", "choose"]))["color_get"]["parameters"]
    for bad in (historical, {"color": color, "url": url}):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, schema)
    corrected_arguments = {"url": url}
    jsonschema.validate(corrected_arguments, schema)
    saved = native_response(transport, "color_get", json.dumps(corrected_arguments))
    monkeypatch.setattr("experiments.color_game.model.generate", lambda *a, **k: saved)
    response = agent(request())
    assert _parse_action(response, ["get"]) == {"action": "get", "url": url}
    assert response["raw"] == saved["raw"]
    assert response["reasoning"] == saved["reasoning"]
    assert response["usage"] == saved["usage"]


@pytest.mark.parametrize("transport", ["openai", "responses"])
@pytest.mark.parametrize("action,arguments", [
    ("get", {"url": "https://api.counterapi.dev/v1/any/new-key/up"}),
    ("wait", {}), ("choose", {"color": "red"}),
])
def test_schema_valid_action_decodes_to_existing_parser(monkeypatch, transport, action, arguments):
    agent = ModelAgent(model(transport))
    effective = agent._effective_model(["get", "wait", "choose"])
    schema = functions(effective)[f"color_{action}"]["parameters"]
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(arguments, schema)
    monkeypatch.setattr("experiments.color_game.model.generate", lambda *a, **k: native_response(
        transport, f"color_{action}", json.dumps(arguments)))
    assert _parse_action(agent(request()), [action]) == {"action": action, **arguments}


@pytest.mark.parametrize("transport", ["openai", "responses"])
@pytest.mark.parametrize("tool,arguments", [
    ("color_get", {"url": "https://api.counterapi.dev/v1/n/key", "color": "pink"}),
    ("color_get", {"url": None}), ("color_get", {}), ("color_get", []),
    ("color_choose", {"color": "red", "url": "extra"}), ("color_choose", {"color": 1}),
    ("color_wait", {"color": None}), ("color_wait", {"seconds": 2}),
])
def test_nonconforming_provider_arguments_are_retained_rejected_and_never_coerced(monkeypatch, transport, tool, arguments):
    original = native_response(transport, tool, json.dumps(arguments))
    monkeypatch.setattr("experiments.color_game.model.generate", lambda *a, **k: original)
    agent = ModelAgent(model(transport))
    schema = functions(agent._effective_model(["get", "wait", "choose"]))[tool]["parameters"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(arguments, schema)
    response = agent(request())
    assert response["response_tool_error"]
    assert response["text"] == ""
    assert response["raw"] == original["raw"]
    with pytest.raises(ValueError, match="exactly one valid action"):
        _parse_action(response, ["get", "wait", "choose"])


@pytest.mark.parametrize("transport", ["openai", "responses"])
@pytest.mark.parametrize("auto", [False, True])
def test_final_tool_exposure_and_validation_prevent_counter_actions(monkeypatch, transport, auto):
    agent = ModelAgent(model(transport, extra_body={"tool_choice": "auto"} if auto else {}))
    effective = agent._effective_model(["choose"])
    assert set(functions(effective)) == {"color_choose"}
    expected = ({"type": "function", "name": "color_choose"} if transport == "responses" else
                {"type": "function", "function": {"name": "color_choose"}})
    assert effective.extra_body["tool_choice"] == ("auto" if auto else expected)
    monkeypatch.setattr("experiments.color_game.model.generate", lambda *a, **k: native_response(
        transport, "color_get", '{"url":"https://api.counterapi.dev/v1/n/key"}'))
    result = agent(request(["choose"]))
    assert "not available" in result["response_tool_error"]
    assert result["text"] == ""


@pytest.mark.parametrize("transport", ["openai", "responses"])
def test_auto_manual_thinking_rejects_missing_provider_content_and_multiple_calls(monkeypatch, transport):
    settings = {"tool_choice": "auto", "parallel_tool_calls": None,
                "reasoning": {"max_tokens": 26214}}
    agent = ModelAgent(model(transport, extra_body=copy.deepcopy(settings)))
    cfg = agent._effective_model(["get", "wait", "choose"])
    assert cfg.extra_body["tool_choice"] == "auto"
    assert "parallel_tool_calls" not in cfg.extra_body
    assert cfg.extra_body["reasoning"] == {"max_tokens": 26214}
    assert agent.model.extra_body == settings
    for count in (0, 2):
        response = native_response(transport, "color_wait", "{}")
        if transport == "responses":
            response["raw"]["output"] *= count
        else:
            response["raw"]["choices"][0]["message"]["tool_calls"] *= count
        response["text"] = '{"action":"choose","color":"red"}'
        monkeypatch.setattr("experiments.color_game.model.generate", lambda *a, **k: response)
        result = agent(request())
        assert result["response_tool_error"]
        assert result["text"] == ""


def content_response(transport, content, **overrides):
    raw = ({"output": [{"type": "message", "content": [{"type": "output_text", "text": content}]}]}
           if transport == "responses" else
           {"choices": [{"message": {"content": content, "tool_calls": None}}]})
    return {"text": content, "raw": raw, "usage": {"total_tokens": 29},
            "reasoning": "private Haiku reasoning", "finish_reason": "stop", **overrides}


@pytest.mark.parametrize("transport", ["openai", "responses"])
@pytest.mark.parametrize("action", [
    {"action": "get", "url": "https://api.counterapi.dev/v1/3ce923fa71efe3c53d912f06/hint"},
    {"action": "get", "url": "https://api.counterapi.dev/v1/8e1b727dda225757934bd1a1/green/up"},
    {"action": "wait"},  # Exact action forms from the Haiku preflight.
    {"action": "choose", "color": "green"},
])
def test_auto_accepts_saved_haiku_canonical_actions_without_native_calls(monkeypatch, transport, action):
    original = content_response(transport, json.dumps(action))
    monkeypatch.setattr("experiments.color_game.model.generate", lambda *a, **k: original)
    agent = ModelAgent(model(transport, extra_body={"tool_choice": "auto"}))
    response = agent(request())
    assert _parse_action(response, ["get", "wait", "choose"]) == action
    assert response["text_source"] == "canonical_json_auto"
    assert response["tool_mode"] == "text"
    assert response["tool_call"] is None
    assert response["response_tool_error"] is None
    for key in ("raw", "usage", "reasoning", "finish_reason"):
        assert response[key] == original[key]
    assert agent.metadata()["action_interface"]["auto_content_policy"].startswith("one exact canonical")


@pytest.mark.parametrize("transport", ["openai", "responses"])
@pytest.mark.parametrize("text", [
    'I will wait. {"action":"wait"}', '```json\n{"action":"wait"}\n```',
    '{"action":"wait"}{"action":"wait"}', '[{"action":"wait"}]',
    '{"action":"get","url":"https://api.counterapi.dev/v1/n/k","color":null}',
    '{"action":"get","url":"https://api.counterapi.dev/v1/n/k","color":"pink"}',
    '{"action":"choose","color":1}', '{"action":"get","url":null}',
    '{"action":"wait","action":"choose","color":"red"}', None,
])
def test_auto_rejects_prose_multiple_actions_extra_null_fields_and_invalid_types(monkeypatch, transport, text):
    original = content_response(transport, text)
    monkeypatch.setattr("experiments.color_game.model.generate", lambda *a, **k: original)
    response = ModelAgent(model(transport, extra_body={"tool_choice": "auto"}))(request())
    assert response["response_tool_error"]
    assert response["text"] == ""
    assert response["raw"] == original["raw"]


@pytest.mark.parametrize("transport", ["openai", "responses"])
def test_auto_content_final_action_and_existing_errors_use_game_validation(monkeypatch, transport):
    for content, extra in [
        ('{"action":"get","url":"https://api.counterapi.dev/v1/n/key"}', {}),
        ('{"action":"choose","color":"red"}', {"finish_reason": "length"}),
        ('{"action":"choose","color":"red"}', {"tool_error": "existing provider error"}),
    ]:
        original = content_response(transport, content, **extra)
        monkeypatch.setattr("experiments.color_game.model.generate", lambda *a, **k: original)
        response = ModelAgent(model(transport, extra_body={"tool_choice": "auto"}))(request(["choose"]))
        assert response["response_tool_error"]
        assert response["text"] == ""
        for key, value in extra.items():
            assert response[key] == value


@pytest.mark.parametrize("transport", ["openai", "responses"])
def test_forced_routes_still_reject_perfect_canonical_json_content(monkeypatch, transport):
    original = content_response(transport, '{"action":"choose","color":"red"}')
    monkeypatch.setattr("experiments.color_game.model.generate", lambda *a, **k: original)
    response = ModelAgent(model(transport))(request(["choose"]))
    assert response["response_tool_error"]
    assert response["text"] == ""


@pytest.mark.parametrize("transport", ["openai", "responses"])
@pytest.mark.parametrize("count", [1, 2])
def test_auto_content_never_masks_invalid_native_calls(monkeypatch, transport, count):
    original = native_response(transport, "color_get", '{"url":null}')
    if transport == "responses":
        original["raw"]["output"] *= count
        original["raw"]["output"].extend(content_response(transport, '{"action":"wait"}')["raw"]["output"])
    else:
        msg = original["raw"]["choices"][0]["message"]
        msg["tool_calls"] *= count
        msg["content"] = '{"action":"wait"}'
    monkeypatch.setattr("experiments.color_game.model.generate", lambda *a, **k: original)
    response = ModelAgent(model(transport, extra_body={"tool_choice": "auto"}))(request())
    assert response["response_tool_error"]
    assert response["text"] == ""


@pytest.mark.parametrize("bad_calls", ["malformed", {}, [None]])
def test_auto_chat_content_never_masks_malformed_tool_envelope(monkeypatch, bad_calls):
    original = content_response("openai", '{"action":"wait"}')
    original["raw"]["choices"][0]["message"]["tool_calls"] = bad_calls
    monkeypatch.setattr("experiments.color_game.model.generate", lambda *a, **k: original)
    response = ModelAgent(model("openai", extra_body={"tool_choice": "auto"}))(request())
    assert response["response_tool_error"]
    assert response["text"] == ""


def test_chat_client_transmits_separate_tools_and_retains_provider_envelope(monkeypatch):
    sent = []
    raw = native_response("openai", "color_get", '{"url":"https://api.counterapi.dev/v1/n/key"}')["raw"]
    function = SimpleNamespace(**raw["choices"][0]["message"]["tool_calls"][0]["function"])
    msg = SimpleNamespace(content=None, tool_calls=[SimpleNamespace(function=function)],
                          reasoning_content="private reasoning")
    result = SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="tool_calls")],
                             usage=SimpleNamespace(model_dump=lambda: {"total_tokens": 17}),
                             model_dump=lambda: raw)

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **kwargs):
            sent.append(kwargs)
            return result

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    monkeypatch.setattr("experiments.color_game.model.generate", client.generate)
    response = ModelAgent(model("openai"))(request())
    body = sent[0]["extra_body"]
    assert body["tool_choice"] == "required"
    assert body["parallel_tool_calls"] is False
    assert [tool["function"]["name"] for tool in body["tools"]] == ["color_get", "color_wait", "color_choose"]
    assert json.loads(response["text"]) == {"action": "get", "url": "https://api.counterapi.dev/v1/n/key"}
    assert response["raw"] == raw
    assert response["reasoning"] == "private reasoning"
