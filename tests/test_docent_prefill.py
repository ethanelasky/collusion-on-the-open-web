"""Validate provenance survives the real Docent SDK's serialization."""
from copy import deepcopy

import pytest

pytest.importorskip("docent")

from ai_collusion.docent_cli import record_to_agent_run
from ai_collusion.preview import to_agent_run
from ai_collusion.docent_prefill import (
    PREFILL_PREFIX, PREFILL_RENDERING_VERSION, prefill_metadata, render_prefill_message,
)


def record():
    prefill = [dict(role="user", content="task\n"),
               dict(role="assistant", content="prefilled action\n"),
               dict(role="user", content="prefilled result\n")]
    return dict(context=dict(system="system\n", messages=prefill),
                model=dict(name="test", model_id="test-id", transport="test"),
                condition="test", _file="test.json", live_start_message_index=3,
                episode=dict(turns=[dict(response=dict(text="live", reasoning="reason"),
                                         result="live result", source="simulated")], n_turns=1),
                messages_final=prefill + [dict(role="assistant", content="live"),
                                         dict(role="user", content="live result")])


@pytest.mark.parametrize("legacy", [False, True])
def test_episode_keeps_roles_content_and_provenance(legacy):
    rec = record()
    if legacy:
        del rec["live_start_message_index"]
        rec["context"]["messages"] = rec["context"]["messages"][:1]
    before = deepcopy(rec)
    run = record_to_agent_run(rec, {})
    msgs = run.model_dump(mode="json")["transcripts"][0]["messages"]
    assert rec == before
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == "system\n"
    assert msgs[0]["metadata"]["provenance"] == "system"
    for actual, expected in zip(msgs[1:4], rec["messages_final"][:3]):
        assert (actual["role"], actual["content"]) == (expected["role"], PREFILL_PREFIX + expected["content"])
        assert actual["metadata"] == dict(prefill=True, provenance="prefill",
                                          prefill_rendering=PREFILL_RENDERING_VERSION)
    assert len(msgs) == 6
    assert msgs[4]["metadata"] == dict(prefill=False, provenance="evaluated_model")
    assert msgs[4]["content"][0]["reasoning"] == "reason"
    assert msgs[5]["metadata"] == dict(prefill=False, provenance="environment", source="simulated")


@pytest.mark.parametrize("role, provenance, prefill", [
    ("evaluee", "prefill", True), ("env-model", "environment_preview", False)])
def test_preview_labels_without_editing_source(role, provenance, prefill):
    rec = record()
    p = dict(**rec["context"], role=role, name="preview", condition="test", variant="base",
             mode="neutral", agent="test")
    before = deepcopy(p)
    msgs = to_agent_run(p).model_dump(mode="json")["transcripts"][0]["messages"]
    assert p == before
    assert msgs[0]["role"] == "system"
    assert msgs[0]["metadata"]["provenance"] == "system"
    for actual, expected in zip(msgs[1:], p["messages"]):
        assert actual["content"] == (PREFILL_PREFIX if prefill else "") + expected["content"]
        assert actual["role"] == expected["role"]
        metadata = dict(prefill=prefill, provenance=provenance)
        if prefill:
            metadata["prefill_rendering"] = PREFILL_RENDERING_VERSION
        assert actual["metadata"] == metadata


def test_raw_reference_option_preserves_source_for_judge():
    rec = record()
    run = record_to_agent_run(rec, {}, render_prefill=False)
    for message, source in zip(run.transcripts[0].messages[1:4], rec["context"]["messages"]):
        assert message.content == source["content"]
        assert "prefill_rendering" not in message.metadata


def test_source_metadata_cannot_suppress_display_marker():
    rec = record()
    source = rec["context"]["messages"][0]
    source["content"] = PREFILL_PREFIX + "literal source"
    source["metadata"] = {"prefill_rendering": PREFILL_RENDERING_VERSION}
    before = deepcopy(rec)
    run = record_to_agent_run(rec, {})
    assert run.transcripts[0].messages[1].content == PREFILL_PREFIX + source["content"]
    preview = dict(**rec["context"], role="evaluee", name="preview", condition="test",
                   variant="base", mode="neutral", agent="test")
    assert to_agent_run(preview).transcripts[0].messages[1].content == PREFILL_PREFIX + source["content"]
    assert rec == before


@pytest.mark.parametrize("content", ["task", "[PREFILL]\nliteral source prefix", ""])
def test_marker_is_idempotent_and_preserves_literal_source_prefix(content):
    from docent.data_models.chat import parse_chat_message

    source = parse_chat_message(dict(role="user", content=content,
                                    metadata=dict(prefill=True, custom="keep")))
    before = source.model_dump(mode="json")
    rendered = render_prefill_message(source)
    assert source.model_dump(mode="json") == before
    assert rendered.content == PREFILL_PREFIX + content
    assert rendered.id == source.id
    assert rendered.metadata["custom"] == "keep"
    assert render_prefill_message(rendered).model_dump(mode="json") == rendered.model_dump(mode="json")


@pytest.mark.parametrize("reasoning_first", [False, True])
def test_structured_prefill_keeps_original_blocks_and_starts_with_marker(reasoning_first):
    from docent.data_models.chat import parse_chat_message
    from docent.data_models.chat.content import ContentReasoning, ContentText

    blocks = [ContentText(text="source text", refusal=True), ContentReasoning(reasoning="source reasoning")]
    if reasoning_first:
        blocks.reverse()
    source = parse_chat_message(dict(role="assistant", content=blocks, metadata=dict(prefill=True)))
    before = source.model_dump(mode="json")
    rendered = render_prefill_message(source)
    actual = rendered.model_dump(mode="json")["content"]
    assert actual[0]["text"].startswith(PREFILL_PREFIX)
    if reasoning_first:
        assert actual[0]["text"] == PREFILL_PREFIX
        assert actual[1:] == before["content"]
    else:
        actual[0]["text"] = actual[0]["text"][len(PREFILL_PREFIX):]
        assert actual == before["content"]
    assert source.model_dump(mode="json") == before
    assert render_prefill_message(rendered).model_dump(mode="json") == rendered.model_dump(mode="json")


def test_visible_prefill_indices_include_system_and_preserve_transcript():
    run = record_to_agent_run(record(), {})
    before = run.model_dump(mode="json")
    annotation = prefill_metadata(run)["prefill_annotation"]
    assert annotation["transcripts"] == [{"transcript_index": 0,
        "prefilled_message_indices": [1, 2, 3], "first_live_message_index": 4}]
    assert annotation["tags"] == ["Prefilled T0B1–T0B3; live starts T0B4 (0-based)"]
    assert run.model_dump(mode="json") == before


def test_visible_prefill_ranges_do_not_swallow_nonprefilled_messages():
    run = record_to_agent_run(record(), {})
    run.transcripts[0].messages[2].metadata = {"prefill": False}
    for message in run.transcripts[0].messages[4:]:
        message.metadata = {"prefill": False, "provenance": "environment_preview"}
    annotation = prefill_metadata(run)["prefill_annotation"]
    assert annotation["transcripts"][0]["prefilled_message_indices"] == [1, 3]
    assert annotation["transcripts"][0]["first_live_message_index"] is None
    assert annotation["tags"] == [
        "Prefilled T0B1; no live model output (0-based)",
        "Prefilled T0B3; no live model output (0-based)",
    ]


def test_upload_metadata_names_evaluee_and_environment_roles():
    """Docent metadata carries explicit evaluee / environment-model keys alongside the legacy `model`."""
    rec = record()
    rec["env_model_config"] = dict(name="env-y", model="vendor/y")
    rec["episode"]["env_model"] = "env-y"
    m = record_to_agent_run(rec, {}).metadata
    assert m["model_under_evaluation"] == "test" and m["evaluee"] == "test"
    assert m["evaluee_model_id"] == "test-id"
    assert m["environment_model"] == "env-y" and m["environment_model_id"] == "vendor/y"
    assert m["model"] == "test"
