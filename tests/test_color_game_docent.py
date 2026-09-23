"""Check exact private histories, failed attempts, and post-deadline audit data."""
from copy import deepcopy
import json

import pytest

pytest.importorskip("docent")

from experiments.color_game import GameConfig, make_plan, run_rollout
from experiments.color_game.docent import EXPORT_SCHEMA, rollout_to_agent_run


def visible(message):
    return {"role": message.role, "content": message.content if isinstance(message.content, str) else
            "".join(block.text for block in message.content if block.type == "text")}


class Participant:
    def __init__(self, assigned):
        self.assigned = assigned

    def metadata(self):
        return {"name": "offline-high", "model": "offline/model", "adapter": "test-adapter",
                "action_interface": {"version": "test-interface"}}

    def __call__(self, request):
        role, index, step = request["role"], request["round_index"], request["action_index"]
        if step == 0 and "get" in request["available_actions"]:
            action = {"action": "get", "url": "https://api.counterapi.dev/v1/test/red" +
                      ("/up" if role == "alice" else "")}
        else:
            action = {"action": "choose", "color": self.assigned[index]}
        text = json.dumps(action)
        return {"text": text, "reasoning": f"private-{role}-round-{index}-step-{step}",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.001},
                "raw": {"provider": "offline", "tool_arguments": text},
                "action_tool_version": "test-interface", "text_source": "tool_call.arguments"}


@pytest.fixture
def rollout(tmp_path):
    cfg = GameConfig(setting="async_counter", colors=["red", "blue"], rounds=2,
                     actions_per_agent=3, seed=4)
    player = Participant(make_plan(cfg)["assigned_colors"])
    return run_rollout(cfg, player, player, output_dir=tmp_path / "game")


@pytest.mark.parametrize("setting", ["guessing_only", "async_counter", "sync_counter"])
def test_real_engine_exports_exact_private_histories_in_all_settings(tmp_path, setting):
    cfg = GameConfig(setting=setting, colors=["red", "blue"], rounds=2,
                     actions_per_agent=3, seed=4, round_time_limit_s=0.4)
    player = Participant(make_plan(cfg)["assigned_colors"])
    source = run_rollout(cfg, player, player, output_dir=tmp_path / setting)
    before = deepcopy(source)
    campaign = {"campaign_id": "suite-1", "job_index": 7, "rollout_index": 2,
                "source_file": "original/rollout.json", "source_sha256": "source-sha"}
    exported = rollout_to_agent_run(source, campaign_metadata=campaign)
    assert source == before
    assert exported.metadata["campaign_metadata"] == campaign
    assert exported.metadata["rollout_index"] == 2
    assert exported.metadata["n_correct"] == 2
    assert exported.metadata["accuracy"] == 1
    assert exported.metadata["complete_rollout"] is True
    assert exported.metadata["transcripts_settled"] is True
    assert exported.metadata["models"] == source["models"]
    assert exported.metadata["source"] == source["source"]
    assert exported.metadata["counter_events"] == source["counter_events"]
    assert exported.metadata["counter_state"] == source["counter_state"]
    assert exported.metadata["rounds"] == [{k: v for k, v in rnd.items() if k != "actions"}
                                            for rnd in source["rounds"]]
    for transcript in exported.transcripts:
        role = transcript.name
        assert [visible(m) for m in transcript.messages] == [
            {"role": "system", "content": source["system_prompts"][role]},
            *source["agents"][role]["messages"]]
        actions = [action for rnd in source["rounds"] for action in rnd["actions"] if action["role"] == role]
        reasoning = [block.reasoning for m in transcript.messages if isinstance(m.content, list)
                     for block in m.content if block.type == "reasoning"]
        assert reasoning == [action["response"]["reasoning"] for action in actions]
        assert transcript.metadata["n_actions"] == len(actions)
        assert transcript.metadata["observed_message_count"] == len(transcript.messages)
        assert transcript.metadata["reasoning_history_replayed"] is False
        assert all(m.metadata["source_sha256"] == "source-sha" for m in transcript.messages)
        assert all(m.metadata["source_file"] == "original/rollout.json" for m in transcript.messages)
    bob_text = "\n".join(visible(m)["content"] for m in exported.transcripts[1].messages)
    assert "Your private assigned color is" not in bob_text
    assert "private-alice" not in bob_text


def test_response_links_reconstruct_exact_inputs_without_prefix_duplication(rollout):
    rollout["_file"], rollout["_sha256"] = "relative/rollout.json", "sha"
    exported = rollout_to_agent_run(rollout)
    for transcript in exported.transcripts:
        role = transcript.name
        responses = [m for m in transcript.messages if m.role == "assistant"]
        actions = [a for rnd in rollout["rounds"] for a in rnd["actions"] if a["role"] == role]
        for response, action in zip(responses, actions, strict=True):
            meta = response.metadata
            bounds = meta["request_message_range"]
            assert [visible(m) for m in transcript.messages[bounds["start"]:bounds["end_exclusive"]]] == action["request_messages"]
            assert meta["response_metadata"]["raw"] == action["response"]["raw"]
            assert meta["response_metadata"]["usage"] == action["response"]["usage"]
            assert meta["action_record"]["result"] == action["result"]
            assert meta["source_file"] == "relative/rollout.json"
            assert meta["source_sha256"] == "sha"
            assert "request_messages" not in meta["action_record"]


def test_stable_export_keys_do_not_set_sdk_ids_and_metadata_is_independent(rollout):
    a, b = rollout_to_agent_run(rollout), rollout_to_agent_run(rollout)
    assert a.metadata["export_key"] == b.metadata["export_key"] == f"{EXPORT_SCHEMA}:{rollout['rollout_id']}"
    assert "id" not in a.model_fields_set
    assert all("id" not in transcript.model_fields_set for transcript in a.transcripts)
    assert a.id != b.id
    assert a.transcripts[0].metadata["export_key"].endswith(":alice")
    a.metadata["config"]["colors"].append("changed")
    a.transcripts[0].metadata["model"]["model"] = "changed"
    assert rollout["models"]["alice"]["model"] == "offline/model"
    assert "changed" not in rollout["config"]["colors"]


def test_api_failure_is_attached_to_exact_request_without_fake_assistant(tmp_path):
    cfg = GameConfig(setting="async_counter", colors=["red", "blue"], rounds=2,
                     actions_per_agent=3, seed=4)
    player = Participant(make_plan(cfg)["assigned_colors"])

    def bob(request):
        if request["round_index"] == 0:
            raise RuntimeError("test failure")
        return player(request)

    source = run_rollout(cfg, player, bob, output_dir=tmp_path / "error")
    exported = rollout_to_agent_run(source)
    assert exported.metadata["status"] == "complete_with_errors"
    assert exported.metadata["complete_rollout"] is True
    transcript = exported.transcripts[1]
    assert [visible(m) for m in transcript.messages[1:]] == source["agents"]["bob"]["messages"]
    errors = [attempt for m in transcript.messages for attempt in (m.metadata or {}).get("action_attempts", [])
              if attempt["action_record"].get("error")]
    assert len(errors) == 1
    assert errors[0]["round_index"] == 0
    assert errors[0]["action_record"]["error"]["category"] == "model_api"


def test_late_response_is_retained_only_after_exact_observed_history(rollout):
    # A received response can be saved after the last sync deadline, with no
    # assistant or result inserted into the player's observed history.
    action = rollout["rounds"][-1]["actions"][-1]
    assert action["role"] == "bob"
    action.update(response_status="late", accepted=False,
                  error={"type": "DeadlineExpired", "category": "timing"},
                  result={"status": "deadline_expired", "applied": False})
    rollout["agents"]["bob"]["messages"] = deepcopy(action["request_messages"])
    last = rollout["rounds"][-1]
    last.update(bob_color=None, match=False)
    last["errors"] = [action["error"]]
    exported = rollout_to_agent_run(rollout)
    transcript = exported.transcripts[1]
    n = transcript.metadata["observed_message_count"]
    assert [visible(m) for m in transcript.messages[1:n]] == action["request_messages"]
    assert len(transcript.messages) == n + 2
    assert "Audit only" in visible(transcript.messages[n])["content"]
    late = transcript.messages[-1]
    assert visible(late)["content"] == action["response"]["text"]
    assert late.metadata["observed_by_agent"] is False
    assert late.metadata["provenance"] == "unobserved_model_response"
    assert late.content[0].reasoning == action["response"]["reasoning"]
    assert exported.metadata["rounds"][-1]["bob_color"] is None
    assert exported.metadata["unobserved_response_count"] == 1


def test_pending_responses_are_explicit_and_do_not_invent_outputs(rollout):
    action = rollout["rounds"][-1]["actions"][-1]
    rollout["agents"]["bob"]["messages"] = deepcopy(action["request_messages"])
    action.update(response_status="pending_late", response=None, accepted=False,
                  error={"type": "DeadlineExpired", "category": "timing"},
                  result={"status": "deadline_expired", "applied": False})
    rollout["status"] = "complete_pending_responses"
    exported = rollout_to_agent_run(rollout)
    assert exported.metadata["pending_responses"] == 1
    assert exported.metadata["transcripts_settled"] is False
    assert exported.metadata["unobserved_response_count"] == 0
    assert [visible(m) for m in exported.transcripts[1].messages[1:]] == action["request_messages"]


@pytest.mark.parametrize("status", ["failed", "interrupted"])
def test_terminal_partial_rollouts_are_kept_and_labeled(rollout, status):
    rollout["status"] = status
    rollout["config"]["rounds"] = 5
    exported = rollout_to_agent_run(rollout)
    assert exported.name.startswith("[PARTIAL]")
    assert exported.metadata["complete_rollout"] is False
    assert exported.metadata["recorded_rounds"] == 2
    assert exported.metadata["expected_rounds"] == 5
    assert exported.metadata["status"] == status


def test_interrupt_after_response_save_retains_unobserved_response(rollout):
    action = rollout["rounds"][-1]["actions"][-1]
    rollout["status"] = "interrupted"
    rollout["agents"]["bob"]["messages"] = deepcopy(action["request_messages"])
    action.update(action=None, result=None)
    exported = rollout_to_agent_run(rollout)
    late = exported.transcripts[1].messages[-1]
    assert late.metadata["observed_by_agent"] is False
    assert late.metadata["unobserved_reason"] == "terminal_partial_history_ended_at_request"
    assert visible(late)["content"] == action["response"]["text"]
    assert "not applied to the game" in visible(exported.transcripts[1].messages[-2])["content"]
    assert exported.metadata["unobserved_response_count"] == 1


@pytest.mark.parametrize("corruption,match", [
    ("running", "terminal"), ("missing_round", "every completed round"),
    ("unfinished_round", "every completed round"), ("duplicate_round", "Round indices"),
    ("system", "system prompt"), ("request", "history prefix"),
    ("response", "Model response"), ("observation", "Action result"),
    ("unmapped", "Unmapped assistant"),
])
def test_invalid_or_inconsistent_sources_are_rejected(rollout, corruption, match):
    action = rollout["rounds"][0]["actions"][0]
    if corruption == "running":
        rollout["status"] = "running"
    elif corruption == "missing_round":
        rollout["rounds"].pop()
    elif corruption == "unfinished_round":
        rollout["rounds"][-1].pop("actions_used")
    elif corruption == "duplicate_round":
        rollout["rounds"][-1]["round_index"] = 0
    elif corruption == "system":
        action["request_system"] += "changed"
    elif corruption == "request":
        action["request_messages"][0]["content"] += "changed"
    elif corruption == "response":
        action["response"]["text"] += "changed"
    elif corruption == "observation":
        action["result"]["changed"] = True
    elif corruption == "unmapped":
        rollout["agents"]["alice"]["messages"].append({"role": "assistant", "content": "unmapped"})
    with pytest.raises(ValueError, match=match):
        rollout_to_agent_run(rollout)
