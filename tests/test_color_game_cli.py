"""Offline checks for source snapshots and the separate campaign process."""
import argparse
import hashlib
import json
import os
import time
from pathlib import Path

import pytest

from experiments.color_game import cli
from experiments.color_game.config import SETTINGS


def launch_args(tmp_path):
    models = tmp_path / "models.yaml"
    models.write_text('models:\n  - name: offline\n    model: offline\n    transport: stub\n'
                      '    stub_text: \'{"action":"choose","color":"red"}\'\n')
    return argparse.Namespace(
        out=tmp_path / "launch", models=models, model="offline", env_file=tmp_path / "empty.env",
        colors="red,blue", rounds=1, actions_per_agent=2, seed=17, start_index=0,
        round_time_limit=0.2, no_fuzz_bob=False, rollouts=1, workers=3,
        prompt_additions=None, no_caffeinate=True,
    )


@pytest.mark.parametrize("selection", [None, ["async_counter"], ["sync_counter", "guessing_only"]])
def test_detached_snapshot_runs_without_notebook_and_keeps_transcripts(tmp_path, selection):
    args = launch_args(tmp_path)
    if selection is not None:
        args.settings = selection
    expected_settings = list(SETTINGS) if selection is None else selection
    out = cli.prepare(args)
    request = cli._verify(out)
    assert request["model"]["transport"] == "stub"
    assert request["selected_settings"] == expected_settings
    launch = cli._read(out / "launch.json")
    assert launch["selected_settings"] == expected_settings
    assert launch["planned_rollouts"] == launch["planned_rounds"] == len(expected_settings)
    assert (out / "source/experiments/covert_channel/control.py").is_file()
    assert not (out / "source/notebooks").exists()
    assert not (out / "source/.env").exists()
    assert cli.start_worker(out, foreground=True) == 0
    status = cli.inspect_status(out)
    assert not status["supervisor_active"]
    assert status["status"] == "complete"
    assert status["summary"]["completed_rollouts"] == len(expected_settings)
    assert status["selected_settings"] == expected_settings
    assert list(status["settings"]) == expected_settings
    assert all(s["completed_rollouts"] == 1 for s in status["settings"].values())
    manifest = cli._read(out / "runs/campaign.json")
    assert manifest["selected_settings"] == expected_settings
    assert [job["setting"] for job in manifest["outcomes"]] == expected_settings
    report = cli._read(out / "runs/progress.json")
    assert list(report["settings"]) == expected_settings
    assert report["summary"]["planned_rollouts"] == len(expected_settings)
    snapshots = {}
    for job in manifest["outcomes"]:
        for key in ("json", "html", "jsonl"):
            path = Path(job["artifact_paths"][key])
            assert path.is_file()
            snapshots[path] = hashlib.sha256(path.read_bytes()).hexdigest()
    assert (out / "runs/index.html").is_file()
    assert (out / "runs/rollouts.csv").is_file()
    assert cli.start_worker(out, resume=True, foreground=True) == 0
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest
               for path, digest in snapshots.items())
    assert len(list((out / "process-history").glob("*.json"))) == 1


@pytest.mark.parametrize("changed", ["request.json", "source-sha256.json", "source/experiments/color_game/cli.py"])
def test_changed_inputs_or_source_fail_before_launch(tmp_path, changed):
    out = cli.prepare(launch_args(tmp_path))
    path = out / changed
    if path.suffix == ".json":
        data = cli._read(path)
        data["changed"] = True
        path.write_text(json.dumps(data))
    else:
        path.write_text(path.read_text() + "\n# changed\n")
    with pytest.raises(ValueError, match="changed"):
        cli.start_worker(out)
    assert not (out / "process.json").exists()
    assert not (out / "runs").exists()


def test_lock_prevents_duplicate_supervisor_and_status_detects_exit(tmp_path):
    out = cli.prepare(launch_args(tmp_path))
    lock = cli._acquire_lock(out)
    try:
        assert cli.inspect_status(out)["supervisor_active"]
        with pytest.raises(ValueError, match="running supervisor"):
            cli.start_worker(out)
    finally:
        lock.close()
    assert cli.inspect_status(out)["status"] == "interrupted"
    assert not (out / "process.json").exists()


def test_stop_is_a_durable_request_and_does_not_kill_arbitrary_pids(tmp_path):
    out = cli.prepare(launch_args(tmp_path))
    cli.write_json(out / "process.json", {"pid": os.getpid()})
    assert cli.main(["stop", "--out", str(out)]) == 0
    assert cli._read(out / "stop-request.json")["reason"] == "user_requested_stop"


def test_startup_failure_has_saved_status_and_releases_lock(tmp_path):
    out = cli.prepare(launch_args(tmp_path))
    path = out / "request.json"
    path.write_text("{}")
    assert cli.worker(argparse.Namespace(out=out, lock_fd=None, resume=False)) == 1
    assert not cli._is_active(out)
    state = cli._read(out / "worker.json")
    assert state["status"] == "failed"
    assert state["error"]["phase"] == "startup"
    assert not (out / "runs").exists()


def test_launch_returns_while_separate_worker_writes_result(tmp_path):
    out = cli.prepare(launch_args(tmp_path))
    assert cli.start_worker(out) == 0
    process = cli._read(out / "process.json")
    assert process["pid"] != os.getpid()
    deadline = time.monotonic() + 15
    while cli._is_active(out) and time.monotonic() < deadline:
        time.sleep(0.05)
    status = cli.inspect_status(out)
    assert status["status"] == "complete", (out / "supervisor.log").read_text()
    assert status["summary"]["completed_rollouts"] == 3


def test_cli_parses_settings_and_refuses_duplicate_selection_before_creating_artifacts(tmp_path, monkeypatch):
    args = launch_args(tmp_path)
    monkeypatch.setattr(cli, "start_worker", lambda out, **kwargs: 0)
    command = ["launch", "--out", str(args.out), "--models", str(args.models),
               "--model", "offline", "--env-file", str(args.env_file), "--rollouts", "2",
               "--settings", "async_counter", "--no-caffeinate"]
    assert cli.main(command) == 0
    assert cli._read(args.out / "request.json")["selected_settings"] == ["async_counter"]
    assert cli._read(args.out / "launch.json")["planned_rollouts"] == 2
    args.out = tmp_path / "invalid"
    args.settings = ["async_counter", "async_counter"]
    with pytest.raises(ValueError, match="distinct"):
        cli.prepare(args)
    assert not args.out.exists()


def test_legacy_launch_without_selection_still_runs_all_settings(tmp_path):
    out = cli.prepare(launch_args(tmp_path))
    request, launch = cli._read(out / "request.json"), cli._read(out / "launch.json")
    request.pop("selected_settings")
    launch.pop("selected_settings")
    launch["request_sha256"] = cli.stable_sha256(request)
    cli.write_json(out / "request.json", request)
    cli.write_json(out / "launch.json", launch)
    assert cli.start_worker(out, foreground=True) == 0
    assert cli.inspect_status(out)["summary"]["completed_rollouts"] == 3


@pytest.mark.parametrize("field,value", [("selected_settings", ["async_counter"]), ("planned_rollouts", 1),
                                        ("planned_rounds", 100)])
def test_changed_launch_selection_or_counts_fail_before_start(tmp_path, field, value):
    out = cli.prepare(launch_args(tmp_path))
    launch = cli._read(out / "launch.json")
    launch[field] = value
    cli.write_json(out / "launch.json", launch)
    with pytest.raises(ValueError, match="changed"):
        cli.start_worker(out)
    assert not (out / "process.json").exists()
