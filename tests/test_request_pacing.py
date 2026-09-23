from types import SimpleNamespace

import pytest

from ai_collusion import rate_limit


def test_request_starts_are_spaced_without_waiting_for_completion():
    now = [10.0]
    sleeps = []
    def sleep(delay):
        sleeps.append(delay)
        now[0] += delay
    pacer = rate_limit.RequestPacer(100, clock=lambda: now[0], sleep=sleep)
    for _ in range(3):
        pacer.wait()
    assert sleeps == pytest.approx([0.6, 0.6])


def test_configured_model_does_not_limit_another_model(monkeypatch):
    monkeypatch.setattr(rate_limit, "_PACERS", {})
    a = SimpleNamespace(transport="openai", base_url="url", model="a", api_key_env="KEY")
    b = SimpleNamespace(transport="openai", base_url="url", model="b", api_key_env="KEY")
    waited = []
    rate_limit._PACERS[rate_limit._key(a)] = SimpleNamespace(wait=lambda: waited.append("a"))
    rate_limit.wait_for_request(b)
    assert waited == []
    rate_limit.wait_for_request(a)
    assert waited == ["a"]


def test_transport_retries_are_also_paced(monkeypatch):
    from ai_collusion.client import ModelConfig, _with_retries
    model = ModelConfig("test", "openai", "test", retries=1)
    waited, calls = [], []
    monkeypatch.setattr(rate_limit, "wait_for_request", lambda cfg: waited.append(cfg.name))
    monkeypatch.setattr("ai_collusion.client.time.sleep", lambda delay: None)
    class TemporaryError(Exception):
        status_code = 502
    def request():
        calls.append(1)
        if len(calls) == 1:
            raise TemporaryError()
        return "ok"
    assert _with_retries(model, request) == "ok"
    assert waited == ["test", "test"]


def test_pacing_and_scoped_admission_compose_on_each_retry(monkeypatch):
    from contextlib import contextmanager
    from ai_collusion import client

    events = []
    monkeypatch.setattr(rate_limit, "wait_for_request", lambda cfg: events.append("pace"))
    monkeypatch.setattr(client.time, "sleep", lambda delay: events.append("backoff"))

    @contextmanager
    def admission():
        events.append("enter")
        try:
            yield
        finally:
            events.append("release")

    attempts = 0

    def request():
        nonlocal attempts
        attempts += 1
        events.append("request")
        if attempts == 1:
            error = RuntimeError("temporary failure")
            error.status_code = 502
            raise error
        return "ok"

    retries = []
    with client.request_control(attempt_context=admission, on_retry=lambda event: events.append("retry")):
        assert client._with_retries(client.ModelConfig("test", "openai", "test"),
                                    request, retry_events=retries) == "ok"
    assert events == ["pace", "enter", "request", "release", "retry", "backoff",
                      "pace", "enter", "request", "release"]
    assert len(retries) == 1 and retries[0]["status_code"] == 502
