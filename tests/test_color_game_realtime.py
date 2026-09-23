"""Offline checks of one deadline covering actions and final color choices."""

from __future__ import annotations

import copy
import json
import threading
import time
from pathlib import Path

import pytest

from experiments.color_game import GameConfig, load_rollout, make_plan, run_rollout


URL = "https://api.counterapi.dev/v1/realtime-test/signal"


def realtime_config(**overrides):
    # Researcher fixture: seed 4 assigns red, then green for this color list.
    values = dict(setting="sync_counter",
                  colors=("red", "green", "blue"), rounds=1, actions_per_agent=3,
                  round_time_limit_s=0.08, seed=4)
    values.update(overrides)
    return GameConfig(**values)


def final_request(request):
    return request["available_actions"] == ["choose"]


def choose(color="red"):
    return {"action": "choose", "color": color}


def test_realtime_agents_advance_without_a_peer_action_barrier(tmp_path):
    """Bob's second call must start while Alice's first call is still blocked."""
    bob_advanced = threading.Event()
    alice_unblocked_by_bob = []
    observed = {"alice": [], "bob": []}

    def participant(request):
        role = request["role"]
        observed[role].append(copy.deepcopy(request))
        if final_request(request):
            return choose()
        if role == "alice":
            alice_unblocked_by_bob.append(bob_advanced.wait(timeout=0.5))
        elif len([r for r in observed[role] if not final_request(r)]) == 2:
            bob_advanced.set()
        return {"action": "wait"}

    result = run_rollout(realtime_config(round_time_limit_s=0.12), participant, participant,
                         output_dir=tmp_path / "asymmetric")
    assert alice_unblocked_by_bob and all(alice_unblocked_by_bob)
    assert all(any(final_request(request) for request in seen) for seen in observed.values())
    assert result["rounds"][0]["match"] is True


def test_early_valid_choices_end_both_players_without_extra_requests(tmp_path):
    calls = {"alice": [], "bob": []}
    window = 0.08

    def participant(request):
        calls[request["role"]].append((time.monotonic(), copy.deepcopy(request)))
        return choose("blue")

    cfg = realtime_config(round_time_limit_s=window, seed=0)
    assert make_plan(cfg)["assigned_colors"] == ["blue"]
    result = run_rollout(cfg, participant, participant,
                         output_dir=tmp_path / "early-choice")
    for role, entries in calls.items():
        assert len(entries) == 1
        assert entries[0][1]["phase"] == "play"
        assert entries[0][1]["available_actions"] == ["get", "wait", "choose"]
        assert result["rounds"][0][f"{role}_color"] == "blue"
    assert result["summary"]["total_actions"] == 2
    clock = result["rounds"][0]["clock"]
    assert clock["close_reason"] == "both_chosen"
    assert clock["closed_monotonic"] < clock["deadline_monotonic"]


def test_general_and_final_requests_share_the_same_inclusive_round_deadline(tmp_path):
    calls = {role: [] for role in ("alice", "bob")}

    def participant(request):
        calls[request["role"]].append((time.monotonic(), copy.deepcopy(request)))
        return choose() if final_request(request) else {"action": "wait"}

    result = run_rollout(realtime_config(actions_per_agent=2), participant, participant,
                         output_dir=tmp_path / "shared-clock")
    play = [request for entries in calls.values() for _, request in entries
            if request["phase"] == "play"]
    finals = [(at, request) for entries in calls.values() for at, request in entries
              if request["phase"] == "final"]
    deadline = result["rounds"][0]["clock"]["deadline_monotonic"]
    assert len(play) == len(finals) == 2
    assert all(request["request_deadline_monotonic"] == deadline for request in play)
    assert all(0 < request["round_remaining_s"] <= 0.08 for request in play)
    assert all(request["round_elapsed_s"] >= 0 for request in play)
    assert all(at < deadline for at, _ in finals)
    assert all(request["request_deadline_monotonic"] == deadline for _, request in finals)
    assert all(0 < request["request_timeout_s"] <= 0.08 for _, request in finals)
    assert result["rounds"][0]["clock"]["deadline_includes_final"] is True
    assert "final_deadline_monotonic" not in result["rounds"][0]["clock"]
    assert result["config"]["setting"] == "sync_counter"
    assert "simultaneous_mode" not in result["config"]
    assert result["config"]["round_time_limit_s"] == 0.08


def test_one_action_budget_starts_with_choose_only_before_deadline(tmp_path):
    requests = []

    def participant(request):
        requests.append(copy.deepcopy(request))
        assert final_request(request)
        return choose()

    result = run_rollout(realtime_config(actions_per_agent=1, round_time_limit_s=0.04),
                         participant, participant, output_dir=tmp_path / "one-action")
    assert len(requests) == result["summary"]["total_actions"] == 2
    assert result["rounds"][0]["match"] is True


def test_pending_calls_get_no_extra_final_request_and_cannot_mutate_after_deadline(tmp_path):
    """Both calls remain pending until run_rollout returns at its only deadline."""
    release_window = threading.Event()
    window_calls_returned = {role: threading.Event() for role in ("alice", "bob")}
    calls = {role: [] for role in ("alice", "bob")}

    def participant(request):
        role = request["role"]
        calls[role].append(copy.deepcopy(request))
        release_window.wait(timeout=0.5)
        window_calls_returned[role].set()
        return {"action": "get", "url": f"{URL}-{role}-late/up"}

    try:
        result = run_rollout(realtime_config(), participant, participant,
                             output_dir=tmp_path / "pending-window")
        assert not any(event.is_set() for event in window_calls_returned.values())
    finally:
        release_window.set()
    assert all(event.wait(timeout=0.3) for event in window_calls_returned.values())
    assert result["counter_state"] == []
    assert result["rounds"][0]["alice_color"] is None
    assert result["rounds"][0]["bob_color"] is None
    assert result["rounds"][0]["clock"]["close_reason"] == "deadline"
    assert result["rounds"][0]["clock"]["closed_monotonic"] >= result["rounds"][0]["clock"]["deadline_monotonic"]
    assert all(len(requests) == 1 and requests[0]["phase"] == "play" for requests in calls.values())
    assert "-late/up" not in json.dumps(result["agents"])
    # The completed main transcript must not later acquire a late counter write.
    assert load_rollout(tmp_path / "pending-window")["counter_state"] == []


@pytest.mark.parametrize("failed_roles", [("alice", "bob"), ("alice",)])
def test_terminal_api_errors_close_without_waiting_for_the_round_deadline(tmp_path, failed_roles):
    calls = []

    class BadRequest(RuntimeError):
        status_code = 400

    def participant(request):
        calls.append((request["role"], request["action_index"]))
        if request["role"] in failed_roles:
            raise BadRequest("offline terminal provider error")
        return choose()

    result = run_rollout(realtime_config(round_time_limit_s=1), participant, participant,
                         output_dir=tmp_path / "terminal-errors")
    rnd = result["rounds"][0]
    assert rnd["clock"]["close_reason"] == "no_actions_available"
    assert rnd["clock"]["closed_monotonic"] < rnd["clock"]["deadline_monotonic"]
    assert sorted(calls) == [("alice", 0), ("bob", 0)]
    assert result["summary"]["total_actions"] == 2
    assert result["summary"]["infrastructure_errors"] == len(failed_roles)
    assert result["status"] == "complete_with_errors"
    assert rnd["valid_for_analysis"] is False and rnd["match"] is False
    for role in ("alice", "bob"):
        assert rnd[f"{role}_color"] == (None if role in failed_roles else "red")
    assert all(error["category"] == "model_api" and error["status_code"] == 400 for error in rnd["errors"])


def test_terminal_peer_error_does_not_cancel_a_pending_valid_choice(tmp_path):
    alice_failed = threading.Event()
    bob_returned = threading.Event()
    requests = []

    class BadRequest(RuntimeError):
        status_code = 400

    def on_event(event):
        if event["kind"] == "model_error" and event["role"] == "alice":
            alice_failed.set()

    def alice(request):
        raise BadRequest("offline terminal provider error")

    def bob(request):
        requests.append(copy.deepcopy(request))
        assert alice_failed.wait(timeout=0.5)
        # Keep Bob's call pending while the controller handles Alice's error.
        threading.Event().wait(timeout=0.04)
        bob_returned.set()
        return choose()

    result = run_rollout(realtime_config(round_time_limit_s=0.5), alice, bob,
                         output_dir=tmp_path / "pending-after-peer-error", on_event=on_event)
    assert bob_returned.is_set()
    assert len(requests) == 1
    rnd = result["rounds"][0]
    assert rnd["alice_color"] is None and rnd["bob_color"] == "red"
    assert rnd["clock"]["close_reason"] == "no_actions_available"
    assert rnd["clock"]["closed_monotonic"] < rnd["clock"]["deadline_monotonic"]
    assert result["summary"]["infrastructure_errors"] == 1
    assert "offline terminal provider error" not in json.dumps(requests)


def test_live_get_results_enter_only_the_callers_final_history(tmp_path):
    finals = {}

    def alice(request):
        if final_request(request):
            finals["alice"] = copy.deepcopy(request)
            return choose()
        return {"action": "get", "url": f"{URL}-alice-only/up"}

    def bob(request):
        if final_request(request):
            finals["bob"] = copy.deepcopy(request)
            return choose()
        return {"action": "wait"}

    result = run_rollout(realtime_config(actions_per_agent=2), alice, bob,
                         output_dir=tmp_path / "private-results")
    assert result["counter_state"] == [{"namespace": "realtime-test", "key": "signal-alice-only", "count": 1}]
    assert "signal-alice-only" in json.dumps(finals["alice"]["messages"])
    assert "signal-alice-only" not in json.dumps(finals["bob"]["messages"])
    for request in finals.values():
        assert not ({"peer_actions", "peer_actions_used", "alice_color", "bob_color", "counter_state"} & request.keys())


def test_a_final_response_after_its_timeout_remains_a_missing_choice(tmp_path):
    alice_returned = threading.Event()

    def alice(request):
        if final_request(request):
            time.sleep(0.2)
            alice_returned.set()
            return {"text": json.dumps(choose()), "usage": {
                "prompt_tokens": 17, "completion_tokens": 3, "total_tokens": 20, "cost": 0.125,
            }}
        return {"action": "wait"}

    def bob(request):
        return choose() if final_request(request) else {"action": "wait"}

    result = run_rollout(realtime_config(actions_per_agent=1, round_time_limit_s=0.04), alice, bob,
                         output_dir=tmp_path / "late-final")
    assert not alice_returned.is_set(), "The round deadline must not wait for the late call"
    assert result["rounds"][0]["alice_color"] is None
    assert result["rounds"][0]["bob_color"] == "red"
    assert result["rounds"][0]["match"] is False
    assert alice_returned.wait(timeout=0.3)
    alice_record = next(action for action in result["rounds"][0]["actions"]
                        if action["role"] == "alice" and action["phase"] == "final")
    response_path = Path(alice_record["response_path"])
    until = time.monotonic() + 0.3
    while not response_path.exists() and time.monotonic() < until:
        time.sleep(0.005)
    assert response_path.exists(), "Late paid responses must have a durable artifact"
    assert alice_record["response"] is None, "Worker threads must not mutate a completed result"
    reloaded = load_rollout(tmp_path / "late-final")
    assert reloaded["rounds"][0]["alice_color"] is None
    reloaded_record = next(action for action in reloaded["rounds"][0]["actions"]
                           if action["role"] == "alice" and action["phase"] == "final")
    assert reloaded_record["response"] is not None
    assert reloaded["summary"]["cost"] == 0.125
    assert reloaded["summary"]["usage"]["total_tokens"] == 20
    assert reloaded["summary"]["pending_responses"] == 0


def test_late_previous_round_action_cannot_mutate_the_next_round(tmp_path):
    next_round_started = threading.Event()
    first_alice_returned = threading.Event()

    def participant(request):
        if final_request(request):
            return choose(("red", "green")[request["round_index"]])
        if request["round_index"] == 0 and request["role"] == "alice":
            next_round_started.wait(timeout=0.5)
            first_alice_returned.set()
            return {"action": "get", "url": f"{URL}-old-round/up"}
        if request["round_index"] == 1:
            next_round_started.set()
            if request["role"] == "alice":
                return {"action": "get", "url": f"{URL}-current-round/up"}
        return {"action": "wait"}

    result = run_rollout(realtime_config(rounds=2, actions_per_agent=2,
                                        round_time_limit_s=0.05), participant, participant,
                         output_dir=tmp_path / "round-generation")
    assert next_round_started.is_set() and first_alice_returned.wait(timeout=0.3)
    assert result["counter_state"] == [
        {"namespace": "realtime-test", "key": "signal-current-round", "count": 1}
    ]
    assert result["rounds"][0]["alice_color"] is None
    assert result["rounds"][1]["match"] is True


def test_total_limit_reserves_final_choices_in_later_rounds(tmp_path):
    final_rounds = {role: [] for role in ("alice", "bob")}

    def participant(request):
        if final_request(request):
            final_rounds[request["role"]].append(request["round_index"])
            return choose(("red", "green")[request["round_index"]])
        return {"action": "wait"}

    result = run_rollout(realtime_config(rounds=2, total_action_limit=6,
                                        round_time_limit_s=0.04), participant, participant,
                         output_dir=tmp_path / "reserve-later-rounds")
    assert final_rounds == {"alice": [0, 1], "bob": [0, 1]}
    assert result["summary"]["total_actions"] <= 6
    assert all(round_result["match"] for round_result in result["rounds"])


def test_final_phase_rejects_counter_actions_without_an_extra_attempt(tmp_path):
    requests = []

    def participant(request):
        requests.append(copy.deepcopy(request))
        return {"action": "get", "url": f"{URL}/up"}

    result = run_rollout(realtime_config(actions_per_agent=1, round_time_limit_s=0.03),
                         participant, participant, output_dir=tmp_path / "final-get")
    assert len(requests) == result["summary"]["total_actions"] == 2
    assert all(final_request(request) for request in requests)
    assert result["counter_state"] == []
    assert result["rounds"][0]["alice_color"] is None
    assert result["rounds"][0]["bob_color"] is None
    assert len(result["rounds"][0]["errors"]) == 2


def test_repeated_assigned_color_is_valid_under_the_shared_clock(tmp_path):
    cfg = realtime_config(rounds=2, actions_per_agent=1, round_time_limit_s=0.03, seed=9)
    assert make_plan(cfg)["assigned_colors"] == ["red", "red"]
    result = run_rollout(cfg,
                         lambda request: choose(), lambda request: choose(),
                         output_dir=tmp_path / "repeated-assigned-final")
    first, second = result["rounds"]
    assert first["alice_color"] == first["bob_color"] == "red"
    assert second["alice_color"] == "red"
    assert second["bob_color"] == "red"
    assert second["match"] is True
    assert result["summary"]["total_actions"] == 4


def test_get_rejected_during_parsing_does_not_enter_history_or_trigger_a_late_call(tmp_path, monkeypatch):
    """Crossing the cutoff after parsing starts must not partly admit an action."""
    import experiments.color_game.game as game_module

    original_parse = game_module._parse_action
    shared_deadline = []
    calls = []
    late_url = f"{URL}-parse-crossed-deadline/up"

    def parse_slowly(response, available_actions):
        if late_url in response.get("text", ""):
            time.sleep(max(0.0, shared_deadline[0] - time.monotonic()) + 0.005)
        return original_parse(response, available_actions)

    monkeypatch.setattr(game_module, "_parse_action", parse_slowly)

    def participant(request):
        calls.append((time.monotonic(), copy.deepcopy(request)))
        if final_request(request):
            return choose()
        if request["role"] == "alice":
            shared_deadline.append(request["request_deadline_monotonic"])
            return {"action": "get", "url": late_url}
        return {"action": "wait"}

    result = run_rollout(realtime_config(actions_per_agent=2, round_time_limit_s=0.04),
                         participant, participant, output_dir=tmp_path / "parse-at-deadline")
    assert result["counter_state"] == []
    assert late_url not in json.dumps(result["agents"]["alice"]["messages"])
    assert all(at < request["request_deadline_monotonic"] for at, request in calls)


def test_timely_choice_scores_even_when_its_response_file_is_saved_after_deadline(tmp_path, monkeypatch):
    import experiments.color_game.game as game_module

    original_save = game_module._save_json
    alice_file_saved = threading.Event()

    def delayed_save(path, payload):
        if Path(path).parent.name == "responses" and payload["role"] == "alice":
            time.sleep(0.15)
            original_save(path, payload)
            alice_file_saved.set()
            return
        original_save(path, payload)

    monkeypatch.setattr(game_module, "_save_json", delayed_save)
    result = run_rollout(realtime_config(actions_per_agent=1, round_time_limit_s=0.04),
                         lambda request: choose(), lambda request: choose(),
                         output_dir=tmp_path / "slow-response-file")
    assert result["rounds"][0]["match"] is True
    assert not alice_file_saved.is_set()
    alice_action = next(action for action in result["rounds"][0]["actions"] if action["role"] == "alice")
    assert alice_action["response_received_monotonic"] < alice_action["request_deadline_monotonic"]
    assert alice_action["accepted"] is True
    assert alice_file_saved.wait(timeout=0.4)
    reloaded = load_rollout(tmp_path / "slow-response-file")
    assert reloaded["rounds"][0]["match"] is True
    reloaded_alice = next(action for action in reloaded["rounds"][0]["actions"] if action["role"] == "alice")
    assert reloaded_alice["response_file_status"] == "saved"


def test_timely_choice_is_accepted_during_close_after_slow_parsing(tmp_path, monkeypatch):
    import experiments.color_game.game as game_module

    original_parse = game_module._parse_action
    deadline = []

    def delayed_parse(response, allowed):
        if response.get("slow_parse"):
            time.sleep(max(0.0, deadline[0] - time.monotonic()) + 0.005)
        return original_parse(response, allowed)

    monkeypatch.setattr(game_module, "_parse_action", delayed_parse)

    def alice(request):
        deadline.append(request["request_deadline_monotonic"])
        return {"text": json.dumps(choose()), "slow_parse": True}

    result = run_rollout(realtime_config(actions_per_agent=1, round_time_limit_s=0.04),
                         alice, lambda request: choose(), output_dir=tmp_path / "timely-drain")
    assert result["rounds"][0]["match"] is True
    assert all(action["response_received_monotonic"] < deadline[0]
               for action in result["rounds"][0]["actions"])
    assert result["rounds"][0]["clock"]["closed_monotonic"] >= deadline[0]


def test_request_journaling_past_deadline_does_not_start_a_callable(tmp_path):
    called = []
    delayed = False

    def on_event(event):
        nonlocal delayed
        if event["kind"] == "request" and not delayed:
            delayed = True
            time.sleep(max(0.0, event["request_deadline_monotonic"] - time.monotonic()) + 0.005)

    def participant(request):
        called.append(request)
        return choose()

    result = run_rollout(realtime_config(actions_per_agent=1, round_time_limit_s=0.03),
                         participant, participant, output_dir=tmp_path / "admission-deadline",
                         on_event=on_event)
    assert delayed and called == []
    assert result["rounds"][0]["alice_color"] is None
    assert result["rounds"][0]["bob_color"] is None
    assert result["summary"]["total_actions"] == 1


def test_response_file_failure_retains_a_fallback_and_reports_the_error(tmp_path, monkeypatch):
    import experiments.color_game.game as game_module

    original_save = game_module._save_json
    fallback_saved = threading.Event()

    def fail_primary(path, payload):
        if Path(path).parent.name == "responses" and payload["role"] == "alice":
            raise OSError("test primary-response-file failure")
        original_save(path, payload)
        if Path(path).parent.name == "response-failures":
            fallback_saved.set()

    monkeypatch.setattr(game_module, "_save_json", fail_primary)
    result = run_rollout(realtime_config(actions_per_agent=1), lambda request: choose(),
                         lambda request: choose(), output_dir=tmp_path / "fallback-response")
    assert result["rounds"][0]["match"] is True
    assert fallback_saved.wait(timeout=0.3)
    reloaded = load_rollout(tmp_path / "fallback-response")
    alice_action = next(action for action in reloaded["rounds"][0]["actions"] if action["role"] == "alice")
    assert alice_action["response_file_status"] == "fallback"
    assert alice_action["response_file_error"] == "OSError"
    assert Path(alice_action["response_fallback_path"]).is_file()
    assert not Path(alice_action["response_path"]).exists()
    assert alice_action["response"]["action"] == choose()
