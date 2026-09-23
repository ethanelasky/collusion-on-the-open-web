"""Attach validated offline judgments to existing, exactly matched Docent runs."""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .docent_cli import make_client, record_to_agent_run
from .docent_prefill import render_prefill_message


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def _read_ledger(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _append_ledger(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        stream.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _message_content(message: Any) -> dict:
    value = message.model_dump(mode="json")
    # Message IDs are assigned by Docent, and are not source content.
    return {key: value.get(key) for key in ("role", "content", "model", "tool_calls", "tool_call_id")}


def _verify_and_map(actual: Any, expected: Any, record: dict, judgment: dict) -> dict:
    """Match an exact historical or prefixed display against the raw source view."""
    for key in ("context_sha256", "experiment_sha256", "live_start_message_index", "task_id", "arm_id"):
        if key in expected.metadata and actual.metadata.get(key) != expected.metadata[key]:
            raise ValueError(f"Docent source metadata mismatch: {key}")
    if len(actual.transcripts) != 1 or len(expected.transcripts) != 1:
        raise ValueError("expected exactly one source transcript")
    transcript, reference = actual.transcripts[0], expected.transcripts[0]
    if len(transcript.messages) != len(reference.messages):
        raise ValueError("Docent transcript message count differs from source")
    live_indices = []
    possible_displays = {"historical", "prefixed"}
    for index, (message, wanted) in enumerate(zip(transcript.messages, reference.messages)):
        metadata = message.metadata or {}
        for key in ("prefill", "provenance", "source"):
            if metadata.get(key) != (wanted.metadata or {}).get(key):
                raise ValueError(f"Docent live boundary/provenance differs at message {index}")
        matching_displays = set()
        for display, candidate in (("historical", wanted),
                                   ("prefixed", render_prefill_message(wanted))):
            display_metadata = candidate.metadata or {}
            if (_message_content(message) == _message_content(candidate)
                    and ("prefill_rendering" in metadata) == ("prefill_rendering" in display_metadata)
                    and metadata.get("prefill_rendering") == display_metadata.get("prefill_rendering")):
                matching_displays.add(display)
        possible_displays &= matching_displays
        if not possible_displays:
            raise ValueError(f"Docent transcript content or rendering differs at message {index}")
        if (wanted.metadata or {}).get("provenance") == "evaluated_model":
            live_indices.append(index)
    turns = record.get("episode", {}).get("turns", [])
    if len(live_indices) != len(turns):
        raise ValueError("source live turn count does not match transcript")
    mapping = {}
    for turn, index in zip(turns, live_indices):
        turn_id = turn.get("turn")
        if type(turn_id) is not int or turn_id <= 0 or turn_id in mapping:
            raise ValueError("invalid or duplicate source turn ID")
        mapping[turn_id] = (turn, index)
    annotated = copy.deepcopy(judgment)
    for event in annotated["evidence"]:
        for evidence in event["quotes"]:
            turn, index = mapping[evidence["turn"]]
            field = evidence["field"]
            if field not in ("text", "reasoning", "result"):
                raise ValueError(f"unknown evidence field: {field}")
            content = turn.get("result") if field == "result" else (turn.get("response") or {}).get(field)
            quote = evidence["quote"]
            if not isinstance(quote, str) or not quote or not isinstance(content, str) or quote not in content:
                raise ValueError("evidence quote does not match exact source turn")
            evidence.update(transcript_id=transcript.id, message_index=index + (field == "result"))
    return annotated


def upload_judgments(envelopes: list[dict], collection_id: str, ledger_path: str | Path,
                     *, agent_run_ids: list[str] | None = None) -> list[dict]:
    """Deep-merge annotations; never upload, replace, or delete source transcripts.

    Returns one result per envelope. A ``failed`` result is retryable and must
    make the calling CLI fail. The append-only JSONL ledger records actual IDs.
    Message indices in annotations are zero-based positions in the transcript.
    """
    ledger_path = Path(ledger_path)
    ledger = _read_ledger(ledger_path)
    successes = {row["identity"]: row for row in ledger if row.get("status") in ("uploaded", "skipped")}
    results = []
    client = None
    existing = None
    for envelope in envelopes:
        result = {"source_path": envelope.get("source_path"), "collection_id": collection_id}
        if envelope.get("error") or not envelope.get("judgment"):
            results.append({**result, "status": "skipped_invalid"})
            continue
        try:
            version = f"{_hash(envelope['schema_version'])}_{envelope['rubric_sha256']}_{_hash(envelope['judge_config'])}"
            identity = _hash({"collection_id": collection_id, "source_path": envelope["source_path"],
                              "source_sha256": envelope["source_sha256"], "version": version,
                              "schema_version": envelope.get("schema_version"), "judgment": envelope["judgment"]})
            result.update(identity=identity, version=version)
            source_path = Path(envelope["source_path"])
            source_bytes = source_path.read_bytes()
            if hashlib.sha256(source_bytes).hexdigest() != envelope["source_sha256"]:
                raise ValueError("source bytes changed since judging")
            if identity in successes:
                results.append({**result, "status": "skipped", "agent_run_id": successes[identity]["agent_run_id"]})
                continue
            record = json.loads(source_bytes)
            record["_file"] = source_path.name
            manifest = json.loads((source_path.parent / "manifest.json").read_text())
            # Derive both display candidates from source bytes, never by removing
            # prefixes from the uploaded transcript. Live messages stay exact.
            expected = record_to_agent_run(record, manifest, render_prefill=False)
            keys = ("run_id", "source_file", "model", "condition", "sample_index")
            if any(expected.metadata.get(key) is None for key in keys):
                raise ValueError("source is missing required run identity fields")
            if client is None:
                client = make_client()
            if existing is None:
                # Newly uploaded runs can be directly readable before the
                # collection query index includes them. A verified upload
                # ledger supplies their exact IDs; source checks below remain.
                ids = (client.list_agent_run_ids(collection_id) if agent_run_ids is None
                       else list(dict.fromkeys(agent_run_ids)))
                with ThreadPoolExecutor(max_workers=8) as pool:
                    existing = list(pool.map(lambda run_id: client.get_agent_run(collection_id, run_id), ids))
            matches = [run for run in existing if run is not None and
                       all(run.metadata.get(key) == expected.metadata[key] for key in keys)]
            if len(matches) != 1:
                raise ValueError(f"expected unique existing Docent run; found {len(matches)}")
            actual = matches[0]
            result["agent_run_id"] = actual.id
            judgment = _verify_and_map(actual, expected, record, envelope["judgment"])
            annotation = {"source_sha256": envelope["source_sha256"],
                          "schema_version": envelope.get("schema_version"),
                          "rubric_version": envelope.get("rubric_version"),
                          "rubric_sha256": envelope["rubric_sha256"],
                          "judge_config": envelope["judge_config"],
                          "input_sha256": envelope.get("input_sha256"),
                          "judgment": judgment}
            prior = actual.metadata.get("collaboration_judgments", {}).get(version)
            if prior is not None and prior != annotation:
                raise ValueError("different annotation already exists for this rubric/judge version")
            if prior is None:
                client.update_agent_run_metadata(collection_id, actual.id,
                                                 {"collaboration_judgments": {version: annotation}})
                actual.metadata.setdefault("collaboration_judgments", {})[version] = annotation
            result["status"] = "uploaded" if prior is None else "skipped"
        except Exception as exc:
            result.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        result["timestamp"] = datetime.now(timezone.utc).isoformat()
        _append_ledger(ledger_path, result)
        if result["status"] in ("uploaded", "skipped"):
            successes[result["identity"]] = result
        results.append(result)
    return results
