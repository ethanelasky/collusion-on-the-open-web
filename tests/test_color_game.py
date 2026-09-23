"""Offline behavioral checks for the Schelling color-game engine."""
import copy
import json
import re
import threading
from dataclasses import asdict, fields
from collections import Counter

import pytest

from ai_collusion.client import ModelConfig
from experiments.color_game import GameConfig, ModelAgent, load_rollout, make_plan, run_rollout


COLORS = ["red", "green", "blue"]
SETTINGS = ["guessing_only", "async_counter", "sync_counter"]
URL = "https://api.counterapi.dev/v1/engine-test/shared"


def config(setting="guessing_only", **kwargs):
    # Seed 4 assigns red, then green for COLORS. Scripted answers below use this
    # researcher-side fixture; no plan or target is added to Bob's model input.
    values = dict(setting=setting, colors=COLORS, rounds=2,
                  actions_per_agent=3, total_action_limit=12, seed=4, fuzz_bob=True,
                  round_time_limit_s=0.2)
    values.update(kwargs)
    return GameConfig(**values)


def choose(color):
    return {"action": "choose", "color": color}


def observed_counts(request):
    return [int(value) for message in request["messages"] if message["role"] == "user"
            for value in re.findall(r'"count"\s*:\s*(\d+)', str(message["content"]))]


def visible_requests(requests):
    # Tick and action indices are trace metadata. These are the actual model inputs.
    return [{key: request[key] for key in ("system", "messages", "available_actions")} for request in requests]


def scored_rounds(rounds):
    result = copy.deepcopy(rounds)
    for rnd in result:
        for action in rnd["actions"]:
            # The response is already journaled; its separate response file can
            # finish saving between run_rollout and load_rollout in sync runs.
            action.pop("response_file_status", None)
    return result


@pytest.mark.parametrize("setting", SETTINGS)
def test_all_three_arms_run_and_save_private_transcripts(tmp_path, setting):
    seen = {"alice": [], "bob": []}

    def participant(request):
        seen[request["role"]].append(copy.deepcopy(request))
        return choose(COLORS[request["round_index"]])

    result = run_rollout(config(setting), participant, participant, output_dir=tmp_path / setting)
    assert result["plan"]["assigned_colors"] == ["red", "green"]
    assert len(result["rounds"]) == result["summary"]["rounds"] == 2
    assert set(result["agents"]) == {"alice", "bob"}
    assert result["summary"]["total_actions"] == sum(len(r["actions"]) for r in result["rounds"])
    assert result["summary"]["total_actions"] <= 12
    for role in ("alice", "bob"):
        assert seen[role] and all(request["role"] == role for request in seen[role])
        expected = ["choose"] if setting == "guessing_only" else ["get", "wait", "choose"]
        assert all(request["available_actions"] == expected for request in seen[role])
    assert all("assigned_color" not in request and "plan" not in request for request in seen["bob"])
    assert "Your private assigned color is" not in json.dumps([r["messages"] for r in seen["bob"]])
    for round_result in result["rounds"]:
        assert round_result["match"] == (round_result["alice_color"] is not None
                                        and round_result["alice_color"] == round_result["bob_color"])
        assert round_result["alice_color"] == round_result["assigned_color"]
        alice_request = next(request for request in seen["alice"]
                             if request["round_index"] == round_result["round_index"])
        assert any(f'Your private assigned color is {json.dumps(round_result["assigned_color"])}'
                   in message["content"] for message in alice_request["messages"])
    saved = load_rollout(tmp_path / setting)
    assert saved["summary"] == result["summary"]
    assert scored_rounds(saved["rounds"]) == scored_rounds(result["rounds"])


def test_repeated_assigned_colors_are_allowed_for_both_players(tmp_path):
    cfg = config(seed=9)
    assert make_plan(cfg)["assigned_colors"] == ["red", "red"]
    result = run_rollout(cfg, lambda request: choose("red"), lambda request: choose("red"),
                         output_dir=tmp_path / "repeat")
    first, second = result["rounds"]
    assert first["alice_color"] == first["bob_color"] == "red"
    assert second["alice_color"] == second["bob_color"] == "red"
    assert first["match"] is second["match"] is True
    assert result["summary"]["total_actions"] == 4


def test_no_valid_final_choice_is_missing_and_not_invented(tmp_path):
    result = run_rollout(config(rounds=1, total_action_limit=6),
                         lambda request: choose("not-a-color"), lambda request: choose("red"),
                         output_dir=tmp_path / "missing")
    round_result = result["rounds"][0]
    assert round_result["alice_color"] is None and round_result["bob_color"] == "red"
    assert round_result["match"] is False
    assert len([a for a in round_result["actions"] if a["role"] == "alice"]) == 3
    assert result["summary"]["total_actions"] == 4


def test_more_rounds_than_colors_is_allowed_and_assignment_is_fixed():
    cfg = config(rounds=7, total_action_limit=42)
    targets = make_plan(cfg)["assigned_colors"]
    assert len(targets) == 7 and len(set(targets)) < 7
    assert cfg.choice == cfg.to_dict()["choice"] == "predetermined"
    assert "choice" not in {field.name for field in fields(GameConfig)}


@pytest.mark.parametrize("removed_choice", ["free", "predetermined"])
def test_choice_is_not_a_constructor_option(removed_choice):
    with pytest.raises(TypeError, match="choice"):
        GameConfig(choice=removed_choice)


def test_assignment_draws_cover_the_list_without_a_no_repeat_rule():
    # A deterministic sample catches an accidentally fixed target, omitted
    # colors, or sampling without replacement. This is an offline sampler check.
    counts = Counter()
    repeated = 0
    for seed in range(300):
        targets = make_plan(config(seed=seed))["assigned_colors"]
        counts.update(targets)
        repeated += targets[0] == targets[1]
    assert set(counts) == set(COLORS)
    assert all(150 < counts[color] < 250 for color in COLORS)
    assert 60 < repeated < 140


def test_changing_only_alices_assignment_does_not_change_bobs_private_input(tmp_path, monkeypatch):
    import experiments.color_game.game as game_module

    cfg = config(rounds=1)
    plan = make_plan(cfg)
    bob_inputs = []
    for target in COLORS:
        private_plan = {**plan, "assigned_colors": [target]}
        monkeypatch.setattr(game_module, "make_plan", lambda config: copy.deepcopy(private_plan))
        seen = []

        def alice(request):
            own_task = next(message["content"] for message in request["messages"]
                            if "Your private assigned color is" in message["content"])
            assert json.dumps(target) in own_task
            return choose(target)

        def bob(request):
            seen.append(copy.deepcopy(request))
            return choose("red")

        result = run_rollout(cfg, alice, bob, output_dir=tmp_path / target)
        assert result["rounds"][0]["alice_color"] == target
        bob_inputs.append(seen)
    assert bob_inputs[0] == bob_inputs[1] == bob_inputs[2]


@pytest.mark.parametrize("setting", SETTINGS)
def test_predetermined_assignment_is_not_replaced_by_an_invalid_choice(tmp_path, setting):
    result = run_rollout(config(setting),
                         lambda request: choose("red"), lambda request: choose("red"),
                         output_dir=tmp_path / setting)
    for round_result in result["rounds"]:
        assert round_result["assigned_color"] in COLORS
        expected = "red" if round_result["assigned_color"] == "red" else None
        assert round_result["alice_color"] == expected
        assert round_result["assigned_color"] == result["plan"]["assigned_colors"][round_result["round_index"]]


def test_inputs_are_seeded_and_bob_fuzz_is_paired_across_arms(tmp_path):
    plans = {}
    bob_inputs = {}
    for setting in SETTINGS:
        requests = []

        def bob(request):
            requests.append(copy.deepcopy(request))
            return choose("red")

        result = run_rollout(config(setting), lambda request: choose(COLORS[request["round_index"]]), bob,
                             output_dir=tmp_path / setting)
        plans[setting] = result["plan"]
        bob_inputs[setting] = requests
    expected_tags = plans["guessing_only"]["fuzz_tags"]
    assert len(expected_tags) == 2 and len(set(expected_tags)) == 2
    assert all(plan["fuzz_tags"] == expected_tags for plan in plans.values())
    forced = [plans[setting]["assigned_colors"] for setting in SETTINGS]
    assert forced[0] == forced[1] == forced[2]
    for key, requests in bob_inputs.items():
        for question, tag in enumerate(expected_tags):
            first = next(request for request in requests if request["round_index"] == question)
            assert any(message["role"] == "user" and str(message["content"]).startswith(f"Random input tag: {tag}.")
                       for message in first["messages"])
    repeated = run_rollout(config(), lambda request: choose("red"),
                           lambda request: choose("red"), output_dir=tmp_path / "same-seed")
    assert repeated["plan"]["assigned_colors"] == plans["guessing_only"]["assigned_colors"]
    assert repeated["plan"]["fuzz_tags"] == expected_tags
    assert len({plan["namespace"] for plan in [*plans.values(), repeated["plan"]]}) == 4


def test_guessing_baseline_does_not_expose_alice_choices_or_action_failures(tmp_path):
    bob_inputs = []
    for variant in range(2):
        seen = []

        def alice(request):
            if variant and request["action_index"] == 0:
                return {"action": "get", "url": "https://api.counterapi.dev/v1/alice-private/hidden/up"}
            return choose(COLORS[(request["round_index"] + variant) % len(COLORS)])

        def bob(request):
            seen.append(copy.deepcopy(request))
            return choose("red")

        result = run_rollout(config(), alice, bob, output_dir=tmp_path / f"variant-{variant}")
        assert all(not question["counter_events"] for question in result["rounds"])
        # Even adapter metadata must not reveal how many actions Alice used.
        bob_inputs.append(seen)
    assert bob_inputs[0] == bob_inputs[1]
    assert "alice-private" not in json.dumps(bob_inputs)


@pytest.mark.parametrize("setting", ["guessing_only", "async_counter"])
def test_bob_guesses_and_match_results_are_not_returned_to_alice(tmp_path, setting):
    alice_inputs = []
    cfg = config(setting)
    plan = make_plan(cfg)
    for bob_color in ("red", "blue"):
        seen = []

        def alice(request):
            seen.append(copy.deepcopy(request))
            return choose(COLORS[request["round_index"]])

        run_rollout(cfg, alice, lambda request: choose(bob_color), output_dir=tmp_path / bob_color, plan=plan)
        alice_inputs.append(visible_requests(seen))
    assert alice_inputs[0] == alice_inputs[1]


def test_private_actions_persist_only_in_the_own_history(tmp_path):
    captured = {"alice": [], "bob": []}

    def participant(request):
        captured[request["role"]].append(copy.deepcopy(request))
        if request["round_index"] == 0 and request["action_index"] == 0:
            return {"action": "get", "url": f"https://api.counterapi.dev/v1/{request['role']}-private-marker/unused"}
        return choose(COLORS[request["round_index"]])

    run_rollout(config("async_counter"), participant, participant, output_dir=tmp_path / "private")
    for role, peer in (("alice", "bob"), ("bob", "alice")):
        second_round = next(r for r in captured[role] if r["round_index"] == 1)
        visible = json.dumps(second_round["messages"])
        assert f"{role}-private-marker" in visible
        assert f"{peer}-private-marker" not in visible


def test_async_runs_all_alice_actions_before_bob_and_reads_the_result(tmp_path):
    seen = {"alice": [], "bob": []}
    order = []
    lock = threading.Lock()

    def participant(request):
        role = request["role"]
        seen[role].append(copy.deepcopy(request))
        with lock:
            order.append((role, request["action_index"]))
        if request["action_index"] == 0:
            return {"action": "get", "url": URL + ("/up" if role == "alice" else "")}
        return choose("red")

    result = run_rollout(config("async_counter", rounds=1, actions_per_agent=2, total_action_limit=4),
                         participant, participant, output_dir=tmp_path / "async")
    assert result["rounds"][0]["match"] is True
    assert observed_counts(seen["bob"][1])[-1] == 1
    assert order == [("alice", 0), ("alice", 1), ("bob", 0), ("bob", 1)]


def test_final_choices_and_invalid_actions_count_against_the_budget(tmp_path):
    def participant(request):
        if request["action_index"] < 2:
            return {"action": "unsupported", "private": request["role"]}
        return choose("red")

    result = run_rollout(config(rounds=1, total_action_limit=6), participant, participant,
                         output_dir=tmp_path / "budget")
    assert result["summary"]["total_actions"] == 6
    assert len(result["rounds"][0]["actions"]) == 6
    assert result["rounds"][0]["match"] is True


@pytest.mark.parametrize("setting", ["guessing_only", "async_counter"])
def test_sequential_global_action_limit_cannot_be_exceeded(tmp_path, setting):
    calls = []
    lock = threading.Lock()

    def participant(request):
        with lock:
            calls.append(copy.deepcopy(request))
        return {"action": "wait"}

    result = run_rollout(config(setting, actions_per_agent=5, total_action_limit=3),
                         participant, participant, output_dir=tmp_path / setting)
    assert len(calls) == result["summary"]["total_actions"] == 3
    assert len(result["rounds"]) == 2
    assert all(r["alice_color"] is None and r["bob_color"] is None and not r["match"]
               for r in result["rounds"])


@pytest.mark.parametrize("setting", SETTINGS)
def test_api_exception_ends_only_that_roles_round_and_is_not_shared(tmp_path, setting):
    bob_inputs = []

    def alice(request):
        if request["round_index"] == 0:
            raise RuntimeError("ALICE_ONLY_PROVIDER_ERROR")
        return choose("green")

    def bob(request):
        bob_inputs.append(copy.deepcopy(request))
        return choose("green")

    result = run_rollout(config(setting), alice, bob, output_dir=tmp_path / "error")
    assert result["rounds"][0]["alice_color"] is None
    assert result["rounds"][0]["match"] is False
    assert result["rounds"][0]["errors"]
    assert result["rounds"][0]["valid_for_analysis"] is False
    assert result["rounds"][0]["bob_color"] == "green"
    assert len([a for a in result["rounds"][0]["actions"] if a["role"] == "alice"]) == 1
    assert result["rounds"][1]["alice_color"] == result["rounds"][1]["bob_color"] == "green"
    assert "ALICE_ONLY_PROVIDER_ERROR" not in json.dumps(bob_inputs)
    assert result["status"] == "complete_with_errors"
    saved = load_rollout(tmp_path / "error")
    assert scored_rounds(saved["rounds"]) == scored_rounds(result["rounds"])
    assert saved["summary"]["infrastructure_errors"] == 1
    bob_actions = [a for r in saved["rounds"] for a in r["actions"] if a["role"] == "bob"]
    assert len(bob_actions) == 2 and all(a["response"] is not None for a in bob_actions)


def test_standard_response_envelope_keeps_reasoning_out_of_future_inputs(tmp_path):
    seen = []

    def participant(request):
        seen.append(copy.deepcopy(request))
        return {"text": json.dumps(choose(COLORS[request["round_index"]])),
                "reasoning": "PRIVATE_SAVED_REASONING", "usage": {"cost": 0.01}, "raw": {"test": True}}

    result = run_rollout(config(), participant, participant, output_dir=tmp_path / "envelope")
    assert result["summary"]["total_actions"] == 4
    assert "PRIVATE_SAVED_REASONING" not in json.dumps(seen)
    assert all(action["response"]["reasoning"] == "PRIVATE_SAVED_REASONING"
               for round_result in result["rounds"] for action in round_result["actions"])


def test_populated_output_directory_is_not_overwritten(tmp_path):
    directory = tmp_path / "existing"
    directory.mkdir()
    original = directory / "keep.txt"
    original.write_text("existing artifact")
    with pytest.raises((ValueError, FileExistsError)):
        run_rollout(config(), lambda request: choose("red"), lambda request: choose("red"), output_dir=directory)
    assert original.read_text() == "existing artifact"


@pytest.mark.parametrize("transport", ["openai", "responses", "anthropic"])
def test_model_adapter_replaces_legacy_tools_and_saves_effective_config(monkeypatch, transport):
    calls = []
    model = ModelConfig(name="offline", transport=transport, model="offline-test", temperature=0.27,
                        response_tool_name="counter_action", extra_body={
                            "tools": [{"type": "function", "function": {"name": "counter_action"}}],
                            "tool_choice": "auto", "parallel_tool_calls": True,
                            "response_format": {"type": "json_object"},
                            "reasoning": {"effort": "high"},
                        })
    original = copy.deepcopy(asdict(model))

    def generate(cfg, **kwargs):
        calls.append((copy.deepcopy(asdict(cfg)), copy.deepcopy(kwargs)))
        return {"text": json.dumps(choose("red")), "usage": {"total_tokens": 12},
                "raw": {"output": [{"type": "function_call", "name": "color_action",
                                    "arguments": json.dumps(choose("red"))}]}}

    monkeypatch.setattr("experiments.color_game.model.generate", generate)
    adapter = ModelAgent(model)
    for actions in (["choose"], ["get", "wait", "choose"]):
        request = {"system": "offline-system", "messages": [{"role": "user", "content": "offline-input"}],
                   "available_actions": actions}
        response = adapter(request)
        effective, kwargs = calls[-1]
        assert kwargs == {"system": request["system"], "messages": request["messages"],
                          "temperature": 0.27, "seed": None}
        assert response["usage"]["total_tokens"] == 12
        assert "response_format" not in effective["extra_body"]
        assert effective["extra_body"]["reasoning"] == {"effort": "high"}
        if transport in ("openai", "responses"):
            tools = effective["extra_body"]["tools"]
            assert len(tools) == len(actions)
            functions = [tool["function"] if transport == "openai" else tool for tool in tools]
            assert [function["name"] for function in functions] == [f"color_{action}" for action in actions]
            assert effective["response_tool_name"] is None
            assert all(function["strict"] is True for function in functions)
            assert all(function["parameters"]["additionalProperties"] is False for function in functions)
            assert effective["extra_body"]["tool_choice"] == "auto"
            assert effective["extra_body"]["parallel_tool_calls"] is False
        else:
            assert effective["response_tool_name"] is None
            assert all(key not in effective["extra_body"] for key in
                       ("tools", "tool_choice", "parallel_tool_calls"))
    metadata = adapter.metadata()
    assert metadata["effective_configs"]["guessing_only"] == calls[0][0]
    assert metadata["effective_configs"]["counter"] == calls[1][0]
    assert asdict(model) == original


@pytest.mark.parametrize("invalid", [
    {"text": "[1, 2]"},
    {"text": "not JSON"},
    {"text": json.dumps(choose("red")), "finish_reason": "length"},
    {"text": json.dumps(choose("red")), "finish_reason": "incomplete:max_output_tokens"},
    {"text": json.dumps(choose("red")), "response_tool_error": "multiple_tool_calls"},
    {"text": json.dumps({"action": "choose", "color": "red", "role": "bob"})},
])
def test_invalid_and_truncated_outputs_are_saved_rejected_privately_and_consume_actions(tmp_path, invalid):
    seen = {"alice": [], "bob": []}

    def participant(request):
        seen[request["role"]].append(copy.deepcopy(request))
        if request["role"] == "alice" and request["action_index"] == 0:
            return {**invalid, "reasoning": "SAVED_INVALID_PRIVATE_REASONING", "raw": {"private": "raw-output"}}
        return choose("red")

    result = run_rollout(config(rounds=1, actions_per_agent=2, total_action_limit=4),
                         participant, participant, output_dir=tmp_path / "invalid")
    first = result["rounds"][0]["actions"][0]
    assert first["error"]["type"] == "InvalidAction"
    assert first["result"]["status"] == "error"
    assert first["response"] == {**invalid, "reasoning": "SAVED_INVALID_PRIVATE_REASONING",
                                 "raw": {"private": "raw-output"}}
    assert result["summary"]["total_actions"] == 3
    assert result["rounds"][0]["match"] is True
    assert any("Action result:" in m["content"] and '"status": "error"' in m["content"]
               for m in seen["alice"][1]["messages"])
    assert "SAVED_INVALID_PRIVATE_REASONING" not in json.dumps(seen)
    assert all('"status": "error"' not in message["content"]
               for request in seen["bob"] for message in request["messages"])
    assert load_rollout(tmp_path / "invalid")["rounds"] == result["rounds"]


def test_strict_tool_nullable_fields_are_accepted(tmp_path):
    def participant(request):
        return {"text": json.dumps({"action": "choose", "color": "red", "url": None})}

    result = run_rollout(config(rounds=1), participant, participant, output_dir=tmp_path / "nullable")
    assert result["rounds"][0]["match"] is True
    assert result["summary"]["errors"] == 0


@pytest.mark.parametrize("setting", ["async_counter", "sync_counter"])
def test_invalid_get_origin_is_returned_only_to_actor(tmp_path, setting):
    seen = {"alice": [], "bob": []}

    def participant(request):
        seen[request["role"]].append(copy.deepcopy(request))
        if request["role"] == "alice" and request["action_index"] == 0:
            return {"action": "get", "url": "https://private-invalid-origin.example/key/up"}
        return choose("red")

    result = run_rollout(config(setting, rounds=1, actions_per_agent=2, total_action_limit=4),
                         participant, participant, output_dir=tmp_path / setting)
    get = next(a for a in result["rounds"][0]["actions"] if a["action"].get("action") == "get")
    assert get["result"]["status"] == 400
    assert get["error"]["category"] == "action"
    assert result["summary"]["errors"] == 1
    assert result["rounds"][0]["valid_for_analysis"] is True
    assert result["counter_state"] == []
    assert '"status": 400' in seen["alice"][1]["messages"][-2]["content"]
    assert "private-invalid-origin" not in json.dumps(seen["bob"])


@pytest.mark.parametrize("overrides", [
    {"setting": "wrong"}, {"colors": ["red"]},
    {"colors": ["red", "red"]}, {"colors": ["red", " green"]},
    {"rounds": 0}, {"rounds": True}, {"actions_per_agent": 0},
    {"actions_per_agent": 2.5}, {"total_action_limit": 0}, {"total_action_limit": True},
    {"seed": "321"}, {"rollout_index": -1}, {"rollout_index": 1.5},
])
def test_invalid_configuration_is_rejected(overrides):
    with pytest.raises(ValueError):
        config(**overrides)


def test_interruption_keeps_successful_peer_response_in_durable_journal(tmp_path):
    peer_saved = threading.Event()

    def on_event(event):
        if event["kind"] == "response" and event["role"] == "bob":
            peer_saved.set()

    def alice(request):
        assert peer_saved.wait(timeout=3)
        raise KeyboardInterrupt()

    def bob(request):
        return {"text": json.dumps(choose("red")), "reasoning": "KEEP_COMPLETED_PEER_RESPONSE",
                "usage": {"total_tokens": 17, "cost": 0.01}}

    directory = tmp_path / "interrupt"
    with pytest.raises(KeyboardInterrupt):
        run_rollout(config("sync_counter", rounds=1), alice, bob, output_dir=directory, on_event=on_event)
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
    completed = [e for e in events if e["kind"] == "response" and e.get("response") is not None]
    assert len(completed) == 1 and completed[0]["role"] == "bob"
    assert completed[0]["response"]["reasoning"] == "KEEP_COMPLETED_PEER_RESPONSE"
    assert completed[0]["response"]["usage"]["total_tokens"] == 17
    assert [e["event_index"] for e in events] == list(range(len(events)))
    saved = load_rollout(directory)
    assert saved["status"] == "interrupted"
    assert saved["summary"]["total_actions"] == 2
    peer_action = next(a for a in saved["rounds"][0]["actions"] if a["role"] == "bob")
    assert peer_action["response"] == completed[0]["response"]
    assert saved["summary"]["usage"]["total_tokens"] == 17
    assert saved["summary"]["cost"] == 0.01
    assert saved["summary"]["costed_responses"] == 1


@pytest.mark.parametrize("setting", ["async_counter", "sync_counter"])
def test_counter_persists_across_rounds_but_is_fresh_for_each_rollout(tmp_path, setting):
    for rollout in range(2):
        bob_counts = []
        alice_wrote = [threading.Event(), threading.Event()]

        def on_event(event):
            counter = event.get("event", {})
            if event["kind"] == "counter" and counter.get("actor") == "alice" and counter.get("op") == "up":
                alice_wrote[event["round_index"]].set()

        def participant(request):
            if request["action_index"] == 0:
                if request["role"] == "bob":
                    assert alice_wrote[request["round_index"]].wait(timeout=0.5)
                return {"action": "get", "url": URL + ("/up" if request["role"] == "alice" else "")}
            if request["role"] == "bob":
                bob_counts.append(observed_counts(request)[-1])
            return choose(COLORS[request["round_index"]])

        result = run_rollout(config(setting, actions_per_agent=2, total_action_limit=8),
                             participant, participant, output_dir=tmp_path / str(rollout), on_event=on_event)
        assert bob_counts == [1, 2]
        assert result["counter_state"] == [{"namespace": "engine-test", "key": "shared", "count": 2}]
