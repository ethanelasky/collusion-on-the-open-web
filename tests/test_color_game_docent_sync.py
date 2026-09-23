"""Offline checks for safe, complete, public color-game transcript uploads."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
import requests

from experiments.color_game import docent, docent_sync as sync


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


def entry_with_pending(*, jobs=("job-1",)):
    return {
        "collection_id": "collection-1", "uploaded": {},
        "pending": {"agent_run_ids": ["run-1"], "job_ids": list(jobs),
                    "sources": {"rollout-1": {"agent_run_id": "run-1"}}},
    }


class Client:
    def __init__(self):
        self.remote_ids = []
        self.statuses = [{"job_id": "job-1", "status": "completed"}]
        self.add_calls = []
        self.shares = []
        self.updates = []
        self.before_add = None
        self.fail_add = False
        self.accept_before_failure = False

    def get_agent_run_job_statuses(self, collection_id, job_ids):
        return self.statuses

    def list_agent_run_ids(self, collection_id):
        return self.remote_ids[:]

    def share_collection_with_public(self, collection_id, *, permission):
        self.shares.append((collection_id, permission))

    def update_collection(self, collection_id, **kwargs):
        self.updates.append((collection_id, kwargs))

    def add_agent_runs(self, collection_id, runs, *, wait):
        assert wait is False
        self.add_calls.append((collection_id, list(runs)))
        if self.before_add:
            self.before_add(runs)
        if not self.fail_add or self.accept_before_failure:
            self.remote_ids.extend(run.id for run in runs)
        if self.fail_add:
            raise requests.ConnectionError("Connection lost before the receipt arrived")
        return {"job_ids": ["job-1"]}


@pytest.fixture
def campaign(tmp_path, monkeypatch):
    path = tmp_path / "runs"
    rollouts = {}
    outcomes = []
    converted = []
    verifications = []

    def add(key, status="complete", *, pending=0, artifact=True, rollout_status=None):
        source = path / key / "rollout.json"
        value = {"rollout_id": key, "status": rollout_status or status,
                 "summary": {"pending_responses": pending},
                 "rounds": [{"match": False, "error": {"type": "TestError"}}]}
        outcome = {"job_id": key, "status": status, "artifact_paths": {}}
        if artifact:
            dump(source, value)
            rollouts[str(source)] = value
            outcome["artifact_paths"]["json"] = str(source)
        outcomes.append(outcome)
        return outcome, value

    manifest = {"campaign_id": "campaign-1", "status": "running",
                "model": {"name": "test-model", "model": "test-model-id"},
                "summary": {"completed_rollouts": 0}, "outcomes": outcomes}

    def persist(status="running"):
        manifest["status"] = status
        manifest["summary"]["completed_rollouts"] = sum(
            outcome["status"] in sync.TERMINAL for outcome in outcomes)
        dump(path / "campaign.json", manifest)

    def convert(rollout, *, campaign_metadata):
        from docent.data_models import AgentRun, Transcript
        from docent.data_models.chat import parse_chat_message

        converted.append((deepcopy(rollout), deepcopy(campaign_metadata)))
        return AgentRun(name=rollout["rollout_id"], metadata=deepcopy(rollout),
                        transcripts=[Transcript(messages=[parse_chat_message(
                            {"role": "assistant", "content": "saved response"})])])

    def verify(client, collection_id, agent_run_id=None):
        verifications.append((collection_id, agent_run_id))
        return {"anonymous": True, "permission": "read", "sample_agent_run_id": agent_run_id}

    monkeypatch.setattr(sync, "load_rollout", lambda source: deepcopy(rollouts[str(source)]))
    monkeypatch.setattr(sync, "resolve_collection_id", lambda client, name: "collection-1")
    monkeypatch.setattr(sync, "verify_public", verify)
    monkeypatch.setattr(docent, "rollout_to_agent_run", convert)
    return path, add, persist, converted, verifications


def test_comparison_uses_current_campaigns_and_deduplicates_paths(tmp_path):
    spec = dump(tmp_path / "comparison.json", {
        "models": [{"directory": "replacement"}, {"directory": "second"}],
        "superseded_attempts": [{"directory": "old-rate-limited"}],
    })
    replacement = tmp_path / "replacement" / "runs"
    assert sync.campaign_paths(spec, [replacement]) == [
        str(replacement.resolve()), str((tmp_path / "second" / "runs").resolve())]


def test_fresh_campaigns_with_same_directory_get_distinct_collection_names(campaign, monkeypatch):
    path, _, persist, _, _ = campaign
    persist()
    names = []

    def resolve(client, name):
        names.append(name)
        return "collection-1"

    monkeypatch.setattr(sync, "resolve_collection_id", resolve)
    client = Client()
    sync.sync_campaign(client, path, {}, lambda: None, batch_size=10)
    manifest = json.loads((path / "campaign.json").read_text())
    manifest["campaign_id"] = "fresh-confirmation-campaign"
    manifest["selected_settings"] = ["async_counter"]
    dump(path / "campaign.json", manifest)
    sync.sync_campaign(client, path, {}, lambda: None, batch_size=10)
    assert names[0] != names[1]
    assert "campaign-1" in names[0]
    assert "fresh-confirmation-campaign" in names[1]
    description = client.updates[-1][1]["description"]
    assert "Settings: async_counter." in description
    assert "guessing_only" not in description
    assert "sync_counter," not in description


def test_settled_selection_keeps_failed_games_but_defers_running_and_late_replies(campaign):
    path, add, persist, _, _ = campaign
    for status in ("complete", "complete_with_errors", "failed", "interrupted"):
        add(status, status)
    add("late", "complete_pending_responses", pending=1)
    add("still-running", "running")
    add("queued", "queued")
    add("manifest-ahead", "complete", rollout_status="running")
    persist()
    selected = list(sync.settled_rollouts(sync.read(path / "campaign.json")))
    assert [row[2]["rollout_id"] for row in selected] == [
        "complete", "complete_with_errors", "failed", "interrupted"]
    assert all(row[2]["rounds"][0]["match"] is False for row in selected)
    assert all(row[2]["rounds"][0]["error"] for row in selected)


def test_missing_failed_artifact_does_not_block_later_saved_transcripts(campaign):
    path, add, persist, _, _ = campaign
    add("failed-before-save", "failed", artifact=False)
    add("saved")
    outcome, _ = add("deleted", "failed")
    Path(outcome["artifact_paths"]["json"]).unlink()
    persist()
    assert [row[2]["rollout_id"] for row in sync.settled_rollouts(
        sync.read(path / "campaign.json"))] == ["saved"]


def test_complete_pending_can_upload_after_saved_provider_reply_is_resolved(campaign):
    path, add, persist, _, _ = campaign
    add("resolved", "complete_pending_responses", pending=0, rollout_status="complete_with_errors")
    persist()
    assert [row[2]["rollout_id"] for row in sync.settled_rollouts(
        sync.read(path / "campaign.json"))] == ["resolved"]


@pytest.mark.parametrize("status", ["pending", "running"])
def test_reconcile_defers_unfinished_ingestion_without_resubmitting(status):
    client = Client()
    client.statuses[0]["status"] = status
    entry = entry_with_pending()
    saved = []
    assert sync._reconcile(client, entry, lambda: saved.append(1)) is False
    assert entry["pending"] and not entry["uploaded"]
    assert not saved and not client.add_calls


@pytest.mark.parametrize("status", ["failed", "canceled"])
def test_reconcile_preserves_receipt_when_ingestion_fails(status):
    client = Client()
    client.statuses[0]["status"] = status
    entry = entry_with_pending()
    original = deepcopy(entry)
    with pytest.raises(RuntimeError, match="ingestion failed"):
        sync._reconcile(client, entry, lambda: None)
    assert entry == original
    assert not client.add_calls


def test_completed_ingestion_needs_remote_run_confirmation():
    entry = entry_with_pending()
    assert sync._reconcile(Client(), entry, lambda: None) is False
    assert "pending" in entry and not entry["uploaded"]


@pytest.mark.parametrize("jobs", [(), ("job-1",)])
def test_reconcile_recovers_saved_ids_without_uploading_again(jobs):
    client = Client()
    client.remote_ids = ["run-1"]
    entry = entry_with_pending(jobs=jobs)
    saved = []
    assert sync._reconcile(client, entry, lambda: saved.append(deepcopy(entry))) is True
    assert entry["uploaded"] == {"rollout-1": {"agent_run_id": "run-1"}}
    assert "pending" not in entry and len(saved) == 1
    assert not client.add_calls


def test_ambiguous_unconfirmed_upload_is_never_resubmitted():
    client = Client()
    entry = entry_with_pending(jobs=())
    original = deepcopy(entry)
    with pytest.raises(RuntimeError, match="Uncertain upload receipt"):
        sync._reconcile(client, entry, lambda: None)
    assert entry == original and not client.add_calls


def test_sync_uploads_errors_once_and_saves_receipt_before_request(campaign):
    path, add, persist, converted, verifications = campaign
    add("wrong-answer")
    add("failed-api", "complete_with_errors")
    add("partial", "interrupted")
    persist("complete_with_errors")
    client, entry, snapshots = Client(), {}, []

    def save():
        snapshots.append(deepcopy(entry))

    def before_add(runs):
        assert snapshots[-1]["pending"]["agent_run_ids"] == [run.id for run in runs]
        assert snapshots[-1]["pending"]["job_ids"] == []

    client.before_add = before_add
    sync.sync_campaign(client, path, entry, save, batch_size=2)
    assert len(client.add_calls) == 2
    assert set(entry["uploaded"]) == {"wrong-answer", "failed-api", "partial"}
    assert [value[0]["status"] for value in converted] == [
        "complete", "complete_with_errors", "interrupted"]
    assert all(value[0]["rounds"][0]["error"] for value in converted)
    assert all(len(value[1]["source_sha256"]) == 64 for value in converted)
    assert client.shares == [("collection-1", "read")]
    assert verifications[0] == ("collection-1", None)
    assert verifications[-1][1] in client.remote_ids
    assert entry["finished"] is True
    sync.sync_campaign(client, path, entry, save, batch_size=2)
    assert len(client.add_calls) == 2
    assert len(converted) == 3


def test_terminal_campaign_with_missing_artifact_is_not_reported_fully_uploaded(campaign):
    path, add, persist, _, _ = campaign
    add("failed-before-save", "failed", artifact=False)
    add("saved")
    persist("complete_with_errors")
    client, entry = Client(), {}
    sync.sync_campaign(client, path, entry, lambda: None, batch_size=10)
    assert set(entry["uploaded"]) == {"saved"}
    assert entry["planned"] == 2 and entry["finished"] is False


def test_failed_public_verification_stops_before_upload(campaign, monkeypatch):
    path, add, persist, _, _ = campaign
    add("saved")
    persist()

    def inaccessible(*args, **kwargs):
        raise requests.HTTPError("Anonymous read denied")

    monkeypatch.setattr(sync, "verify_public", inaccessible)
    client, entry = Client(), {}
    with pytest.raises(requests.HTTPError):
        sync.sync_campaign(client, path, entry, lambda: None, batch_size=10)
    assert client.shares == [("collection-1", "read")]
    assert not client.add_calls and "public_verification" not in entry


@pytest.mark.parametrize("accepted", [False, True])
def test_connection_loss_retains_ids_and_does_not_send_same_batch_again(campaign, accepted):
    path, add, persist, _, _ = campaign
    add("saved")
    persist("complete")
    client, entry = Client(), {}
    client.fail_add = True
    client.accept_before_failure = accepted
    with pytest.raises(requests.ConnectionError):
        sync.sync_campaign(client, path, entry, lambda: None, 10)
    pending = deepcopy(entry["pending"])
    assert pending["agent_run_ids"] and pending["job_ids"] == []
    if accepted:
        sync.sync_campaign(client, path, entry, lambda: None, 10)
        assert entry["finished"] and "pending" not in entry
    else:
        with pytest.raises(RuntimeError, match="Uncertain upload receipt"):
            sync.sync_campaign(client, path, entry, lambda: None, 10)
        assert entry["pending"] == pending
    assert len(client.add_calls) == 1


def test_pending_upload_stops_new_batches_at_poll_deadline(campaign, monkeypatch):
    path, add, persist, _, _ = campaign
    add("first")
    add("second")
    persist()
    client, entry = Client(), {}
    client.statuses[0]["status"] = "running"
    clock = iter([0, 46])
    monkeypatch.setattr(sync.time, "monotonic", lambda: next(clock))
    sync.sync_campaign(client, path, entry, lambda: None, batch_size=1)
    assert len(client.add_calls) == 1
    assert set(entry["pending"]["sources"]) == {"first"}
    assert not entry["uploaded"]


def test_detected_credential_prevents_public_upload(campaign, monkeypatch):
    path, add, persist, _, _ = campaign
    _, rollout = add("secret-in-artifact")
    secret = "test-only-credential-do-not-upload-123456"
    rollout["provider_response"] = secret
    monkeypatch.setenv("DOCENT_SYNC_TEST_API_KEY", secret)
    persist()
    client, entry = Client(), {}
    with pytest.raises(ValueError, match="credential"):
        sync.sync_campaign(client, path, entry, lambda: None, 10)
    assert not client.add_calls and "pending" not in entry


class Response:
    def __init__(self, payload, failure=None):
        self.payload = payload
        self.failure = failure

    def raise_for_status(self):
        if self.failure:
            raise self.failure

    def json(self):
        return self.payload


def public_client(acl):
    class PublicClient:
        _api_url = "https://example.invalid/rest"

        def get_collection_collaborators(self, collection_id):
            return acl

    return PublicClient()


@pytest.mark.parametrize("acl", [[], [{"subject_type": "user", "permission_level": "read"}],
                                  [{"subject_type": "public", "permission_level": "write"}]])
def test_public_verification_requires_explicit_public_read_acl(acl, monkeypatch):
    def unexpected_session():
        pytest.fail("Do not attempt an anonymous read when the ACL is wrong")

    monkeypatch.setattr(sync.requests, "Session", unexpected_session)
    with pytest.raises(ValueError, match="public read"):
        sync.verify_public(public_client(acl), "collection-1")


@pytest.mark.parametrize("failure", [None, "wrong_collection", "wrong_run", "empty_transcript", "http"])
def test_anonymous_verification_reads_collection_and_real_transcript(monkeypatch, failure):
    calls = []

    class AnonymousSession:
        def __init__(self):
            self.trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, **kwargs):
            assert self.trust_env is False, "Anonymous reads must not attach .netrc credentials"
            assert "headers" not in kwargs and "auth" not in kwargs
            calls.append((url, kwargs))
            if url.endswith("collection_details"):
                return Response({"id": "different" if failure == "wrong_collection" else "collection-1"},
                                requests.HTTPError("401") if failure == "http" else None)
            return Response({"id": "different" if failure == "wrong_run" else "run-1",
                             "transcripts": [] if failure == "empty_transcript" else [{"messages": []}]})

    monkeypatch.setattr(sync.requests, "Session", AnonymousSession)
    client = public_client([{"subject_type": "public", "permission_level": "read"}])
    if failure:
        with pytest.raises((ValueError, requests.HTTPError)):
            sync.verify_public(client, "collection-1", "run-1")
    else:
        result = sync.verify_public(client, "collection-1", "run-1")
        assert result["anonymous"] is True and result["permission"] == "read"
        assert result["sample_agent_run_id"] == "run-1"
        assert calls == [
            ("https://example.invalid/rest/collection-1/collection_details", {"timeout": 45}),
            ("https://example.invalid/rest/collection-1/agent_run",
             {"params": {"agent_run_id": "run-1"}, "timeout": 45}),
        ]
