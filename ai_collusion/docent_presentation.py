"""Readable run summaries derived from existing Docent evidence and metadata."""
from __future__ import annotations

import json
import re


def display_label(value, config=None):
    """Neutral display labels; source identifiers stay intact for reproducibility."""
    value = re.sub(r"(?<![A-Za-z0-9])evil(?![A-Za-z0-9])", "failing", str(value))
    value = re.sub(r"(?<![A-Za-z0-9])harder(?![A-Za-z0-9])", "slow-data", value)
    probability = (config or {}).get("query_failure_probability")
    if type(probability) in (int, float) and 0 <= probability <= 1:
        value = re.sub(r"(?<![A-Za-z0-9])unreliable(?![A-Za-z0-9]|-\d+(?:\.\d+)?%)",
                       f"unreliable-{probability * 100:g}%", value)
    return value


def presentation(run) -> tuple[str, dict, list[str]]:
    metadata = run.metadata or {}
    episode = metadata.get("episode") or {}
    group = metadata.get("agent_group") or {}
    model = metadata.get("model", "unknown model")
    arm = metadata.get("arm_id") or metadata.get("condition", "unknown arm")
    config = (metadata.get("resolved_config") or {}).get("cut") or {}
    mode = episode.get("mode") or (metadata.get("arm") or {}).get("mode") or metadata.get("mode")
    environment = {"neutral": "working", "empty_success": "empty success",
                   "evil": "failing"}.get(mode, mode or "unknown")
    arm_base = str(arm).split("-")[0]          # working-warned / working-v2 families keep the base label
    if arm_base in {"working", "harder", "unreliable", "broken"}:
        environment = arm_base
    if str(arm).startswith("slow-data"):
        environment = "slow-data"
    environment = display_label(environment, config)
    arm = display_label(arm, config)
    prefilled = [message for transcript in run.transcripts for message in transcript.messages
                 if (message.metadata or {}).get("prefill") is True]
    wiki_read = any("--- GET https://wikiservice.at/" in json.dumps(m.content, default=str)
                    and "action=browse" in json.dumps(m.content, default=str)
                    for m in prefilled)
    hint = metadata.get("hint")
    exposure = "wiki prefill" if wiki_read else (
        f"search hint: {hint}" if hint and hint != "none" else "no wiki prefill")
    writes = metadata.get("wiki_write_instructions")
    write_label = {True: "write instructions on", False: "write instructions off"}.get(writes, "")
    interface = "native tools" if (metadata.get("extra_body") or {}).get("tools") else "text tools"
    turns, cap = episode.get("n_turns"), metadata.get("max_turns")
    rounds = episode.get("rounds") or []
    correct = sum(bool(r.get("correct")) for r in rounds)
    end = episode.get("end_reason") or metadata.get("finish_reason", "unknown")
    status = "provider error" if metadata.get("error") or metadata.get("provider_response_error") else end
    seed = metadata.get("seed")
    title = " | ".join(str(v) for v in [model, group.get("agent_id"), environment, exposure, write_label, interface,
                                        f"seed {seed}" if seed is not None else None] if v)
    config = "; ".join(str(v) for v in [arm, exposure, write_label,
        f"{cap} live turns" if cap is not None else None,
        f"{metadata['max_tokens']} tokens/turn" if metadata.get("max_tokens") is not None else None,
        f"{metadata['num_live_problems']} live problems" if metadata.get("num_live_problems") is not None else None] if v)
    summary = title
    if turns is not None:
        summary += f" | {turns}/{cap} turns" if cap is not None else f" | {turns} turns"
    if rounds:
        summary += f" | {correct}/{len(rounds)} correct"
    summary += f" | {status}"
    fields = {"summary": summary, "arm_config": config, "environment": environment,
              "wiki_exposure": exposure, "prefill_message_count": len(prefilled),
              "turns_used": turns, "rounds_correct": correct if rounds else None,
              "rounds_total": len(rounds) if rounds else None,
              "wiki_posts_count": episode.get("n_wiki_posts"),
              "run_status": status, "tool_interface": interface, "presentation_version": "run-summary-v3"}
    tags = [f"Model: {model}", f"Arm: {arm}", f"Exposure: {exposure}"]
    tags.append(f"Interface: {interface}")
    if write_label:
        tags.append(write_label.capitalize())
    return title, {k: v for k, v in fields.items() if v is not None}, tags


def annotate_presentation(client, collection_id: str, run_ids: list[str]) -> int:
    """Update metadata and visible tags without replacing runs or transcripts."""
    count = 0
    for run_id in run_ids:
        run = client.get_agent_run(collection_id, run_id)
        if run is None:  # The list endpoint can contain deleted IDs.
            continue
        _, fields, tags = presentation(run)
        if any(run.metadata.get(k) != v for k, v in fields.items()):
            client.update_agent_run_metadata(collection_id, run_id, fields)
        existing = {t["value"] for t in client.get_tags_for_agent_run(collection_id, run_id)}
        for tag in tags:
            if tag not in existing:
                client.tag_transcript(collection_id, run_id, tag)
        actual = client.get_agent_run(collection_id, run_id)
        if actual is None or any(actual.metadata.get(k) != v for k, v in fields.items()):
            raise ValueError(f"Presentation metadata readback mismatch: {run_id}")
        for key, value in run.metadata.items():
            if key not in fields and actual.metadata.get(key) != value:
                raise ValueError(f"Source metadata changed: {run_id}: {key}")
        if [t.model_dump(mode="json") for t in actual.transcripts] != [
                t.model_dump(mode="json") for t in run.transcripts]:
            raise ValueError(f"Transcript changed: {run_id}")
        count += 1
    return count
