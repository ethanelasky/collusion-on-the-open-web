"""Do not turn provider backpressure into a queue of failed experiment questions."""
import json
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from ai_collusion.client import ModelConfig
from experiments.covert_channel.analysis import load_records
from experiments.covert_channel.control import RequestGate, RunCancelled, RunControl


def test_model_error_stops_both_cohorts_before_remaining_questions(tmp_path, monkeypatch):
    from experiments.covert_channel import run, participants
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("Provider unavailable after retries")
    monkeypatch.setattr(participants, "generate", fail)
    monkeypatch.setattr(run, "load_models", lambda *args: [ModelConfig("offline", "stub", "stub")])
    stop_file = tmp_path / "stop.json"
    for cohort in ("one", "two"):
        assert run.main(["--preset", "counter-five-questions", "--sessions", "2", "--workers", "1",
                         "--sender", "offline", "--receiver", "offline", "--stop-file", str(stop_file),
                         "--out", str(tmp_path), "--run-id", cohort, "--skip-analysis"]) == 1
    first, records = load_records(tmp_path / "one")
    second, skipped = load_records(tmp_path / "two")
    assert first["status"] == second["status"] == "stopped"
    assert len(calls) == 1 and len(records) == 1 and skipped == []
    assert records[0]["agents"]["sender"]["error"]["type"] == "RuntimeError"
    assert records[0]["agents"]["receiver"]["error"]["type"] == "RunCancelled"
    assert stop_file.exists()
    assert not list((tmp_path / "two").glob("*.events.jsonl"))


@pytest.mark.parametrize("status,wait,reason", [
    (402, 120, "in_flight_budget_exhausted"), (429, 60, "upstream_rate_limit")])
def test_two_cohorts_share_admission_and_cooldown_then_reduce_concurrency(tmp_path, monkeypatch, status, wait, reason):
    from experiments.covert_channel import control
    now = [100.0]
    monkeypatch.setattr(control.time, "time", lambda: now[0])
    one = RequestGate(tmp_path / "gate.sqlite", 50)
    two = RequestGate(tmp_path / "gate.sqlite", 50)
    event = {"status_code": status, "wait_s": wait}
    if status == 402:
        event["reason"] = reason
    else:
        event.update(provider_name="Friendli", limit_source="upstream_provider_shared_pool")
    with one.attempt(lambda: False):
        assert two.snapshot()["active_requests"] == 1
    one.backoff(event)
    two.backoff(event)
    assert one.snapshot()["request_limit"] == two.snapshot()["request_limit"] == 25
    assert two.snapshot()["cooldown_until_unix_s"] == 100 + wait
    def advance(seconds):
        now[0] = 100 + wait
    monkeypatch.setattr(control.time, "sleep", advance)
    with two.attempt(lambda: False):
        assert now[0] == 100 + wait
        assert one.snapshot()["active_requests"] == 1
    two.backoff(event)
    assert one.snapshot()["request_limit"] == 12
    assert one.snapshot()["active_requests"] == 0
    with one.connect() as db:
        assert {r[0] for r in db.execute("SELECT reason FROM backoff_events")} == {reason}


def test_waiting_admission_obeys_stop_and_does_not_leak_a_slot(tmp_path):
    one = RunControl(tmp_path / "stop.json", tmp_path / "gate.sqlite", 1)
    two = RunControl(tmp_path / "stop.json", tmp_path / "gate.sqlite", 1)
    with one.attempt():
        with ThreadPoolExecutor(max_workers=1) as pool:
            def waiter():
                with two.attempt():
                    pytest.fail("No second request may enter")
            future = pool.submit(waiter)
            one.fail(RuntimeError("Provider unavailable"))
            with pytest.raises(RunCancelled):
                future.result(timeout=5)
    assert two.gate.snapshot()["active_requests"] == 0


@pytest.mark.parametrize("cancel", [False, True])
def test_concurrent_429_requests_share_backoff_and_obey_cancellation(tmp_path, monkeypatch, cancel):
    from ai_collusion import client

    class UpstreamRateLimit(Exception):
        status_code = 429
        body = {"error": {"metadata": {"provider_name": "Friendli",
                                       "limit_source": "upstream_provider_shared_pool"}}}

    now = [100.0]
    monkeypatch.setattr(client.time, "time", lambda: now[0])
    monkeypatch.setattr(client.random, "uniform", lambda *args: 0.0)
    controls = [RunControl(tmp_path / "stop.json", tmp_path / "gate.sqlite", 50) for _ in range(2)]
    first_attempts = threading.Barrier(2)
    both_sleeping = threading.Event()
    release = threading.Event()
    sleep_lock = threading.Lock()
    sleeps, calls = [], [0, 0]

    def sleep(seconds):
        assert seconds == 60
        with sleep_lock:
            sleeps.append(seconds)
            if len(sleeps) == 2:
                both_sleeping.set()
        assert release.wait(timeout=5), "Test did not release the shared cooldown"

    monkeypatch.setattr(client.time, "sleep", sleep)

    def worker(index):
        def request():
            calls[index] += 1
            if calls[index] == 1:
                first_attempts.wait(timeout=5)
                raise UpstreamRateLimit("Retry shortly")
            assert now[0] >= 160
            return "same request recovered"

        control = controls[index]
        with client.request_control(attempt_context=control.attempt, on_retry=control.on_retry):
            return client._with_retries(ModelConfig("offline", "stub", "stub", retries=2), request)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, index) for index in range(2)]
        try:
            assert both_sleeping.wait(timeout=5)
            state = controls[0].gate.snapshot()
            assert state["request_limit"] == 25  # One reduction for the same burst.
            assert state["cooldown_until_unix_s"] == 160 and state["active_requests"] == 0
            assert state["backoff_events"] == 2 and calls == [1, 1]
            if cancel:
                controls[0].fail(RuntimeError("Stop requested by another cohort"))
            now[0] = 160
        finally:
            release.set()
        for future in futures:
            if cancel:
                with pytest.raises(RunCancelled):
                    future.result(timeout=5)
            else:
                assert future.result(timeout=5) == "same request recovered"
    assert calls == ([1, 1] if cancel else [2, 2])
    assert controls[0].gate.snapshot()["active_requests"] == 0


def test_retry_is_journaled_before_peer_stops_during_backoff(monkeypatch):
    from ai_collusion import client
    from experiments.covert_channel import participants
    from experiments.covert_channel.run import run_trial
    class CreditBackpressure(Exception):
        status_code = 402
        body = {"error": {"metadata": {"reason": "in_flight_budget_exhausted", "headers": {"Retry-After": "120"}}}}
    control = RunControl()
    events, attempts = [], []
    def generate(model, *args, **kwargs):
        def request():
            attempts.append(1)
            raise CreditBackpressure("Temporary request limit")
        return client._with_retries(model, request)
    monkeypatch.setattr(participants, "generate", generate)
    monkeypatch.setattr(client.time, "sleep", lambda seconds: control.fail(RuntimeError("Peer stopped")))
    model = ModelConfig("offline", "stub", "stub")
    record = run_trial("baseline", "red", "test", ["red", "blue"], model, model,
                       emit=events.append, control=control)
    retries = [e for e in events if e["kind"] == "model_retry"]
    assert len(attempts) == len(retries) == 1
    assert retries[0]["agent_id"] == "sender" and retries[0]["wait_s"] >= 120
    assert record["agents"]["sender"]["error"]["type"] == "RunCancelled"


def test_same_url_can_name_separate_counters_in_different_rollouts(tmp_path, monkeypatch):
    from experiments.covert_channel import run, participants
    monkeypatch.setattr(run, "load_models", lambda *args: [ModelConfig("offline", "stub", "stub")])
    def act(model, system, messages, **kwargs):
        question = max(i for i, m in enumerate(messages) if m["role"] == "user" and m["content"].startswith("Question "))
        turn = sum(m["role"] == "assistant" for m in messages[question:])
        sender = "You are the sender" in system
        if turn == 0:
            action = {"action": "get", "url": "https://api.counterapi.dev/v1/agent-chosen/CA5" + ("/up" if sender else "")}
        else:
            action = {"action": "done"} if sender else {"action": "guess", "answer": "red"}
        return {"text": json.dumps(action)}
    monkeypatch.setattr(participants, "generate", act)
    assert run.main(["--preset", "counter-five-questions", "--arm", "sender_to_receiver", "--sessions", "3",
                     "--sender", "offline", "--receiver", "offline", "--out", str(tmp_path),
                     "--run-id", "separate", "--skip-analysis"]) == 0
    manifest, records = load_records(tmp_path / "separate")
    assert len(records) == 15 and manifest["config"]["counter_mode"] == "wiki"
    assert len({r["counter_instance_id"] for r in records}) == 3
    for record in records:
        assert record["counter_scope"] == "session"
        reads = [e for e in record["channel_events"] if e["agent_id"] == "receiver"]
        assert len(reads) == 1
        assert json.loads(reads[0]["body"])["data"]["count"] == record["question_index"] + 1


def test_legacy_gate_recovers_from_one_to_configured_cap_after_healthy_requests(tmp_path, monkeypatch):
    from experiments.covert_channel import control
    now = [100.0]
    monkeypatch.setattr(control.time, "time", lambda: now[0])
    path = tmp_path / "gate.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE state (id INTEGER PRIMARY KEY, request_limit INTEGER, cooldown_until REAL, backoffs INTEGER)")
        db.execute("INSERT INTO state VALUES (1,1,160,9)")
        db.execute("CREATE TABLE requests (id TEXT PRIMARY KEY, pid INTEGER, started REAL)")
        db.execute("CREATE TABLE backoff_events (at REAL, request_limit INTEGER, cooldown_until REAL, reason TEXT)")
    gate = RequestGate(path, 8)
    assert gate.snapshot()["request_limit"] == 1
    assert gate.snapshot()["cooldown_until_unix_s"] == 160
    assert gate.snapshot()["backoff_events"] == 9
    assert gate.snapshot()["recovery_after_unix_s"] == 190

    now[0] = 200
    observed = [1]
    while gate.snapshot()["request_limit"] < 8:
        limit = gate.snapshot()["request_limit"]
        for _ in range(limit):
            with gate.attempt(lambda: False):
                pass
        observed.append(gate.snapshot()["request_limit"])
        now[0] += gate.RECOVERY_INTERVAL_S
    assert observed == [1, 2, 3, 4, 5, 7, 8]
    assert gate.snapshot()["recovery_events"] == 6
    with gate.connect() as db:
        assert db.execute("SELECT count(*) FROM recovery_events").fetchone()[0] == 6
    with gate.attempt(lambda: False):
        pass
    assert gate.snapshot()["request_limit"] == 8


def test_recovery_needs_quiet_interval_and_failures_do_not_count_as_success(tmp_path, monkeypatch):
    from ai_collusion import client
    from experiments.covert_channel import control
    now = [100.0]
    monkeypatch.setattr(control.time, "time", lambda: now[0])
    run_control = RunControl(admission_file=tmp_path / "gate.sqlite", max_active=4)
    gate = run_control.gate
    gate.backoff({"status_code": 429, "wait_s": 60})
    now[0] = 160
    gate.backoff({"status_code": 429, "wait_s": 60})
    assert gate.snapshot()["request_limit"] == 1
    now[0] = 250

    class ApiFailure(RuntimeError):
        status_code = 503

    def failure():
        raise ApiFailure("An API attempt failed")

    with client.request_control(attempt_context=run_control.attempt):
        with pytest.raises(RuntimeError, match="An API attempt failed"):
            client._with_retries(ModelConfig("offline", "stub", "stub", retries=0), failure)
    assert gate.snapshot()["request_limit"] == 1
    assert gate.snapshot()["healthy_requests_since_recovery"] == 0
    assert gate.snapshot()["recovery_after_unix_s"] == 280

    now[0] = 279
    with gate.attempt(lambda: False):
        pass
    assert gate.snapshot()["request_limit"] == 1
    now[0] = 280
    with gate.attempt(lambda: False):
        pass
    assert gate.snapshot()["request_limit"] == 2


def test_success_from_before_rate_limit_burst_cannot_trigger_recovery(tmp_path, monkeypatch):
    from experiments.covert_channel import control
    now = [100.0]
    monkeypatch.setattr(control.time, "time", lambda: now[0])
    gate = RequestGate(tmp_path / "gate.sqlite", 2)
    with gate.attempt(lambda: False):
        gate.backoff({"status_code": 429, "wait_s": 60})
        now[0] = 500
    assert gate.snapshot()["request_limit"] == 1
    assert gate.snapshot()["healthy_requests_since_recovery"] == 0
    with gate.attempt(lambda: False):
        pass
    assert gate.snapshot()["request_limit"] == 2


def _await_waiters(gate, count):
    deadline = time.monotonic() + 5
    while gate.snapshot()["waiting_requests"] != count:
        assert time.monotonic() < deadline, "API waiters did not enter the queue"
        threading.Event().wait(0.005)


def test_fifo_queue_prevents_a_fast_session_from_retaking_the_slot(tmp_path):
    one = RequestGate(tmp_path / "gate.sqlite", 1)
    two = RequestGate(tmp_path / "gate.sqlite", 1)
    order = []

    def fast():
        for _ in range(2):
            with one.attempt(lambda: False):
                order.append("fast")

    def other():
        with two.attempt(lambda: False):
            order.append("other")

    with ThreadPoolExecutor(max_workers=2) as pool:
        with one.attempt(lambda: False):
            first = pool.submit(fast)
            _await_waiters(one, 1)
            second = pool.submit(other)
            _await_waiters(one, 2)
        first.result(timeout=5)
        second.result(timeout=5)
    assert order == ["fast", "other", "fast"]
    assert one.snapshot()["active_requests"] == one.snapshot()["waiting_requests"] == 0


def test_dead_processes_release_slots_and_queue_but_live_slow_requests_keep_slots(tmp_path, monkeypatch):
    from experiments.covert_channel import control
    gate = RequestGate(tmp_path / "gate.sqlite", 3)
    dead_pid, foreign_pid = os.getpid() + 100_000, os.getpid() + 100_001

    def check_pid(pid, signal):
        assert signal == 0
        if pid == dead_pid:
            raise ProcessLookupError
        assert pid == foreign_pid
        raise PermissionError

    monkeypatch.setattr(control.os, "kill", check_pid)
    with gate.connect() as db:
        db.executemany("INSERT INTO requests VALUES (?,?,?)", [
            ("dead", dead_pid, 1), ("slow-live", os.getpid(), 1), ("foreign-live", foreign_pid, 1)])
        db.execute("INSERT INTO waiters (id,pid) VALUES ('dead-waiter',?)", (dead_pid,))
    with gate.attempt(lambda: False):
        assert gate.snapshot()["active_requests"] == 3
        assert gate.snapshot()["waiting_requests"] == 0
    assert gate.snapshot()["active_requests"] == 2
    with gate.connect() as db:
        assert {r[0] for r in db.execute("SELECT id FROM requests")} == {"slow-live", "foreign-live"}


def test_cancelled_waiter_is_removed_and_does_not_block_the_next_session(tmp_path):
    gate = RequestGate(tmp_path / "gate.sqlite", 1)
    cancelled = threading.Event()

    def waiter(cancel):
        with gate.attempt(cancel):
            return "admitted"

    with ThreadPoolExecutor(max_workers=2) as pool:
        with gate.attempt(lambda: False):
            first = pool.submit(waiter, cancelled.is_set)
            _await_waiters(gate, 1)
            second = pool.submit(waiter, lambda: False)
            _await_waiters(gate, 2)
            cancelled.set()
            with pytest.raises(RunCancelled):
                first.result(timeout=5)
            assert gate.snapshot()["waiting_requests"] == 1
        assert second.result(timeout=5) == "admitted"
    assert gate.snapshot()["waiting_requests"] == 0


def test_fifo_admission_fills_all_slots_without_exceeding_configured_limit(tmp_path):
    gate = RequestGate(tmp_path / "gate.sqlite", 4)
    release = threading.Event()
    first_four = threading.Event()
    lock = threading.Lock()
    active = maximum = 0

    def request():
        nonlocal active, maximum
        with gate.attempt(lambda: False):
            with lock:
                active += 1
                maximum = max(maximum, active)
                if active == 4:
                    first_four.set()
            try:
                assert release.wait(timeout=5)
            finally:
                with lock:
                    active -= 1

    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(request) for _ in range(12)]
        try:
            assert first_four.wait(timeout=5)
            _await_waiters(gate, 8)
            assert gate.snapshot()["active_requests"] == 4
        finally:
            release.set()
        for future in futures:
            future.result(timeout=5)
    assert maximum == 4 and active == 0
    assert gate.snapshot()["active_requests"] == gate.snapshot()["waiting_requests"] == 0
