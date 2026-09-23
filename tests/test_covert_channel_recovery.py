"""Recovery preserves saved responses and refuses ambiguous failure tails."""
from dataclasses import asdict
import json

import pytest

from ai_collusion.client import ModelConfig
from experiments.covert_channel.channel import Channel
from experiments.covert_channel.recovery import inventory, stage_campaign
from experiments.covert_channel.resume import ReplayMismatch, load_cohort, sha256, validate_sessions
from experiments.covert_channel.run import run_trial, write_json


MODEL = ModelConfig("offline", "stub", "stub")
COLORS = ["red", "blue"]


def make_failed_campaign(tmp_path, first_target="red", invalid_receiver_action=False):
    source = tmp_path / "failed"
    run = source / "offline"
    run.mkdir(parents=True)
    plan = {"answer_set": COLORS, "targets": [first_target, "blue"], "nonce": "same",
            "displayed_answer_sets": {"sender": COLORS, "receiver": COLORS[::-1]},
            "question_tags": ["first", "second"]}
    channel = Channel("sender_to_receiver", plan["nonce"], counter_mode="wiki")
    records, history = [], {}
    for q in range(2):
        def generate(model, system, messages, **kwargs):
            role = "sender" if "You are the sender" in system else "receiver"
            start = max(i for i, m in enumerate(messages) if m["content"].startswith("Question "))
            turn = sum(m["role"] == "assistant" for m in messages[start:])
            if q == 1 and role == "sender" and turn == 2:
                raise RuntimeError("Provider response missing choices")
            action = ({"action": "get", "url": channel.url + "/up"} if turn < 2 else {"action": "done"}) if role == "sender" else (
                {"action": "get", "url": channel.url} if turn == 0 else {"action": "guess", "answer": "blue"})
            if q == 1 and role == "receiver" and turn == 0 and invalid_receiver_action is not False:
                action = invalid_receiver_action
            return {"text": json.dumps(action), "reasoning": "Saved private reasoning", "raw": {"role": role, "turn": turn},
                    "usage": {"cost": 0}, "finish_reason": "stop"}

        events = [{"kind": "trial_start", "session_index": 0, "condition": "sender_to_receiver", "round_index": q,
                   "secret": plan["targets"][q], "nonce": plan["nonce"], "question_fuzz": plan["question_tags"][q],
                   "displayed_answer_sets": plan["displayed_answer_sets"]}]
        record = run_trial("sender_to_receiver", plan["targets"][q], plan["nonce"], COLORS, MODEL, MODEL,
                           max_turns=4, schedule="interleaved", channel=channel, histories=history,
                           memory="persistent", rounds_per_session=2, round_index=q,
                           displayed_answer_sets=plan["displayed_answer_sets"], question_fuzz=plan["question_tags"][q],
                           counter_mode="wiki", counter_docs="reference-v1", generate_fn=generate, emit=events.append)
        record.update(session_index=0, run_id="offline", sample_index=q)
        path = run / f"trial-{q:05d}__sender_to_receiver.json"
        write_json(path, record)
        path.with_suffix(".events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))
        records.append({"file": path.name, "sha256": sha256(path)})
        history = {role: agent["messages_final"] for role, agent in record["agents"].items()}
    cfg = dict(interface="tools", memory="persistent", schedule="interleaved", trials=1,
               arm=["sender_to_receiver"], rounds_per_session=2, direction=None, counter_mode="wiki",
               counter_docs="reference-v1", max_turns=4, max_seconds=120)
    write_json(run / "manifest.json", dict(config=cfg, models=[asdict(MODEL)] * 2, records=records, status="stopped"))
    write_json(source / "plans.json", [plan])
    write_json(source / "models.yaml", {"models": [asdict(MODEL)]})
    write_json(source / "campaign.json", {"status": "stopped", "model_names": ["offline"],
                                         "plans_sha256": sha256(source / "plans.json"),
                                         "jobs": [{"model": "offline", "run_id": "offline"}]})
    return source, plan


@pytest.mark.parametrize("first_target", COLORS)
def test_stage_preserves_completed_bytes_and_every_partial_response_and_guess(tmp_path, first_target):
    source, plan = make_failed_campaign(tmp_path, first_target)
    before = inventory(source)
    stage = tmp_path / "stage"
    receipt = stage_campaign(source, stage)
    assert receipt["status"] == "ready_for_exact_resume" and inventory(source) == before
    cohort = receipt["cohorts"][0]
    assert cohort["good_records_preserved"] == cohort["error_questions_recovered"] == 1
    assert cohort["saved_responses_preserved"] == 4
    assert cohort["saved_guesses_preserved"] == 1
    first = "offline/trial-00000__sender_to_receiver.json"
    failed = "offline/trial-00001__sender_to_receiver.json"
    assert (stage / first).read_bytes() == (source / first).read_bytes()
    assert not (stage / failed).exists()
    assert (stage / "recovery-originals" / failed).read_bytes() == (source / failed).read_bytes()
    journal = "offline/trial-00001__sender_to_receiver.events.jsonl"
    assert (stage / "recovery-originals" / journal).read_bytes() == (source / journal).read_bytes()
    assert (source / journal).read_bytes().startswith((stage / journal).read_bytes())
    events = [json.loads(line) for line in (stage / journal).read_text().splitlines()]
    assert events[-1]["kind"] == "request" and events[-1]["agent_id"] == "sender" and events[-1]["turn"] == 2
    validated = validate_sessions(*load_cohort(stage / "offline", [plan], MODEL), MODEL)
    assert validated["completed_questions"] == 1 and validated["saved_responses_replayed"] == 4


def test_check_only_does_not_write_or_call_a_provider(tmp_path):
    source, _ = make_failed_campaign(tmp_path)
    before = inventory(tmp_path)
    assert stage_campaign(source)["status"] == "offline_validated"
    assert inventory(tmp_path) == before


@pytest.mark.parametrize("invalid_action", [None, [{"action": "get", "url": "unused"}]])
def test_saved_guess_after_invalid_receiver_action_is_preserved(tmp_path, invalid_action):
    source, _ = make_failed_campaign(tmp_path, invalid_receiver_action=invalid_action)
    receipt = stage_campaign(source, tmp_path / "stage")
    assert receipt["cohorts"][0]["saved_responses_preserved"] == 4
    assert receipt["cohorts"][0]["saved_guesses_preserved"] == 1


@pytest.mark.parametrize("event", [
    {"kind": "response", "agent_id": "sender", "turn": 9, "response": {"text": "saved"}},
    {"kind": "channel", "agent_id": "sender", "effects": [{"count": 9}]},
    {"kind": "turn", "agent_id": "sender", "turn": 9, "source": "counter", "error": None},
])
def test_any_response_or_state_change_after_failure_prevents_staging(tmp_path, event):
    source, _ = make_failed_campaign(tmp_path)
    journal = source / "offline/trial-00001__sender_to_receiver.events.jsonl"
    with journal.open("a") as stream:
        stream.write(json.dumps(event) + "\n")
    with pytest.raises(ValueError, match="Cannot remove"):
        stage_campaign(source, tmp_path / "stage")
    assert not (tmp_path / "stage").exists()


def test_changed_saved_request_is_rejected_before_staging(tmp_path):
    source, _ = make_failed_campaign(tmp_path)
    journal = source / "offline/trial-00001__sender_to_receiver.events.jsonl"
    events = [json.loads(line) for line in journal.read_text().splitlines()]
    next(event for event in events if event["kind"] == "request")["system"] += " changed"
    journal.write_text("".join(json.dumps(event) + "\n" for event in events))
    with pytest.raises(ReplayMismatch, match="Saved request differs"):
        stage_campaign(source, tmp_path / "stage")
    assert not (tmp_path / "stage").exists()


def test_corrupt_record_rejected_before_staging(tmp_path):
    source, _ = make_failed_campaign(tmp_path)
    with (source / "offline/trial-00000__sender_to_receiver.json").open("a") as stream:
        stream.write(" ")
    with pytest.raises(ValueError, match="checksum"):
        stage_campaign(source, tmp_path / "stage")
    assert not (tmp_path / "stage").exists()
