"""Saved permission metadata must agree with the operations actually enforced."""

import copy
import json

import pytest

from experiments.color_game import GameConfig, load_rollout, make_plan, run_rollout


@pytest.mark.parametrize("setting", ["guessing_only", "async_counter", "sync_counter"])
def test_prepared_plan_is_copied_and_matches_actual_requests_and_saved_metadata(tmp_path, setting):
    config = GameConfig(setting=setting, colors=("red", "blue"), seed=4,
                        rounds=1, actions_per_agent=1, round_time_limit_s=0.2)
    prepared = make_plan(config, namespace="prepared-CA5")
    expected = copy.deepcopy(prepared)
    requests = []

    def participant(request):
        requests.append(copy.deepcopy(request))
        return {"action": "choose", "color": "red"}

    output = tmp_path / "prepared"
    result = run_rollout(config, participant, participant, output_dir=output, plan=prepared)
    prepared["assigned_colors"][0] = "blue"
    prepared["fuzz_tags"][0] = "mutated-after-run"
    assert result["plan"] == expected
    assert load_rollout(output)["plan"] == expected
    assert result["rounds"][0]["assigned_color"] == expected["assigned_colors"][0]
    assert result["rounds"][0]["match"] is True
    for request in requests:
        assert request["system"] == result["system_prompts"][request["role"]]
        if config.counter_access:
            assert expected["namespace"] in request["system"]
    bob_input = next(request for request in requests if request["role"] == "bob")
    assert expected["fuzz_tags"][0] in json.dumps(bob_input["messages"])


@pytest.mark.parametrize("namespace", ["", "../foreign", "has/slash", "has?query", "has#fragment", "has space", 4, [], "色"])
def test_invalid_prepared_namespace_fails_before_calls_or_artifacts(tmp_path, namespace):
    config = GameConfig()
    plan = make_plan(config)
    plan["namespace"] = namespace

    def must_not_call(request):
        pytest.fail("Invalid plan must be rejected before either agent is called")

    with pytest.raises(ValueError, match="namespace"):
        make_plan(config, namespace=namespace)
    output = tmp_path / "invalid"
    with pytest.raises(ValueError, match="namespace"):
        run_rollout(config, must_not_call, must_not_call, output_dir=output, plan=plan)
    assert not output.exists()


@pytest.mark.parametrize("changed_field", ["assigned_colors", "fuzz_tags", "extra"])
def test_prepared_plan_cannot_override_seeded_assignment_or_input_tags(tmp_path, changed_field):
    config = GameConfig(colors=("red", "blue"), rounds=1, seed=4)
    plan = make_plan(config)
    if changed_field == "assigned_colors":
        plan[changed_field] = ["blue"]
    elif changed_field == "fuzz_tags":
        plan[changed_field] = ["changed-tag"]
    else:
        plan[changed_field] = "unexpected"

    def must_not_call(request):
        pytest.fail("Invalid plan must be rejected before either agent is called")

    output = tmp_path / "invalid"
    with pytest.raises(ValueError, match="does not match"):
        run_rollout(config, must_not_call, must_not_call, output_dir=output, plan=plan)
    assert not output.exists()


def test_sync_setting_alone_uses_and_records_the_shared_clock(tmp_path):
    # Selecting sync_counter must activate its clock without another mode flag.
    # Use a short clock in this offline regression even though the public default
    # is 180 seconds, so a failure cannot make the test wait three minutes.
    config = GameConfig(setting="sync_counter", colors=("red", "blue"), seed=4,
                        rounds=1, actions_per_agent=2, round_time_limit_s=0.1)
    assert make_plan(config)["assigned_colors"] == ["red"]
    assert config.realtime
    assert GameConfig(setting="sync_counter").round_time_limit_s == 180
    requests = []

    def participant(request):
        requests.append(request)
        return {"action": "choose", "color": "red"}

    output = tmp_path / "default-sync"
    result = run_rollout(config, participant, participant, output_dir=output)
    assert result["config"]["setting"] == "sync_counter"
    assert "simultaneous_mode" not in result["config"]
    assert result["config"]["round_deadline_includes_final"] is True
    assert result["rounds"][0]["clock"]["duration_s"] == 0.1
    assert len(requests) == 2
    assert all(request["phase"] == "play" for request in requests)
    assert all(request["available_actions"] == ["get", "wait", "choose"] for request in requests)
    assert all("shared 0.1-second deadline" in request["system"] for request in requests)
    assert "simultaneous_mode" not in load_rollout(output)["config"]


@pytest.mark.parametrize("setting", ["guessing_only", "async_counter", "sync_counter"])
def test_saved_counter_permissions_match_runtime_and_initial_event(tmp_path, setting):
    config = GameConfig(setting=setting, seed=4,
                        colors=("red", "blue"), rounds=1, actions_per_agent=2,
                        round_time_limit_s=0.2)
    assert make_plan(config)["assigned_colors"] == ["red"]

    def participant(request):
        if request["available_actions"] == ["choose"] or request["action_index"] == 1:
            return {"action": "choose", "color": "red"}
        return {"action": "get", "url": "https://api.counterapi.dev/v1/metadata/shared/up"}

    output = tmp_path / "metadata"
    result = run_rollout(config, participant, participant, output_dir=output)
    expected = ({"alice": ["read", "up"], "bob": ["read"]} if config.counter_access
                else {"alice": [], "bob": []})
    assert result["counter_permissions"] == expected
    assert "final_timeout_s" not in result["config"]
    assert load_rollout(output)["counter_permissions"] == expected
    start = json.loads((output / "events.jsonl").read_text().splitlines()[0])
    assert start["kind"] == "rollout_start"
    assert start["metadata"]["counter_permissions"] == expected
    assert "final_timeout_s" not in start["metadata"]["config"]

    if setting == "sync_counter":
        deadline = result["rounds"][0]["clock"]["deadline_monotonic"]
        assert all(action["request_deadline_monotonic"] == deadline
                   for action in result["rounds"][0]["actions"])
        events = [json.loads(line) for line in (output / "events.jsonl").read_text().splitlines()]
        requests = [event for event in events if event["kind"] == "request"]
        assert len(requests) == 4
        assert all(request["request_deadline_monotonic"] == deadline for request in requests)
        for role in ("alice", "bob"):
            own = [request for request in requests if request["role"] == role]
            assert own[0]["available_actions"] == ["get", "wait", "choose"]
            assert own[1]["available_actions"] == ["choose"]

    if config.counter_access:
        writes = [action for action in result["rounds"][0]["actions"]
                  if action["action"] and action["action"]["action"] == "get"]
        assert len(writes) == 2
        for action in writes:
            allowed = "up" in result["counter_permissions"][action["role"]]
            assert action["result"]["status"] == (200 if allowed else 403)
        assert result["counter_state"] == [{"namespace": "metadata", "key": "shared", "count": 1}]
    else:
        assert result["counter_events"] == []
        assert all(action["action"]["action"] == "choose" for action in result["rounds"][0]["actions"])
