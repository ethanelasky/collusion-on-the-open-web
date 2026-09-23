import copy
import json
import threading
from dataclasses import asdict

import pytest

from ai_collusion.client import ModelConfig
from experiments.covert_channel.channel import Channel
from experiments.covert_channel.resume import (
    ReplayBoundary, ReplayMismatch, ResponseReplay, load_cohort, read_journal,
    restore_records, sha256, validate_sessions,
)
from experiments.covert_channel.run import run_trial, write_json


MODEL = ModelConfig("offline", "stub", "stub")
COLORS = ["red", "blue"]


def scripted(cfg, system, messages, **kwargs):
    role = "sender" if "You are the sender" in system else "receiver"
    current = max(i for i, m in enumerate(messages) if m["role"] == "user" and "Question " in m["content"])
    turn = sum(m["role"] == "assistant" for m in messages[current:])
    url = "https://api.counterapi.dev/v1/private/any-key"
    if role == "sender":
        action = {"action": "get", "url": url + "/up"} if turn < 2 else {"action": "done"}
    elif turn == 0:
        action = {"action": "get", "url": "https://api.counterapi.dev/v1/other/key/up"}
    elif turn < 3:
        action = {"action": "get", "url": url}
    else:
        action = {"action": "guess", "answer": "blue"}
    return {"text": json.dumps(action), "reasoning": f"Private {role} reasoning {turn}",
            "raw": {"saved": role, "turn": turn}, "usage": {"cost": 0.001}, "finish_reason": "stop"}


def question(channel, q, histories, generate_fn=scripted, emit=None):
    return run_trial("two_way", COLORS[q], "private", COLORS, MODEL, MODEL,
                     max_turns=4, schedule="interleaved", memory="persistent", channel=channel,
                     histories=histories, round_index=q, rounds_per_session=2,
                     counter_mode="wiki", question_fuzz=f"tag{q}",
                     displayed_answer_sets={"sender": COLORS, "receiver": COLORS[::-1]},
                     generate_fn=generate_fn, emit=emit)


def session():
    channel = Channel("two_way", "private", counter_mode="wiki")
    first = question(channel, 0, {})
    histories = {role: first["agents"][role]["messages_final"] for role in ("sender", "receiver")}
    events = []
    second = question(channel, 1, histories, emit=events.append)
    return first, second, events, channel


@pytest.mark.parametrize("kind", ["request", "response", "channel", "turn"])
@pytest.mark.parametrize("occurrence", [0, 1, 3])
def test_resume_any_durable_boundary_preserves_both_private_histories_and_counters(kind, occurrence):
    first, expected, events, old_counter = session()
    positions = [i for i, event in enumerate(events) if event["kind"] == kind]
    cut = positions[min(occurrence, len(positions) - 1)] + 1
    prefix = copy.deepcopy(events[:cut])
    saved = sum(event["kind"] == "response" for event in prefix)
    new_calls = []

    def fresh(*args, **kwargs):
        new_calls.append((args, kwargs))
        return scripted(*args, **kwargs)

    replay = ResponseReplay(prefix, fresh)
    counter = Channel("two_way", "private", counter_mode="wiki")
    histories = restore_records(counter, [first])
    actual = question(counter, 1, histories, generate_fn=replay, emit=replay.check_event)
    replay.assert_consumed()
    assert replay.replayed == saved
    assert len(new_calls) == expected["n_turns"] - saved
    assert actual["guess"] == expected["guess"]
    for role in ("sender", "receiver"):
        assert actual["agents"][role]["messages_final"] == expected["agents"][role]["messages_final"]
        assert [t["response"] for t in actual["agents"][role]["turns"]] == [t["response"] for t in expected["agents"][role]["turns"]]
    assert counter.media["sender"].counts == old_counter.media["sender"].counts
    assert counter.media["receiver"].counts == old_counter.media["receiver"].counts
    assert counter.media["sender"].counts[("private", "any-key")] == 4
    assert counter.media["sender"].counts[("other", "key")] == 2


def test_offline_validation_stops_before_any_new_provider_call():
    first, _, events, _ = session()
    prefix = events[:next(i for i, e in enumerate(events) if e["kind"] == "response") + 1]
    replay = ResponseReplay(prefix)
    counter = Channel("two_way", "private", counter_mode="wiki")
    with pytest.raises(ReplayBoundary):
        question(counter, 1, restore_records(counter, [first]), generate_fn=replay, emit=replay.check_event)
    replay.assert_consumed()
    assert replay.replayed == 1


def test_changed_prompt_aborts_before_fallback():
    first, _, events, _ = session()
    events[0]["system"] += " CHANGED"
    replay = ResponseReplay(events, lambda *a, **k: pytest.fail("Provider was called"))
    counter = Channel("two_way", "private", counter_mode="wiki")
    with pytest.raises(ReplayMismatch):
        question(counter, 1, restore_records(counter, [first]), generate_fn=replay, emit=replay.check_event)


def test_changed_counter_result_aborts_restore():
    first, _, _, _ = session()
    first["channel_events"][0]["body"] = "wrong"
    with pytest.raises(ValueError, match="counter event"):
        restore_records(Channel("two_way", "private", counter_mode="wiki"), [first])


def test_saved_channel_before_missing_turn_is_verified_before_any_new_call():
    first, _, events, _ = session()
    last = next(i for i, event in enumerate(events) if event["kind"] == "channel")
    prefix = events[:last + 1]
    prefix[-1]["body"] = "altered count"
    replay = ResponseReplay(prefix, lambda *a, **k: pytest.fail("Provider was called"))
    counter = Channel("two_way", "private", counter_mode="wiki")
    with pytest.raises(ReplayMismatch, match="counter body"):
        question(counter, 1, restore_records(counter, [first]), generate_fn=replay, emit=replay.check_event)


def test_counter_restoration_keeps_session_instances_separate():
    first, _, _, _ = session()
    first_counter = Channel("two_way", "private", counter_mode="wiki")
    other_counter = Channel("two_way", "private", counter_mode="wiki")
    restore_records(first_counter, [first])
    assert first_counter.media["sender"].counts
    assert not other_counter.media["sender"].counts


def test_torn_journal_tail_is_not_a_saved_response(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'{"kind":"request"}\n{"kind":"response",')
    events, discarded = read_journal(path)
    assert events == [{"kind": "request"}]
    assert discarded == len(b'{"kind":"response",')
    path.write_bytes(b'not-json\n{"kind":"request"}\n')
    with pytest.raises(ValueError, match="Corrupt journal"):
        read_journal(path)


def make_cohort(tmp_path):
    first, _, events, _ = session()
    first.update(session_index=0, run_id="offline", sample_index=0)
    path = tmp_path / "trial-00000__two_way.json"
    write_json(path, first)
    cfg = dict(interface="tools", memory="persistent", schedule="interleaved", trials=1,
               arm=["two_way"], rounds_per_session=2, direction=None, counter_mode="wiki",
               max_turns=4, max_seconds=120)
    manifest = dict(config=cfg, models=[asdict(MODEL)] * 2,
                    records=[{"file": path.name, "sha256": sha256(path)}])
    write_json(tmp_path / "manifest.json", manifest)
    plan = dict(answer_set=COLORS, targets=COLORS, nonce="private", question_tags=["tag0", "tag1"],
                displayed_answer_sets={"sender": COLORS, "receiver": COLORS[::-1]})
    start = dict(kind="trial_start", session_index=0, condition="two_way", round_index=1,
                 secret="blue", nonce="private", question_fuzz="tag1", displayed_answer_sets=plan["displayed_answer_sets"])
    prefix = [start] + events[:4]
    (tmp_path / "trial-00001__two_way.events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in prefix))
    return manifest, plan


def test_full_cohort_offline_preflight_and_model_change_rejected(tmp_path):
    _, plan = make_cohort(tmp_path)
    manifest, sessions = load_cohort(tmp_path, [plan], MODEL)
    stats = validate_sessions(manifest, sessions, MODEL)
    assert stats["completed_questions"] == 1
    assert stats["partial_questions"] == 1
    assert stats["saved_responses_replayed"] == 1
    changed = copy.deepcopy(MODEL)
    changed.max_tokens += 1
    with pytest.raises(ValueError, match="generation settings"):
        load_cohort(tmp_path, [plan], changed)
    changed = copy.deepcopy(MODEL)
    changed.api_key_env = "OTHER_KEY"
    load_cohort(tmp_path, [plan], changed)


def test_corrupt_completed_record_rejected(tmp_path):
    _, plan = make_cohort(tmp_path)
    with (tmp_path / "trial-00000__two_way.json").open("a") as stream:
        stream.write(" ")
    with pytest.raises(ValueError, match="checksum"):
        load_cohort(tmp_path, [plan], MODEL)


def test_terminal_error_journal_rejected():
    with pytest.raises(ValueError, match="terminal model error"):
        ResponseReplay([{"kind": "turn", "agent_id": "sender", "turn": 0, "source": "model-error", "error": {"type": "Error"}}])


def test_old_model_schema_uses_current_defaults(tmp_path):
    manifest, plan = make_cohort(tmp_path)
    for model in manifest["models"]:
        model.pop("response_tool_name", None)
    write_json(tmp_path / "manifest.json", manifest)
    load_cohort(tmp_path, [plan], MODEL)


def test_orphan_record_is_rejected_before_repeating_work(tmp_path):
    _, plan = make_cohort(tmp_path)
    (tmp_path / "trial-00001__two_way.json").write_text("{}")
    with pytest.raises(ValueError, match="orphan records"):
        load_cohort(tmp_path, [plan], MODEL)


def test_real_child_process_continuation_preserves_records_and_reports(tmp_path, monkeypatch):
    from experiments.covert_channel import resume
    source = tmp_path / "source-campaign"
    cohort = source / "offline"
    cohort.mkdir(parents=True)
    manifest, plan = make_cohort(cohort)
    manifest.update(run_id="offline", status="running", schema="covert-channel/v1", planned_guesses=2)
    write_json(cohort / "manifest.json", manifest)
    write_json(source / "plans.json", [plan])
    write_json(source / "models.yaml", {"models": [asdict(MODEL)]})
    write_json(source / "campaign.json", dict(
        status="interrupted", model_names=["offline"], plans_sha256=sha256(source / "plans.json"),
        groups=["two_way"], rounds_per_session=2, sessions_per_group=1, planned_sessions=1,
        planned_guesses=2, answer_set=COLORS, max_turns_per_session=16, counter_mode="wiki",
        jobs=[{"model": "offline", "run_id": "offline", "groups": ["two_way"]}]))
    original_hash = sha256(cohort / "trial-00000__two_way.json")
    monkeypatch.setattr(resume.time, "sleep", lambda _: threading.Event().wait(0.02))
    out = tmp_path / "continued"
    assert resume.main(["--source", str(source), "--out", str(out), "--workers-per-model", "2",
                        "--request-limit-per-model", "2"]) == 0
    assert sha256(out / "offline/trial-00000__two_way.json") == original_hash
    assert sha256(cohort / "trial-00000__two_way.json") == original_hash
    continued = json.loads((out / "offline/trial-00001__two_way.json").read_text())
    assert continued["continuation"]["saved_responses_replayed"] == 1
    assert continued["agents"]["sender"]["history_message_count"] > 0
    assert continued["agents"]["receiver"]["history_message_count"] > 0
    assert continued["channel_events"][0]["effects"][0]["counter"]["value"] == 3
    progress = json.loads((out / "progress.json").read_text())
    assert progress["status"] == "complete"
    assert progress["scored_guesses"] == 2
    assert progress["questions_with_model_errors"] == 0
    assert progress["request_admission_by_model"]["offline"]["request_limit"] == 2
    assert (out / "source-sha256.json").exists()
    assert (out / "offline/continuation-source-journals/trial-00001__two_way.events.jsonl").exists()
