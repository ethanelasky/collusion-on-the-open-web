"""Offline checks for the manifest-only campaign report."""
import copy
import csv
import io
import json
import os
from pathlib import Path

import pytest

from experiments.color_game.campaign_report import update_report
from experiments.color_game.config import SETTINGS


def manifest(root, repeats=50):
    outcomes = []
    for repeat in range(repeats):
        for setting in SETTINGS:
            job_id = f"{len(outcomes):04d}-{setting}-r{repeat:04d}"
            outcomes.append({
                "job_id": job_id, "setting": setting, "rollout_index": repeat,
                "status": "queued", "config": {"rounds": 5}, "summary": {},
                "progress": {"rounds_completed": 0, "actions_completed": 0},
                "artifact_paths": {"plan": str(root / "plans" / f"{job_id}.json")}, "error": None,
            })
    return {
        "schema": "color-game-campaign/v1", "campaign_id": "campaign-test", "status": "running",
        "model": {"name": "gpt-test", "extra_body": {"private": "DO_NOT_COPY"}},
        "output_dir": str(root), "rollouts_per_setting": repeats, "max_parallel_rollouts": 50,
        "updated_utc": "2026-09-12T00:00:00+00:00", "outcomes": outcomes, "callback_errors": [],
    }


def finish(outcome, *, matches=3):
    outcome.update(status="complete", summary={
        "recorded_rounds": 5, "matched": matches, "valid_rounds": 5, "valid_matches": matches,
        "total_actions": 10, "requests_started": 10, "model_api_errors": 0,
        "infrastructure_errors": 0, "missing_choices": 0,
    }, progress={"rounds_completed": 5, "actions_completed": 10, "last_event": "settled"})


def test_150_jobs_three_settings_and_partial_scores(tmp_path):
    campaign = manifest(tmp_path)
    completed, running, failed = campaign["outcomes"][:3]
    finish(completed)
    completed["summary"].update(valid_rounds=4, valid_matches=2, infrastructure_errors=1, missing_choices=1)
    completed["status"] = "complete_with_errors"
    running.update(status="running", progress={"rounds_completed": 1, "actions_completed": 6,
                                               "requests_started": 8, "model_api_errors": 1,
                                               "round_index": 1, "last_event": "request"})
    failed.update(status="failed", error={"category": "fatal_model_api", "type": "CampaignModelError"})
    original = copy.deepcopy(campaign)
    report = update_report(tmp_path, campaign)
    assert campaign == original
    assert report["summary"]["planned_rollouts"] == 150
    assert report["summary"]["queued_rollouts"] == 147
    assert report["summary"]["completed_rollouts"] == report["summary"]["running_rollouts"] == 1
    assert report["summary"]["failed_rollouts"] == 1
    assert report["summary"]["planned_rounds"] == 750
    assert report["summary"]["valid_accuracy"] == 0.5
    assert report["summary"]["matched"] == 3
    assert report["summary"]["requests_started"] == 18
    assert report["summary"]["model_api_errors"] == 1
    assert report["summary"]["missing_choices"] == 1
    assert report["summary"]["actions_completed"] == 16
    assert report["summary"]["partial"] is True
    assert list(report["settings"]) == list(SETTINGS)
    assert all(s["planned_rollouts"] == 50 and s["planned_rounds"] == 250 for s in report["settings"].values())
    page = (tmp_path / "index.html").read_text()
    assert "50.0% (2/4)" in page and "3/250" in page and "Partial results" in page
    assert '<meta http-equiv="refresh" content="5">' in page
    assert "DO_NOT_COPY" not in json.dumps(report)
    assert json.loads((tmp_path / "progress.json").read_text()) == report
    rows = list(csv.DictReader(io.StringIO((tmp_path / "rollouts.csv").read_text())))
    assert len(rows) == 150
    assert rows[0]["valid_accuracy"] == "0.5"
    assert rows[1]["requests_started"] == "8"


def test_only_manifest_read_and_portable_links_after_copy(tmp_path, monkeypatch):
    original_root = Path("/old/computer/color campaign")
    campaign = manifest(original_root, repeats=1)
    job = campaign["outcomes"][0]
    job["artifact_paths"].update(
        html=str(original_root / "a b" / "transcript.html"),
        json=str(original_root / "a b" / "rollout.json"),
        jsonl=str(original_root / "a b" / "events.jsonl"),
    )
    (tmp_path / "campaign.json").write_text(json.dumps(campaign))
    reads = []
    read_text = Path.read_text

    def guarded_read(path, *args, **kwargs):
        reads.append(path)
        assert path == tmp_path / "campaign.json", "The report must not load any transcript"
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read)
    report = update_report(tmp_path)
    assert reads == [tmp_path / "campaign.json"]
    assert report["rollouts"][0]["links"]["html"] == "a%20b/transcript.html"
    page = read_text(tmp_path / "index.html")
    assert 'href="a%20b/rollout.json"' in page
    assert "/old/computer" not in page
    assert not (tmp_path / "a b").exists()


def test_html_escape_and_reject_external_or_parent_links(tmp_path):
    campaign = manifest(tmp_path, repeats=1)
    injection = '<script src="https://evil.invalid/x">&</script>'
    campaign["model"]["name"] = campaign["campaign_id"] = injection
    outcome = campaign["outcomes"][0]
    outcome.update(job_id=injection, status=injection, error={"type": injection})
    outcome["progress"]["last_event"] = injection
    outcome["artifact_paths"] = {
        "html": "javascript:alert(1)", "json": "https://evil.invalid/x", "jsonl": "../secret", "plan": "/outside/secret",
    }
    report = update_report(tmp_path, campaign)
    assert report["rollouts"][0]["links"] == {}
    page = (tmp_path / "index.html").read_text()
    assert "<script" not in page
    assert "&lt;script src=&quot;https://evil.invalid/x&quot;&gt;" in page
    assert 'href="https:' not in page and 'href="javascript:' not in page


def test_complete_reports_stop_refresh_and_do_not_call_unknown_cost_zero(tmp_path):
    campaign = manifest(tmp_path, repeats=1)
    for outcome in campaign["outcomes"]:
        finish(outcome)
    campaign["status"] = "complete"
    campaign["outcomes"][0]["summary"]["cost"] = 0.125
    campaign["outcomes"][1]["summary"]["cost"] = float("nan")
    report = update_report(tmp_path, campaign)
    assert report["summary"]["partial"] is False
    assert report["summary"]["reported_cost_usd"] == 0.125
    assert report["summary"]["rollouts_with_reported_cost"] == 1
    assert report["settings"]["async_counter"]["reported_cost_usd"] is None
    page = (tmp_path / "index.html").read_text()
    assert 'http-equiv="refresh"' not in page
    assert "Partial results" not in page
    assert "missing costs are not zero" in page


def test_csv_keeps_formula_like_identifiers_as_data(tmp_path):
    campaign = manifest(tmp_path, repeats=1)
    campaign["outcomes"][0]["job_id"] = '=HYPERLINK("https://example.invalid")'
    campaign["outcomes"][1]["progress"]["last_event"] = "+SUM(1,2)"
    update_report(tmp_path, campaign)
    rows = list(csv.DictReader(io.StringIO((tmp_path / "rollouts.csv").read_text())))
    assert rows[0]["job_id"].startswith("'=")
    assert rows[1]["last_event"] == "'+SUM(1,2)"


def test_report_files_are_replaced_atomically(tmp_path, monkeypatch):
    campaign = manifest(tmp_path, repeats=1)
    replaced = []
    replace = os.replace

    def observe_replace(source, destination, *args, **kwargs):
        source, destination = Path(source), Path(destination)
        assert source.parent == destination.parent == tmp_path
        assert source != destination and source.read_text()
        replaced.append(destination.name)
        return replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "replace", observe_replace)
    update_report(tmp_path, campaign)
    assert replaced == ["progress.json", "rollouts.csv", "index.html"]
    assert not list(tmp_path.glob(".*.tmp"))


def test_bad_manifest_does_not_replace_previous_report(tmp_path):
    page = tmp_path / "index.html"
    page.write_text("previous report")
    with pytest.raises(ValueError, match="Expected a color-game-campaign/v1"):
        update_report(tmp_path, {"schema": "wrong"})
    assert page.read_text() == "previous report"
    assert not (tmp_path / "progress.json").exists()


def test_report_for_selected_setting_has_no_unrequested_arms(tmp_path):
    campaign = manifest(tmp_path, repeats=2)
    campaign["selected_settings"] = ["async_counter"]
    campaign["outcomes"] = [job for job in campaign["outcomes"] if job["setting"] == "async_counter"]
    report = update_report(tmp_path, campaign)
    assert report["selected_settings"] == ["async_counter"]
    assert list(report["settings"]) == ["async_counter"]
    assert report["summary"]["planned_rollouts"] == 2
    assert report["summary"]["planned_rounds"] == 10
    page = (tmp_path / "index.html").read_text()
    assert "guessing_only" not in page and "sync_counter" not in page.replace("async_counter", "")
