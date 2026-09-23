"""Retry the same model request after temporary account pressure, without API calls."""
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier

import httpx
import openai
import pytest

from ai_collusion import client


def error_body(reason="in_flight_budget_exhausted", retry_after="120"):
    return {"error": {"code": 402, "message": "In-flight requests exceed available credits.",
                      "metadata": {"reason": reason, "limit_source": "openrouter_in_flight_budget",
                                   "headers": {"Retry-After": retry_after}, "provider_name": None}}}


def upstream_rate_limit_body():
    # The Friendli error that stopped the campaign has no Retry-After header.
    return {"error": {"code": 429, "message": "Provider returned error", "metadata": {
        "raw": "z-ai/glm-5.3 is temporarily rate-limited upstream. Please retry shortly.",
        "provider_name": "Friendli", "limit_source": "upstream_provider_shared_pool"}}}


def sdk_error(*, status=402, body=None, headers=None):
    response = httpx.Response(status, headers=headers, json=body or error_body(),
                              request=httpx.Request("POST", "https://example.test/chat/completions"))
    return openai.APIStatusError("Model request failed", response=response, body=response.json())


@pytest.fixture
def waits(monkeypatch):
    recorded = []
    monkeypatch.setattr(client.time, "sleep", recorded.append)
    monkeypatch.setattr(client.random, "uniform", lambda *args: 0.0)
    return recorded


@pytest.mark.parametrize("sdk_unwrapped", [False, True])
def test_temporary_402_retries_identical_request_and_keeps_diagnostics(monkeypatch, waits, sdk_unwrapped):
    requests = []
    body = error_body()
    if sdk_unwrapped:
        body = body["error"]

    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(402, json=body)
        return httpx.Response(200, json={
            "id": "local-test", "object": "chat.completion", "created": 0, "model": "test",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"},
                         "finish_reason": "stop"}], "usage": None,
        })

    real_client = openai.OpenAI
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: real_client(
        **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler))))
    cfg = client.ModelConfig("test", "openai", "test", base_url="https://example.test/v1",
                             retries=2, max_tokens=32768, extra_body={"reasoning": {"effort": "high"}})
    result = client.generate(cfg, "system", [{"role": "user", "content": "same turn"}],
                             temperature=0.7, seed=None)
    assert result["text"] == "ok"
    assert requests[0] == requests[1]
    assert requests[0]["max_tokens"] == 32768
    assert requests[0]["reasoning"] == {"effort": "high"}
    assert waits == [120.0]
    assert len(result["retry_events"]) == 1
    event = result["retry_events"][0]
    assert event["status_code"] == 402
    assert event["reason"] == "in_flight_budget_exhausted"
    assert event["attempt"] == 1
    assert event["wait_s"] == 120.0


def test_sustained_budget_pressure_stops_after_configured_attempts(waits):
    calls = []

    def call():
        calls.append(True)
        raise sdk_error()

    with pytest.raises(openai.APIStatusError) as caught:
        client._with_retries(client.ModelConfig("test", "stub", "test", retries=2), call)
    assert len(calls) == 3
    assert waits == [120.0, 120.0]
    details = client.model_error_details(caught.value)
    assert details["reason"] == "in_flight_budget_exhausted"
    assert details["retry_exhausted"] is True
    assert details["retry_stop_reason"] == "attempt_limit"
    assert details["attempts"] == 3
    assert len(details["retry_events"]) == 3
    assert details["retry_events"][-1]["wait_s"] == 0


def test_upstream_429_recovers_after_three_rejections_without_changing_request(monkeypatch, waits):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) <= 3:
            return httpx.Response(429, json=upstream_rate_limit_body())
        return httpx.Response(200, json={
            "id": "local-test", "object": "chat.completion", "created": 0, "model": "test",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": '{"action":"done"}'},
                         "finish_reason": "stop"}], "usage": None,
        })

    real_client = openai.OpenAI
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: real_client(
        **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler))))
    cfg = client.ModelConfig("test", "openai", "test", base_url="https://example.test/v1",
                             retries=2, max_tokens=32768, extra_body={"reasoning": {"effort": "high"}})
    result = client.generate(cfg, "system", [{"role": "user", "content": "same question and turn"}],
                             temperature=0.7, seed=None)
    assert result["text"] == '{"action":"done"}'
    assert len(requests) == 4 and all(request == requests[0] for request in requests)
    assert requests[0]["max_tokens"] == 32768 and requests[0]["reasoning"] == {"effort": "high"}
    assert waits == [60.0] * 3
    assert len(result["retry_events"]) == 3
    assert all(e["provider_name"] == "Friendli" and e["limit_source"] == "upstream_provider_shared_pool"
               for e in result["retry_events"])


def test_sustained_upstream_429_has_bounded_attempt_budget(waits):
    calls = []

    def call():
        calls.append(True)
        raise sdk_error(status=429, body=upstream_rate_limit_body())

    with pytest.raises(openai.APIStatusError) as caught:
        client._with_retries(client.ModelConfig("test", "stub", "test", retries=2), call)
    assert len(calls) == 8
    assert waits == [60.0] * 7
    details = client.model_error_details(caught.value)
    assert details["attempts"] == 8 and details["retry_exhausted"]
    assert details["retry_stop_reason"] == "attempt_limit"
    assert details["retry_events"][-1]["wait_s"] == 0


def test_explicit_zero_retries_disables_429_retry_override(waits):
    calls = []

    def call():
        calls.append(True)
        raise sdk_error(status=429, body=upstream_rate_limit_body())

    with pytest.raises(openai.APIStatusError) as caught:
        client._with_retries(client.ModelConfig("test", "stub", "test", retries=0), call)
    assert len(calls) == 1 and waits == []
    assert client.model_error_details(caught.value)["retry_stop_reason"] == "attempt_limit"


def test_upstream_429_never_exceeds_total_retry_wait_budget(waits):
    calls = []

    def call():
        calls.append(True)
        raise sdk_error(status=429, body=upstream_rate_limit_body(), headers={"Retry-After": "180"})

    with pytest.raises(openai.APIStatusError) as caught:
        client._with_retries(client.ModelConfig("test", "stub", "test", retries=2), call)
    assert len(calls) == 6 and waits == [180.0] * 5
    assert sum(waits) == 900.0
    assert client.model_error_details(caught.value)["retry_stop_reason"] == "wait_limit"


@pytest.mark.parametrize("status", [400, 403])
def test_permanent_http_errors_still_fail_immediately(waits, status):
    calls = []

    def call():
        calls.append(True)
        raise sdk_error(status=status, body={"error": {"code": status}})

    with pytest.raises(openai.APIStatusError) as caught:
        client._with_retries(client.ModelConfig("test", "stub", "test", retries=2), call)
    assert len(calls) == 1 and waits == []
    assert client.model_error_details(caught.value)["retry_stop_reason"] == "not_retryable"


@pytest.mark.parametrize("body", [
    error_body("insufficient_credits"),
    {"error": {"message": "Add credits to your account."}},
    {"error": {"message": "in_flight_budget_exhausted appears only in text"}},
])
def test_other_402_errors_are_not_retried_even_with_retry_header(waits, body):
    exc = sdk_error(body=body, headers={"Retry-After": "120"})
    calls = []

    def call():
        calls.append(True)
        raise exc

    with pytest.raises(openai.APIStatusError) as caught:
        client._with_retries(client.ModelConfig("test", "stub", "test", retries=3), call)
    assert caught.value is exc
    assert len(calls) == 1
    assert waits == []
    assert client.model_error_details(exc)["retry_exhausted"] is False


def test_response_retry_header_precedes_nested_header_and_supports_http_date(monkeypatch, waits):
    monkeypatch.setattr(client.time, "time", lambda: 0.0)
    exc = sdk_error(headers={"rEtRy-AfTeR": "Thu, 01 Jan 1970 00:03:00 GMT"})
    attempts = iter([exc, "ok"])

    def call():
        result = next(attempts)
        if isinstance(result, Exception):
            raise result
        return result

    assert client._with_retries(client.ModelConfig("test", "stub", "test"), call) == "ok"
    assert waits == [180.0]


@pytest.mark.parametrize("value", ["invalid", "nan", "inf", "-10"])
def test_invalid_response_header_falls_back_to_nested_metadata(value):
    assert client.model_error_details(sdk_error(headers={"Retry-After": value}))["retry_after_s"] == 120


def test_server_delay_over_wait_budget_does_not_retry_early(waits):
    def call():
        raise sdk_error(headers={"Retry-After": "3600"})

    with pytest.raises(openai.APIStatusError) as caught:
        client._with_retries(client.ModelConfig("test", "stub", "test"), call)
    assert waits == []
    details = client.model_error_details(caught.value)
    assert details["retry_after_s"] == 3600
    assert details["retry_stop_reason"] == "wait_limit"


def test_missing_transient_budget_header_uses_conservative_wait(waits):
    body = error_body()
    del body["error"]["metadata"]["headers"]
    attempts = iter([sdk_error(body=body), "ok"])

    def call():
        result = next(attempts)
        if isinstance(result, Exception):
            raise result
        return result

    assert client._with_retries(client.ModelConfig("test", "stub", "test"), call) == "ok"
    assert waits == [120.0]


@pytest.mark.parametrize("status,expected_wait", [(429, 30.0), (503, 30.0), (500, 30.0)])
def test_existing_transient_statuses_honor_retry_after(waits, status, expected_wait):
    attempts = iter([sdk_error(status=status, body={"error": {}}, headers={"Retry-After": "30"}), "ok"])

    def call():
        result = next(attempts)
        if isinstance(result, Exception):
            raise result
        return result

    assert client._with_retries(client.ModelConfig("test", "stub", "test"), call) == "ok"
    assert waits == [expected_wait]


@pytest.mark.parametrize("status,expected_wait", [(402, 125.0), (429, 65.0)])
def test_jitter_never_retries_before_server_deadline(monkeypatch, waits, status, expected_wait):
    monkeypatch.setattr(client.random, "uniform", lambda lower, upper: upper)
    attempts = iter([sdk_error(status=status, body=upstream_rate_limit_body() if status == 429 else None), "ok"])

    def call():
        result = next(attempts)
        if isinstance(result, Exception):
            raise result
        return result

    assert client._with_retries(client.ModelConfig("test", "stub", "test"), call) == "ok"
    assert waits == [expected_wait]


def test_attempt_slot_is_released_before_retry_hook_and_sleep(monkeypatch, waits):
    order = []
    active = False
    attempts = iter([sdk_error(), "ok"])

    @contextmanager
    def admission():
        nonlocal active
        assert not active
        active = True
        order.append("enter")
        try:
            yield
        finally:
            active = False
            order.append("release")

    def call():
        assert active
        order.append("call")
        result = next(attempts)
        if isinstance(result, Exception):
            raise result
        return result

    def retry(event):
        assert not active
        assert event["reason"] == "in_flight_budget_exhausted"
        assert event["status_code"] == 402
        assert event["wait_s"] == 120
        order.append("retry")

    def sleep(seconds):
        assert not active
        order.append("sleep")

    monkeypatch.setattr(client.time, "sleep", sleep)
    with client.request_control(attempt_context=admission, on_retry=retry):
        assert client._with_retries(client.ModelConfig("test", "stub", "test"), call) == "ok"
    assert order == ["enter", "call", "release", "retry", "sleep", "enter", "call", "release"]


@pytest.mark.parametrize("status,expected_wait", [(402, 120.0), (429, 60.0)])
def test_cancellation_during_backoff_blocks_next_api_attempt(monkeypatch, waits, status, expected_wait):
    cancelled = False
    calls = []

    @contextmanager
    def admission():
        if cancelled:
            raise RuntimeError("Campaign stopped")
        yield

    def call():
        calls.append(True)
        raise sdk_error(status=status, body=upstream_rate_limit_body() if status == 429 else None)

    def sleep(seconds):
        nonlocal cancelled
        waits.append(seconds)
        cancelled = True

    monkeypatch.setattr(client.time, "sleep", sleep)
    with client.request_control(attempt_context=admission):
        with pytest.raises(RuntimeError, match="Campaign stopped") as caught:
            client._with_retries(client.ModelConfig("test", "stub", "test"), call)
    assert len(calls) == 1
    assert waits == [expected_wait]
    assert not hasattr(caught.value, "_client_retry_details")


@pytest.mark.parametrize("failure_stage", ["enter", "exit", "retry"])
def test_control_hook_failure_is_never_retried_and_scope_is_reset(waits, failure_stage):
    calls = []

    @contextmanager
    def admission():
        if failure_stage == "enter":
            raise RuntimeError("Hook failed")
        yield
        if failure_stage == "exit":
            raise RuntimeError("Hook failed")

    def call():
        calls.append(True)
        if failure_stage == "retry":
            raise sdk_error()
        return "ok"

    def retry(event):
        raise RuntimeError("Hook failed")

    with pytest.raises(RuntimeError, match="Hook failed"):
        with client.request_control(attempt_context=admission, on_retry=retry):
            client._with_retries(client.ModelConfig("test", "stub", "test"), call)
    assert len(calls) == (0 if failure_stage == "enter" else 1)
    assert waits == []
    assert client._with_retries(client.ModelConfig("test", "stub", "test"), lambda: "unscoped") == "unscoped"


def test_request_control_is_scoped_to_thread_and_restores_outer_scope():
    barrier = Barrier(2)
    seen = []
    cfg = client.ModelConfig("test", "stub", "test")

    @contextmanager
    def admission():
        seen.append("scoped")
        yield

    def scoped():
        with client.request_control(attempt_context=admission):
            barrier.wait(timeout=5)
            with client.request_control():
                assert client._with_retries(cfg, lambda: "inner") == "inner"
            assert client._with_retries(cfg, lambda: "outer") == "outer"

    def unscoped():
        barrier.wait(timeout=5)
        assert client._with_retries(cfg, lambda: "other thread") == "other thread"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(scoped), pool.submit(unscoped)]
        for future in futures:
            future.result(timeout=5)
    assert seen == ["scoped"]


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_admission_observes_api_failure_and_cleanup_errors_are_not_retried(waits, cleanup_fails):
    failure = sdk_error(status=503)
    attempts = iter([failure, "recovered"])
    observed, calls = [], []

    @contextmanager
    def admission():
        try:
            yield
        except Exception as exc:
            observed.append(exc)
            if cleanup_fails:
                raise RuntimeError("Admission cleanup failed") from exc
            raise

    def call():
        calls.append(True)
        result = next(attempts)
        if isinstance(result, Exception):
            raise result
        return result

    with client.request_control(attempt_context=admission):
        if cleanup_fails:
            with pytest.raises(RuntimeError, match="Admission cleanup failed") as caught:
                client._with_retries(client.ModelConfig("test", "stub", "test"), call)
            assert not hasattr(caught.value, "_client_retry_details")
        else:
            assert client._with_retries(client.ModelConfig("test", "stub", "test"), call) == "recovered"
    assert observed == [failure]
    assert len(calls) == (1 if cleanup_fails else 2)
    assert len(waits) == (0 if cleanup_fails else 1)
