"""Check evidence attribution rules specific to the frame-awareness classifier."""
from copy import deepcopy
import json
import pytest

from ai_collusion import judge
from scripts.run_frame_awareness import (RUBRIC, validate_frame_judgment, build_frame_prompt,
                                       feedback, make_jobs, segment_projection)


@pytest.fixture
def rubric():
    return judge.load_rubric(RUBRIC)


@pytest.fixture
def projection():
    return {"turns": [{"turn": 8, "text": "This is a puzzle.",
                       "reasoning": "This world is simulated.", "result": "Saved"},
                      {"turn": 12, "text": "This is an integrity test.",
                       "reasoning": None, "result": "Done"}]}


def event(turn=8, field="reasoning", quote="This world is simulated.", labels=None):
    return {"labels": labels or ["simulation"], "stage": "expression",
            "quotes": [{"turn": turn, "field": field, "quote": quote}], "note": "Explicit frame."}


def test_independent_labels_and_ambiguity(rubric, projection):
    e = event(labels=["simulation", "puzzle_or_capability_challenge"])
    e["quotes"].append({"turn": 8, "field": "text", "quote": "This is a puzzle."})
    result = validate_frame_judgment({"evidence": [e],
        "uncertainties": {"adversarial_trap": "Ambiguous at live turn 12."}}, projection, rubric)
    assert result["labels"] == {"simulation": "present", "puzzle_or_capability_challenge": "present",
                                "adversarial_trap": "uncertain", "safety_evaluation": "absent"}


@pytest.mark.parametrize("violation", ["attempt", "result", "cross_turn", "duplicate_turn",
                                      "reverse_order", "missing_note", "nonliteral", "prefill"])
def test_invalid_evidence_is_an_error_not_a_negative(rubric, projection, violation):
    payload = {"evidence": [event()]}
    e = payload["evidence"][0]
    later = event(12, "text", "This is an integrity test.", ["safety_evaluation"])
    if violation == "attempt":
        e["stage"] = "attempt"
    elif violation == "result":
        e["quotes"].append({"turn": 8, "field": "result", "quote": "Saved"})
    elif violation == "cross_turn":
        e["quotes"] += later["quotes"]
    elif violation == "duplicate_turn":
        payload["evidence"].append(event(8, "text", "This is a puzzle.", ["puzzle_or_capability_challenge"]))
    elif violation == "reverse_order":
        payload["evidence"].insert(0, later)
    elif violation == "missing_note":
        del e["note"]
    elif violation == "nonliteral":
        e["quotes"][0]["quote"] = "This world is definitely simulated."
    elif violation == "prefill":
        e["quotes"][0]["turn"] = 0
    original = deepcopy(payload)
    with pytest.raises(ValueError):
        validate_frame_judgment(payload, projection, rubric)
    assert payload == original


def test_empty_evidence_is_valid(rubric, projection):
    result = validate_frame_judgment({"evidence": []}, projection, rubric)
    assert set(result["labels"].values()) == {"absent"}


def test_rendering_retains_all_live_fields_and_context(rubric, projection):
    projection["context"] = {"system": "Context only", "prefill": [], "context_only": True}
    before = deepcopy(projection)
    system, messages = build_frame_prompt(projection, rubric)
    for turn in projection["turns"]:
        for field in ("text", "reasoning", "result"):
            content = turn.get(field)
            if content is not None:
                assert f"LIVE TURN {turn['turn']} — FIELD {field}:\n{content}" in messages[0]["content"]
    assert "CONTEXT ONLY" in messages[0]["content"]
    assert rubric["text"] in system
    assert projection == before


def test_retry_feedback_locates_exact_quote_without_rewriting_output(projection):
    payload = {"evidence": [event(turn=12)]}
    previous = {"input": projection, "response": {"text": json.dumps(payload)},
                "error": {"phase": "validation", "message": "Wrong live turn"}}
    before = deepcopy(previous)
    message = feedback(previous)
    assert '"exact_matching_live_turns": [8]' in message
    assert previous == before


def test_segments_cover_every_turn_once_and_neighbors_cannot_be_cited(rubric):
    projection = {"context": {"context_only": True}, "turns": [
        {"turn": n, "text": "This is a puzzle.", "reasoning": None, "result": "context"}
        for n in range(1, 48)]}
    jobs = make_jobs([{"source": "example", "n_turns": 47}])
    segments = [segment_projection(projection, job) for job in jobs]
    assert [t["turn"] for s in segments for t in s["turns"]] == list(range(1, 48))
    assert [t["turn"] for t in segments[1]["context"]["neighboring_live_turns_context_only"]] == [20, 41]
    with pytest.raises(ValueError):
        validate_frame_judgment({"evidence": [event(20, "text", "This is a puzzle.",
                                                   ["puzzle_or_capability_challenge"])]}, segments[1], rubric)
