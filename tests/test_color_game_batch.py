"""Offline checks for concurrent, isolated notebook batches."""
import json
import re
import threading
from pathlib import Path

import pytest

from ai_collusion.client import ModelConfig
from experiments.color_game import batch
from experiments.color_game.config import GameConfig, SETTINGS

URL = "https://api.counterapi.dev/v1/batch-check/shared"


def base_config(**changes):
    values = dict(rounds=1, colors=("red", "blue"), actions_per_agent=3,
                  round_time_limit_s=0.5, seed=124)
    return GameConfig(**{**values, **changes})


def model_config():
    return ModelConfig(name="offline", model="offline", transport="stub",
                       extra_body={"test_marker": "original"})


class OfflineAgent:
    def __init__(self, config):
        self.config = config

    def __call__(self, request):
        role = request["role"]
        target = "red"
        if role == "alice":
            text = "\n".join(message.get("content", "") for message in request["messages"])
            target = json.loads(re.findall(r'Your private assigned color is ("[^"]+")', text)[-1])
        if "get" not in request["available_actions"] or request["action_index"] >= (2 if role == "alice" else 1):
            return {"action": "choose", "color": target}
        return {"action": "get", "url": URL + ("/up" if role == "alice" and request["action_index"] == 1 else "")}


def test_three_arms_start_together_and_events_run_on_calling_thread(tmp_path, monkeypatch):
    barrier = threading.Barrier(3)
    started = []
    instance_ids = []
    config_ids = []
    original_run = batch.run_rollout
    calling_thread = threading.get_ident()
    callback_threads = []
    events = []

    def factory(config):
        player = OfflineAgent(config)
        instance_ids.append(player)
        config_ids.append(config)
        return player

    def concurrent_run(config, *agents, **kwargs):
        started.append(config.setting)
        barrier.wait(timeout=3)
        return original_run(config, *agents, **kwargs)

    def callback(event):
        callback_threads.append(threading.get_ident())
        events.append(event)

    monkeypatch.setattr(batch, "ModelAgent", factory)
    monkeypatch.setattr(batch, "run_rollout", concurrent_run)
    result = batch.run_all_configs(base_config(), model_config(), output_dir=tmp_path / "batch", on_event=callback)
    assert set(started) == set(SETTINGS)
    assert result["max_parallel_rollouts"] == 3
    assert [item["config"]["setting"] for item in result["outcomes"]] == list(SETTINGS)
    assert len(instance_ids) == len({id(item) for item in instance_ids}) == 6
    assert len(config_ids) == len({id(item) for item in config_ids}) == 6
    assert callback_threads and set(callback_threads) == {calling_thread}
    assert len([event for event in events if event["kind"] == "batch_arm_complete"]) == 3
    assert {event["setting"] for event in events if event["kind"] == "request"} == set(SETTINGS)
    assert result["summary"]["completed_arms"] == 3 and result["summary"]["failed_arms"] == 0


def test_paired_plans_fresh_counter_stores_and_full_saved_trajectories(tmp_path, monkeypatch):
    monkeypatch.setattr(batch, "ModelAgent", OfflineAgent)
    result = batch.run_all_configs(base_config(), model_config(), output_dir=tmp_path / "batch")
    arms = [item["arm"] for item in result["outcomes"]]
    assert list(result["trajectories"]) == arms
    trajectories = list(result["trajectories"].values())
    plans = [trajectory["plan"] for trajectory in trajectories]
    assert len({plan["namespace"] for plan in plans}) == 3
    seeded_plans = [{key: value for key, value in plan.items() if key != "namespace"} for plan in plans]
    assert seeded_plans[0] == seeded_plans[1] == seeded_plans[2]
    assert trajectories[0]["counter_state"] == []
    for trajectory in trajectories[1:]:
        assert trajectory["counter_state"] == [{"namespace": "batch-check", "key": "shared", "count": 1}]
        first_alice_get = next(a for a in trajectory["rounds"][0]["actions"]
                               if a["role"] == "alice" and a["action"]["action"] == "get")
        assert first_alice_get["result"]["body"]["data"]["count"] == 0
    for item in result["outcomes"]:
        assert set(item["artifact_paths"]) == {"json", "jsonl", "source", "html"}
        assert all(Path(path).exists() for path in item["artifact_paths"].values())
        trajectory = result["trajectories"][item["arm"]]
        assert item["namespace"] == trajectory["plan"]["namespace"]
        if item["config"]["setting"] != "guessing_only":
            assert all(item["namespace"] in prompt for prompt in trajectory["system_prompts"].values())
        assert trajectory["agents"]["alice"]["messages"]
        assert trajectory["rounds"][0]["actions"][0]["request_messages"]
    manifest = json.loads(Path(result["artifact_paths"]["manifest"]).read_text())
    assert "trajectories" not in manifest
    assert manifest["outcomes"] == result["outcomes"]
    assert manifest["summary"] == result["summary"]


def test_repeating_batch_seed_keeps_targets_but_allocates_new_namespaces(tmp_path, monkeypatch):
    monkeypatch.setattr(batch, "ModelAgent", OfflineAgent)
    first = batch.run_all_configs(base_config(), model_config(), output_dir=tmp_path / "first")
    second = batch.run_all_configs(base_config(), model_config(), output_dir=tmp_path / "second")
    namespaces = []
    for arm in first["trajectories"]:
        before = first["trajectories"][arm]["plan"]
        after = second["trajectories"][arm]["plan"]
        namespaces.extend([before["namespace"], after["namespace"]])
        assert {key: value for key, value in before.items() if key != "namespace"} == {
            key: value for key, value in after.items() if key != "namespace"}
    assert len(set(namespaces)) == 6


@pytest.mark.parametrize("base_setting", SETTINGS)
def test_setting_only_batch_always_uses_shared_deadline_for_sync(tmp_path, monkeypatch, base_setting):
    monkeypatch.setattr(batch, "ModelAgent", OfflineAgent)
    config = base_config(setting=base_setting)
    assert "simultaneous_mode" not in config.to_dict()
    result = batch.run_all_configs(config, model_config(), output_dir=tmp_path / base_setting)
    assert result["summary"]["completed_arms"] == 3
    assert [item["config"]["setting"] for item in result["outcomes"]] == list(SETTINGS)
    for item in result["outcomes"]:
        assert "simultaneous_mode" not in item["config"]
        trajectory = result["trajectories"][item["arm"]]
        round_record = trajectory["rounds"][0]
        actions = round_record["actions"]
        if item["config"]["setting"] == "sync_counter":
            assert item["config"]["round_deadline_includes_final"] is True
            assert round_record["clock"]["duration_s"] == 0.5
            deadline = round_record["clock"]["deadline_monotonic"]
            assert actions and all(action["phase"] in {"play", "final"} for action in actions)
            assert all(action["request_deadline_monotonic"] == deadline for action in actions)
            assert any(action["action"]["action"] == "choose" for action in actions)
        else:
            assert "clock" not in round_record
            assert all(action.get("phase") is None for action in actions)


def test_prompts_are_prepared_serially_before_models_and_snapshotted(tmp_path, monkeypatch):
    caller = threading.get_ident()
    prepared = []
    additions = {"alice": "Keep Alice's custom instruction.", "bob": "Keep Bob's custom instruction."}
    model = model_config()

    def transform(role, text):
        assert threading.get_ident() == caller
        prepared.append(role)
        return text + f"\nApplied transform for {role}."

    def factory(snapshot):
        assert len(prepared) == 6
        assert snapshot.extra_body["test_marker"] == "original"
        snapshot.extra_body["test_marker"] = "worker mutation"
        additions["alice"] = "Mutation after prompt preparation."
        return OfflineAgent(snapshot)

    monkeypatch.setattr(batch, "ModelAgent", factory)
    result = batch.run_all_configs(base_config(), model, output_dir=tmp_path / "batch",
                                  prompt_additions=additions, prompt_transform=transform)
    assert prepared == ["alice", "bob"] * 3
    assert model.extra_body == {"test_marker": "original"}
    for item in result["outcomes"]:
        prompts = result["trajectories"][item["arm"]]["system_prompts"]
        assert "Keep Alice's custom instruction." in prompts["alice"]
        assert "Mutation after prompt preparation." not in prompts["alice"]
        assert "Applied transform for bob." in prompts["bob"]
        assert ('"action":"get"' in prompts["bob"]) == (item["config"]["setting"] != "guessing_only")


def test_one_failed_arm_preserves_partial_trajectory_and_does_not_cancel_peers(tmp_path, monkeypatch):
    class OfflineFailure(BaseException):
        pass

    class FailingAgent(OfflineAgent):
        def __call__(self, request):
            if "get" in request["available_actions"] and request.get("phase") is None:
                raise OfflineFailure("sensitive exception text must not be saved")
            return super().__call__(request)

    monkeypatch.setattr(batch, "ModelAgent", FailingAgent)
    result = batch.run_all_configs(base_config(), model_config(), output_dir=tmp_path / "batch")
    assert result["status"] == "complete_with_errors"
    assert result["summary"]["completed_arms"] == 2
    assert result["summary"]["failed_arms"] == 1
    failed = next(item for item in result["outcomes"] if item["config"]["setting"] == "async_counter")
    assert failed["error"] == {"type": "OfflineFailure", "category": "rollout"}
    assert result["trajectories"][failed["arm"]]["status"] == "failed"
    assert Path(failed["artifact_paths"]["html"]).is_file()
    assert "sensitive exception text" not in Path(result["artifact_paths"]["manifest"]).read_text()


def test_transport_errors_are_retained_without_fabricated_clean_status(tmp_path, monkeypatch):
    class FailingAgent(OfflineAgent):
        def __call__(self, request):
            if "get" not in request["available_actions"] and request.get("phase") is None:
                raise TimeoutError("private provider error")
            return super().__call__(request)

    monkeypatch.setattr(batch, "ModelAgent", FailingAgent)
    result = batch.run_all_configs(base_config(), model_config(), output_dir=tmp_path / "batch")
    baseline = result["outcomes"][0]
    assert baseline["status"] == "complete_with_errors"
    assert baseline["summary"]["infrastructure_errors"] == 2
    assert baseline["summary"]["missing_choices"] == 2
    assert result["status"] == "complete_with_errors"
    assert result["summary"]["completed_arms"] == 3


def test_bad_prompt_in_one_arm_does_not_launch_that_arm_or_cancel_others(tmp_path, monkeypatch):
    created = []

    def transform(role, text):
        if "This setting has no communication tools." in text:
            raise ValueError("invalid baseline customization")
        return text

    def factory(config):
        created.append(True)
        return OfflineAgent(config)

    monkeypatch.setattr(batch, "ModelAgent", factory)
    result = batch.run_all_configs(base_config(), model_config(), output_dir=tmp_path / "batch",
                                  prompt_transform=transform)
    assert len(created) == 4
    assert result["outcomes"][0]["error"]["category"] == "prompt_preparation"
    assert result["summary"]["failed_arms"] == 1 and len(result["trajectories"]) == 2


def test_nonempty_output_is_rejected_before_starting_models(tmp_path, monkeypatch):
    target = tmp_path / "batch"
    target.mkdir()
    (target / "existing.txt").write_text("keep")
    monkeypatch.setattr(batch, "ModelAgent", lambda model: pytest.fail("No model must start"))
    with pytest.raises(FileExistsError):
        batch.run_all_configs(base_config(), model_config(), output_dir=target)
    assert (target / "existing.txt").read_text() == "keep"


def test_callback_failure_does_not_discard_rollouts(tmp_path, monkeypatch):
    monkeypatch.setattr(batch, "ModelAgent", OfflineAgent)

    def callback(event):
        if event["kind"] == "batch_end":
            raise RuntimeError("private observer failure")

    result = batch.run_all_configs(base_config(), model_config(), output_dir=tmp_path / "batch", on_event=callback)
    assert len(result["trajectories"]) == 3
    assert result["status"] == "complete_with_errors"
    assert result["summary"]["callback_errors"] == 1
    saved = json.loads(Path(result["artifact_paths"]["manifest"]).read_text())
    assert saved["callback_errors"] == [{"type": "RuntimeError", "category": "callback", "kind": "batch_end", "arm": None}]


def test_notebook_interrupt_stops_workers_and_keeps_settled_artifacts(tmp_path, monkeypatch):
    class InFlightAgent(OfflineAgent):
        def __call__(self, request):
            # Keep an action in flight long enough to interrupt active work,
            # rather than depending on how fast completed events are displayed.
            threading.Event().wait(timeout=0.05)
            return super().__call__(request)

    monkeypatch.setattr(batch, "ModelAgent", InFlightAgent)
    requested = False
    calling_thread = threading.get_ident()

    def callback(event):
        nonlocal requested
        assert threading.get_ident() == calling_thread
        if event["kind"] == "request" and not requested:
            requested = True
            raise KeyboardInterrupt()

    target = tmp_path / "batch"
    with pytest.raises(KeyboardInterrupt):
        batch.run_all_configs(base_config(), model_config(), output_dir=target, on_event=callback)
    assert requested
    saved = json.loads((target / "batch.json").read_text())
    assert saved["status"] == "interrupted"
    assert all(item["status"] not in {"queued", "running"} for item in saved["outcomes"])
    assert any(item["status"] == "interrupted" for item in saved["outcomes"])
    recorded = [item for item in saved["outcomes"] if "json" in item["artifact_paths"]]
    assert recorded
    for item in recorded:
        assert Path(item["artifact_paths"]["json"]).is_file()
        assert Path(item["artifact_paths"]["jsonl"]).is_file()
        assert Path(item["artifact_paths"]["html"]).is_file()
