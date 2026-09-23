"""Every Docent upload path shares its collection publicly (read-only)."""
from ai_collusion.docent_cli import ensure_public


class FakeClient:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def share_collection_with_public(self, collection_id, *, permission):
        self.calls.append((collection_id, permission))
        if self.fail:
            raise RuntimeError("403 admin permission required")


def test_ensure_public_shares_read_only():
    client = FakeClient()
    assert ensure_public(client, "abc") is True
    assert client.calls == [("abc", "read")]


def test_ensure_public_reports_failure_without_raising(capsys):
    client = FakeClient(fail=True)
    assert ensure_public(client, "abc") is False
    assert "could not make collection abc public" in capsys.readouterr().err


def test_cli_upload_makes_collection_public(monkeypatch, tmp_path):
    from ai_collusion import docent_cli

    class UploadClient(FakeClient):
        def list_agent_run_ids(self, cid):
            return []

        def add_agent_runs(self, cid, runs):
            pass

    client = UploadClient()
    monkeypatch.setattr(docent_cli, "make_client", lambda: client)
    monkeypatch.setattr(docent_cli, "load_run_dir", lambda d: ({}, []))
    monkeypatch.setattr("ai_collusion.docent_prefill.annotate_prefills", lambda *a, **k: [])
    monkeypatch.setattr("ai_collusion.docent_presentation.annotate_presentation", lambda *a, **k: None)
    assert docent_cli.main(["--run", str(tmp_path), "--collection-id", "cid-1"]) == 0
    assert client.calls == [("cid-1", "read")]
