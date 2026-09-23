"""Retry unusable HTTP-200 provider envelopes without retrying model actions."""
import copy
import json
from contextlib import contextmanager

import httpx
import openai
import pytest

from ai_collusion import client


def valid():
    return {"id": "response-local", "object": "chat.completion", "created": 0, "model": "local",
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None, "reasoning": "saved reasoning",
                "tool_calls": [{"id": "call-local", "type": "function", "function": {
                    "name": "counter_action", "arguments": '{"action":"done"}'}}]}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}


@pytest.fixture
def api(monkeypatch):
    requests, payloads, waits = [], [], []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=payloads[min(len(requests) - 1, len(payloads) - 1)])

    real_client = openai.OpenAI
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: real_client(
        **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler))))
    monkeypatch.setattr(client.time, "sleep", waits.append)
    monkeypatch.setattr(client.random, "uniform", lambda *args: 0.0)
    return payloads, requests, waits


def generate():
    cfg = client.ModelConfig("local", "openai", "local", base_url="https://example.test/v1",
                             retries=2, response_tool_name="counter_action")
    return client.generate(cfg, "system", [{"role": "user", "content": "same turn"}],
                           temperature=None, seed=None)


@pytest.mark.parametrize("choices", [None, [], [{}], [{"message": None}], [None]])
def test_malformed_envelope_retries_same_request_before_admission_success(api, choices):
    payloads, requests, waits = api
    bad = valid()
    bad["choices"] = choices
    payloads.extend([bad, valid()])
    outcomes = []

    @contextmanager
    def admission():
        try:
            yield
        except client.ProviderResponseError:
            outcomes.append("failure")
            raise
        else:
            outcomes.append("success")

    with client.request_control(attempt_context=admission):
        response = generate()
    assert outcomes == ["failure", "success"]
    assert requests[0] == requests[1] and waits == [2.0]
    assert response["text"] == '{"action":"done"}'
    assert response["reasoning"] == "saved reasoning"
    assert response["retry_events"][0]["provider_response_id"] == "response-local"


def test_missing_choices_exhaustion_keeps_safe_diagnostics(api):
    payloads, requests, waits = api
    payloads.append({"id": "bad-local", "provider_secret": "do-not-copy"})
    with pytest.raises(client.ProviderResponseError) as caught:
        generate()
    details = client.model_error_details(caught.value)
    assert len(requests) == client._CONNECTION_ATTEMPTS
    assert len(waits) == client._CONNECTION_ATTEMPTS - 1
    assert details["retry_exhausted"] is True
    assert details["attempts"] == client._CONNECTION_ATTEMPTS
    assert details["response_issue"] == "missing_or_empty_choices"
    assert details["provider_response_id"] == "bad-local"
    assert "do-not-copy" not in json.dumps(details) + str(caught.value) + json.dumps(caught.value.body)


@pytest.mark.parametrize("code,should_retry,wait", [(400, False, None), (429, True, 7.0), (503, True, 7.0)])
def test_embedded_provider_error_obeys_status_and_preserves_safe_metadata(api, code, should_retry, wait):
    payloads, requests, waits = api
    payloads.extend([{"id": "embedded-error", "choices": None, "error": {
        "code": str(code), "message": "do-not-copy", "metadata": {
            "provider_name": "OpenAI", "reason": "provider_failure", "raw": "do-not-copy",
            "headers": {"Retry-After": "7", "Authorization": "do-not-copy"}}}}, valid()])
    if should_retry:
        result = generate()
        details = result["retry_events"][0]
        assert len(requests) == 2 and waits == [wait]
    else:
        with pytest.raises(client.ProviderResponseError) as caught:
            generate()
        details = client.model_error_details(caught.value)
        assert len(requests) == 1 and not waits
        assert details["retry_exhausted"] is False
        assert "do-not-copy" not in json.dumps(caught.value.body) + str(caught.value)
    assert details["status_code"] == code
    assert details["provider_name"] == "OpenAI" and details["reason"] == "provider_failure"
    assert details["retry_after_s"] == 7 and details["response_issue"] == "embedded_provider_error"
    assert "do-not-copy" not in json.dumps(details)


@pytest.mark.parametrize("case", ["valid", "invalid_arguments", "missing_tool", "refusal", "truncated"])
@pytest.mark.parametrize("error_extension", [False, True])
def test_model_output_semantics_are_preserved_without_retry(api, case, error_extension):
    payloads, requests, waits = api
    payload = valid()
    msg = payload["choices"][0]["message"]
    if case == "invalid_arguments":
        msg["tool_calls"][0]["function"]["arguments"] = "invalid JSON action"
    elif case == "missing_tool":
        msg["tool_calls"] = None
    elif case == "refusal":
        msg.update(refusal="Cannot do this", tool_calls=None)
    elif case == "truncated":
        payload["choices"][0]["finish_reason"] = "length"
        msg["tool_calls"][0]["function"]["arguments"] = '{"action":'
    if error_extension:
        payload["error"] = {"code": 503, "message": "Provider extension after output"}
    original = copy.deepcopy(payload)
    payloads.append(payload)
    result = generate()
    assert len(requests) == 1 and not waits and not result["retry_events"]
    assert result["raw"]["choices"][0]["message"]["tool_calls"] == original["choices"][0]["message"]["tool_calls"]
    assert result["finish_reason"] == original["choices"][0]["finish_reason"]
    assert result["usage"]["total_tokens"] == 30
    assert result["reasoning"] == "saved reasoning"
    if error_extension:
        assert result["raw"]["error"] == original["error"]
    if case in {"missing_tool", "refusal"}:
        assert result["text_source"] == "rejected_tool_call"
    else:
        assert result["text"] == msg["tool_calls"][0]["function"]["arguments"]
