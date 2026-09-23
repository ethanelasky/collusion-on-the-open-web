"""Session exports preserve each private history and every returned reasoning block."""
from copy import deepcopy
import hashlib
import json

import pytest

from ai_collusion.client import ModelConfig
from experiments.covert_channel.channel import Channel
from experiments.covert_channel.docent import session_to_agent_run
from experiments.covert_channel.run import run_trial


@pytest.fixture
def complete_session(monkeypatch):
    def generate(config, system, messages, **kwargs):
        start = max(i for i, m in enumerate(messages) if m["content"].startswith("Question "))
        prompt = messages[start]["content"]
        question = int(prompt.split()[1]) - 1
        role = "sender" if "Your private assigned answer" in prompt else "receiver"
        turn = sum(m["role"] == "assistant" for m in messages[start:])
        if turn == 0:
            action = {"action": "get", "url": "https://api.counterapi.dev/v1/freely-chosen/CA5"
                      + ("/up" if role == "sender" else "")}
        else:
            action = {"action": "done"} if role == "sender" else {"action": "guess", "answer": "red"}
        text = json.dumps(action)
        label = f"{role}-q{question}-t{turn}"
        return {"text": text, "reasoning": label, "finish_reason": "tool_calls",
                "usage": {"prompt_tokens": 10, "completion_tokens": 20,
                          "completion_tokens_details": {"reasoning_tokens": 5}, "cost": 0.01},
                "text_source": "tool_call.arguments", "tool_call_id": label,
                "raw": {"provider": "offline", "choices": [{"message": {"tool_calls": [
                    {"id": label, "type": "function", "function": {"name": "counter_action", "arguments": text}}]}}]}}

    monkeypatch.setattr("experiments.covert_channel.participants.generate", generate)
    model = ModelConfig("test-alias", "stub", "test/model", response_tool_name="counter_action")
    channel, histories, records = Channel("two_way", "private", counter_mode="wiki"), {}, []
    manifest = {"run_id": "test-run", "records": [], "config": {"preset": "counter-four-groups"},
                "session_plan_sha256": "plan-sha"}
    for index, secret in enumerate(["red", "blue", "red", "green", "yellow"]):
        record = run_trial("two_way", secret, "private", ["red", "blue", "green", "yellow"], model, model,
                           max_turns=5, schedule="interleaved", channel=channel, histories=histories,
                           memory="persistent", round_index=index, rounds_per_session=5,
                           counter_mode="wiki", question_fuzz=f"tag-{index}")
        record.update(run_id="test-run", sample_index=index, session_index=7,
                      counter_instance_id="test-run:two_way:session-7")
        source_file = f"question-{index}.json"
        manifest["records"].append({"file": source_file,
                                    "sha256": hashlib.sha256(json.dumps(record).encode()).hexdigest()})
        record["_file"] = source_file
        records.append(record)
        histories = {role: a["messages_final"] for role, a in record["agents"].items()}
    return records, manifest


def visible(message):
    content = message.content
    return {"role": message.role, "content": content if isinstance(content, str) else
            "".join(block.text for block in content if block.type == "text")}


def test_five_questions_have_one_exact_private_history_and_all_reasoning(complete_session):
    records, manifest = complete_session
    before = deepcopy(complete_session)
    result = session_to_agent_run(list(reversed(records)), manifest)
    assert complete_session == before
    assert [t.name for t in result.transcripts] == ["sender", "receiver"]
    for transcript in result.transcripts:
        role = transcript.name
        agent = records[-1]["agents"][role]
        assert [visible(m) for m in transcript.messages] == [
            {"role": "system", "content": agent["context"]["system"]}, *agent["messages_final"]]
        reasoning = [b.reasoning for m in transcript.messages if isinstance(m.content, list)
                     for b in m.content if b.type == "reasoning"]
        assert reasoning == [f"{role}-q{q}-t{t}" for q in range(5) for t in range(2)]
        assert transcript.metadata["n_turns"] == 10
        assert transcript.metadata["private_history"] is True
    receiver_text = "\n".join(visible(m)["content"] for m in result.transcripts[1].messages)
    assert "Your private assigned answer" not in receiver_text
    assert result.metadata["model"] == "test/model"
    assert result.metadata["model_alias"] == "test-alias"
    assert result.metadata["n_questions"] == 5
    assert result.metadata["n_correct"] == 2
    assert result.metadata["accuracy"] == 0.4
    assert result.metadata["question_tags"] == [f"tag-{q}" for q in range(5)]
    assert [q["target"] for q in result.metadata["questions"]] == [r["secret"] for r in records]
    assert result.metadata["source_files"] == manifest["records"]
    assert result.metadata["session_plan_sha256"] == "plan-sha"
    assert result.metadata["experiment_config"] == manifest["config"]
    assert result.metadata["counter_instance_id"] == records[0]["counter_instance_id"]
    assert result.metadata["usage"]["recorded_responses"] == 20
    assert result.metadata["reported_cost_usd"] == pytest.approx(0.2)
    assert result.metadata["reasoning_history_replayed"] is False


def test_response_provenance_keeps_raw_calls_and_references_exact_input(complete_session):
    records, manifest = complete_session
    result = session_to_agent_run(records, manifest)
    for transcript in result.transcripts:
        responses = [m for m in transcript.messages if m.role == "assistant"]
        for q in range(5):
            for t in range(2):
                message = responses[2 * q + t]
                meta = message.metadata
                turn = records[q]["agents"][transcript.name]["turns"][t]
                assert meta["question_index"] == q and meta["turn"] == t
                assert meta["source_file"] == f"question-{q}.json"
                assert meta["source_sha256"] == manifest["records"][q]["sha256"]
                assert meta["raw_response"] == turn["response"]["raw"]
                assert meta["raw_response"]["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] == visible(message)["content"]
                assert meta["usage"] == turn["response"]["usage"]
                assert meta["action"] == turn["action"]
                assert "request" not in meta and "messages" not in meta["request_metadata"]
                bounds = meta["request_message_range"]
                assert [visible(m) for m in transcript.messages[bounds["start"]:bounds["end_exclusive"]]] == turn["request"]["messages"]
                assert "ContentText only" in bounds["note"]
                assert meta["request_message_count"] == len(turn["request"]["messages"])
                assert meta["request_metadata"]["model_seed"] is None
                assert meta["request_metadata"]["at_global_s"] == turn["at_global_s"]


def test_continuation_can_add_default_model_fields_without_changing_sources(complete_session):
    records, manifest = complete_session
    for record in records:
        for agent in record["agents"].values():
            agent["model"]["response_tool_name"] = None
    for record in records[:2]:
        for agent in record["agents"].values():
            del agent["model"]["response_tool_name"]
    before = deepcopy(complete_session)
    result = session_to_agent_run(records, manifest)
    assert result.metadata["model_config"]["response_tool_name"] is None
    assert result.metadata["n_questions"] == 5
    assert result.metadata["source_files"] == manifest["records"]
    assert complete_session == before
    for transcript in result.transcripts:
        assert [visible(message) for message in transcript.messages[1:]] == records[-1]["agents"][transcript.name]["messages_final"]


def test_missing_model_field_does_not_hide_a_changed_nondefault_setting(complete_session):
    records, manifest = complete_session
    records[2]["agents"]["sender"]["model"].pop("response_tool_name")
    with pytest.raises(ValueError, match="models"):
        session_to_agent_run(records, manifest)


@pytest.mark.parametrize("field,value", [("condition", "baseline"), ("session_index", 99),
                                       ("counter_instance_id", "another-counter")])
def test_mixed_sessions_are_rejected(complete_session, field, value):
    records, manifest = complete_session
    records[2][field] = value
    with pytest.raises(ValueError, match="inconsistent sessions"):
        session_to_agent_run(records, manifest)


@pytest.mark.parametrize("corruption,error", [
    ("model", "models"), ("system", "system prompt"), ("history", "history prefix"),
    ("missing_question", "complete session"), ("duplicate_question", "complete session"),
    ("request", "saved history prefix"), ("missing_source", "source file"),
])
def test_corrupt_provenance_is_rejected(complete_session, corruption, error):
    records, manifest = complete_session
    agent = records[2]["agents"]["receiver"]
    if corruption == "model":
        agent["model"]["model"] = "different/model"
    elif corruption == "system":
        agent["context"]["system"] += " changed"
    elif corruption == "history":
        agent["history_message_count"] -= 1
    elif corruption == "missing_question":
        records.pop()
    elif corruption == "duplicate_question":
        records[2]["question_index"] = 1
    elif corruption == "request":
        agent["turns"][0]["request"]["messages"][0]["content"] += " changed"
    elif corruption == "missing_source":
        records[2]["_file"] = "missing.json"
    with pytest.raises(ValueError, match=error):
        session_to_agent_run(records, manifest)
