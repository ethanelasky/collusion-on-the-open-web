"""Exercise the real SDK writer shape without making remote mutations."""
from copy import deepcopy
import hashlib
import json

import pytest

pytest.importorskip("docent")

from ai_collusion.docent_cli import record_to_agent_run
from ai_collusion import judge_docent
from test_judge import citation, judgment, record


class MetadataClient:
    """Only the three SDK operations authorized by the adapter contract exist."""
    def __init__(self, runs):
        self.runs = {run.id: run for run in runs}
        self.updates = []
        self.fail = False

    def list_agent_run_ids(self, collection_id):
        return list(self.runs)

    def get_agent_run(self, collection_id, agent_run_id):
        return self.runs[agent_run_id]

    def update_agent_run_metadata(self, collection_id, agent_run_id, metadata):
        if self.fail:
            raise OSError("temporary metadata update failure")
        self.updates.append((agent_run_id, deepcopy(metadata)))
        def merge(target, patch):
            for key, value in patch.items():
                if isinstance(value, dict) and isinstance(target.get(key), dict):
                    merge(target[key], value)
                else:
                    target[key] = deepcopy(value)
        merge(self.runs[agent_run_id].metadata, metadata)
        return self.runs[agent_run_id].metadata


def fixture(tmp_path, *, legacy=False, literal_prefix=False):
    rec = record()
    if literal_prefix:
        rec["context"]["messages"][0]["content"] = "[PREFILL]\nThis marker is part of the source."
    source = tmp_path / "sample.json"
    source.write_text(json.dumps(rec))
    (tmp_path / "manifest.json").write_text('{}')
    run = record_to_agent_run(rec, {}, render_prefill=not legacy)
    run.metadata["unrelated"] = {"keep": [1, 2]}
    payload = judgment()
    payload["labels"] = {"help": "present", "coordinate": "present"}
    payload["evidence"][0]["labels"].append("coordinate")
    payload["evidence"][0]["quotes"].extend([
        citation(turn=9, quote="Finished"),
        citation(field="reasoning", quote="They need tomorrow's question."),
        citation(field="result", quote="Saved successfully")])
    payload["evidence"][0]["stage"] = "execution"
    envelope = {"source_path": str(source), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "schema_version": "test", "rubric_sha256": "rubric-sha", "rubric_version": "test", "judge_config": {"model": "fixture"},
                "input_sha256": "projection-sha", "judgment": payload, "error": None}
    return run, envelope, tmp_path / "uploads.jsonl"


@pytest.mark.parametrize("legacy", [False, True])
def test_real_sdk_evidence_indices_use_ordinal_and_update_preserves_content(tmp_path, monkeypatch, legacy):
    run, envelope, ledger = fixture(tmp_path, legacy=legacy)
    run = type(run).model_validate(run.model_dump(mode="json"))
    before = run.model_dump(mode="json")
    client = MetadataClient([run])
    monkeypatch.setattr(judge_docent, "make_client", lambda: client)
    result = judge_docent.upload_judgments([envelope], "collection", ledger)
    assert result[0]["status"] == "uploaded"
    assert len(client.updates) == 1
    patch = client.updates[0][1]
    assert set(patch) == {"collaboration_judgments"}
    annotation = next(iter(patch["collaboration_judgments"].values()))
    mapped_event = annotation["judgment"]["evidence"][0]
    assert mapped_event["labels"] == ["help", "coordinate"]
    assert mapped_event["stage"] == "execution"
    assert annotation["judgment"]["labels"] == envelope["judgment"]["labels"]
    evidence = annotation["judgment"]["evidence"][0]["quotes"]
    assert [entry["message_index"] for entry in evidence] == [3, 5, 3, 4]
    assert {entry["transcript_id"] for entry in evidence} == {run.transcripts[0].id}
    for mapped, original in zip(evidence, envelope["judgment"]["evidence"][0]["quotes"]):
        assert {key: mapped[key] for key in original} == original
    after = run.model_dump(mode="json")
    assert after["transcripts"] == before["transcripts"]
    for key, value in before["metadata"].items():
        assert after["metadata"][key] == value
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert rows[0]["agent_run_id"] == run.id
    monkeypatch.setattr(judge_docent, "make_client", lambda: pytest.fail("successful ledger should skip remote access"))
    assert judge_docent.upload_judgments([envelope], "collection", ledger)[0]["status"] == "skipped"


@pytest.mark.parametrize("legacy", [False, True])
def test_literal_source_prefill_marker_is_preserved_and_judgeable(tmp_path, monkeypatch, legacy):
    run, envelope, ledger = fixture(tmp_path, legacy=legacy, literal_prefix=True)
    content = run.transcripts[0].messages[1].content
    assert content == ("" if legacy else "[PREFILL]\n") + "[PREFILL]\nThis marker is part of the source."
    source_before = (tmp_path / "sample.json").read_bytes()
    client = MetadataClient([run])
    monkeypatch.setattr(judge_docent, "make_client", lambda: client)
    assert judge_docent.upload_judgments([envelope], "collection", ledger)[0]["status"] == "uploaded"
    assert (tmp_path / "sample.json").read_bytes() == source_before
    assert run.transcripts[0].messages[1].content == content


@pytest.mark.parametrize("mismatch", ["live_prefix", "unknown_version", "missing_version", "missing_prefix",
                                      "mixed_display", "system_version", "literal_prefix_removed"])
def test_display_marker_never_weakens_exact_source_verification(tmp_path, monkeypatch, mismatch):
    run, envelope, ledger = fixture(tmp_path, literal_prefix=mismatch == "literal_prefix_removed")
    messages = run.transcripts[0].messages
    if mismatch == "live_prefix":
        messages[3].content[-1].text = "[PREFILL]\n" + messages[3].content[-1].text
    elif mismatch == "unknown_version":
        messages[1].metadata["prefill_rendering"] = "unknown-version"
    elif mismatch == "missing_version":
        messages[1].metadata.pop("prefill_rendering")
    elif mismatch in ("missing_prefix", "literal_prefix_removed"):
        messages[1].content = messages[1].content.removeprefix("[PREFILL]\n")
    elif mismatch == "mixed_display":
        messages[1].content = messages[1].content.removeprefix("[PREFILL]\n")
        messages[1].metadata.pop("prefill_rendering")
    elif mismatch == "system_version":
        messages[0].metadata["prefill_rendering"] = "prefix-v1"
    client = MetadataClient([run])
    monkeypatch.setattr(judge_docent, "make_client", lambda: client)
    assert judge_docent.upload_judgments([envelope], "collection", ledger)[0]["status"] == "failed"
    assert client.updates == []


@pytest.mark.parametrize("mismatch", ["absent", "ambiguous", "content", "prefill"])
def test_metadata_match_does_not_override_source_identity_or_transcript(tmp_path, monkeypatch, mismatch):
    run, envelope, ledger = fixture(tmp_path)
    runs = [run]
    if mismatch == "absent":
        run.metadata["run_id"] = "different-run"
    elif mismatch == "ambiguous":
        runs.append(record_to_agent_run(record(), {}))
    elif mismatch == "content":
        run.transcripts[0].messages[1].content = "modified prefill"
    else:
        run.transcripts[0].messages[3].metadata["prefill"] = True
    client = MetadataClient(runs)
    monkeypatch.setattr(judge_docent, "make_client", lambda: client)
    result = judge_docent.upload_judgments([envelope], "collection", ledger)
    assert result[0]["status"] == "failed"
    assert client.updates == []


def test_failed_metadata_update_remains_retryable(tmp_path, monkeypatch):
    run, envelope, ledger = fixture(tmp_path)
    client = MetadataClient([run])
    client.fail = True
    monkeypatch.setattr(judge_docent, "make_client", lambda: client)
    assert judge_docent.upload_judgments([envelope], "collection", ledger)[0]["status"] == "failed"
    failed_bytes = ledger.read_bytes()
    client.fail = False
    assert judge_docent.upload_judgments([envelope], "collection", ledger)[0]["status"] == "uploaded"
    assert ledger.read_bytes().startswith(failed_bytes)
    assert len(client.updates) == 1


def test_upload_ledger_ids_work_when_collection_index_is_stale(tmp_path, monkeypatch):
    run, envelope, ledger = fixture(tmp_path)
    client = MetadataClient([run])
    monkeypatch.setattr(client, 'list_agent_run_ids', lambda _: [])
    monkeypatch.setattr(judge_docent, 'make_client', lambda: client)
    assert judge_docent.upload_judgments([envelope], 'collection', ledger)[0]['status'] == 'failed'
    failed_bytes = ledger.read_bytes()
    assert judge_docent.upload_judgments([envelope], 'collection', ledger,
                                        agent_run_ids=[run.id])[0]['status'] == 'uploaded'
    assert ledger.read_bytes().startswith(failed_bytes)
    assert len(client.updates) == 1
