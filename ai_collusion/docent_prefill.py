"""Docent display markers and provenance notes, separate from source records."""
from __future__ import annotations

from typing import Any


PREFILL_PREFIX = "[PREFILL]\n"
PREFILL_RENDERING_VERSION = "prefix-v1"


def render_prefill_message(message: Any) -> Any:
    """Copy an SDK message and mark supplied history for display only.

    Explicit version metadata distinguishes our marker from literal source text.
    Keeping all content inside the same message preserves live citation indices.
    """
    rendered = message.model_copy(deep=True)
    metadata = rendered.metadata or {}
    if metadata.get("prefill") is not True:
        return rendered
    if metadata.get("prefill_rendering") == PREFILL_RENDERING_VERSION:
        return rendered
    if isinstance(rendered.content, str):
        rendered.content = PREFILL_PREFIX + rendered.content
    else:
        from docent.data_models.chat.content import ContentText

        if rendered.content and isinstance(rendered.content[0], ContentText):
            rendered.content[0].text = PREFILL_PREFIX + rendered.content[0].text
        else:
            rendered.content = [ContentText(text=PREFILL_PREFIX), *(rendered.content or [])]
    rendered.metadata = {**metadata, "prefill_rendering": PREFILL_RENDERING_VERSION}
    return rendered


def _ranges(indices: list[int], transcript_index: int) -> str:
    groups: list[list[int]] = []
    for index in indices:
        if groups and index == groups[-1][-1] + 1:
            groups[-1].append(index)
        else:
            groups.append([index])
    return ", ".join(
        f"T{transcript_index}B{group[0]}" +
        (f"–T{transcript_index}B{group[-1]}" if len(group) > 1 else "")
        for group in groups
    )


def prefill_metadata(run: Any) -> dict:
    """Describe exact zero-based Docent message positions, including system messages."""
    transcripts = []
    tags = []
    for ti, transcript in enumerate(run.transcripts):
        prefilled = [i for i, message in enumerate(transcript.messages)
                     if (message.metadata or {}).get("prefill") is True]
        live = [i for i, message in enumerate(transcript.messages)
                if (message.metadata or {}).get("provenance") == "evaluated_model"]
        first_live = live[0] if live else None
        transcripts.append({"transcript_index": ti,
                            "prefilled_message_indices": prefilled,
                            "first_live_message_index": first_live})
        if prefilled:
            # Split ranges into separate short tags rather than exceed server tag limits.
            for span in _ranges(prefilled, ti).split(", "):
                suffix = (f"live starts T{ti}B{first_live}" if live else "no live model output")
                tags.append(f"Prefilled {span}; {suffix} (0-based)")
    return {"prefill_annotation": {
        "index_base": 0,
        "index_description": "Docent transcript message indices; system message is included. "
                             "Prefilled messages are supplied history, not evaluated-model behavior.",
        "transcripts": transcripts, "tags": tags,
    }}


def annotate_prefills(client: Any, collection_id: str,
                      agent_run_ids: list[str] | None = None) -> list[dict]:
    """Idempotently add native run tags and exact indices, verifying source preservation."""
    ids = client.list_agent_run_ids(collection_id) if agent_run_ids is None else agent_run_ids
    results = []
    for run_id in ids:
        run = client.get_agent_run(collection_id, run_id)
        if run is None:
            raise ValueError(f"Docent run disappeared: {run_id}")
        before = [t.model_dump(mode="json") for t in run.transcripts]
        prior_metadata = run.metadata.copy()
        annotation = prefill_metadata(run)
        tags = annotation["prefill_annotation"]["tags"]
        if not tags:
            results.append({"agent_run_id": run_id, "status": "no_prefill"})
            continue
        existing = {tag["value"] for tag in client.get_tags_for_agent_run(collection_id, run_id)}
        for tag in tags:
            if tag not in existing:
                client.tag_transcript(collection_id, run_id, tag)
        if run.metadata.get("prefill_annotation") != annotation["prefill_annotation"]:
            client.update_agent_run_metadata(collection_id, run_id, annotation)
        actual = client.get_agent_run(collection_id, run_id)
        if actual is None or [t.model_dump(mode="json") for t in actual.transcripts] != before:
            raise ValueError(f"Docent transcript changed while annotating {run_id}")
        for key, value in prior_metadata.items():
            if key != "prefill_annotation" and actual.metadata.get(key) != value:
                raise ValueError(f"Docent metadata changed while annotating {run_id}: {key}")
        if actual.metadata.get("prefill_annotation") != annotation["prefill_annotation"]:
            raise ValueError(f"Docent prefill metadata readback mismatch: {run_id}")
        readback_tags = {tag["value"] for tag in client.get_tags_for_agent_run(collection_id, run_id)}
        if not existing.union(tags).issubset(readback_tags):
            raise ValueError(f"Docent prefill tag readback mismatch: {run_id}")
        results.append({"agent_run_id": run_id, "status": "verified", "tags": tags})
    return results
