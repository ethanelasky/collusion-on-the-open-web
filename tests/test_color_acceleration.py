"""Offline checks for an explicit concurrency change on a frozen campaign."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from ai_collusion import client
from ai_collusion.client import ModelConfig
from experiments.color_game import campaign, cli
from experiments.color_game.config import GameConfig
from scripts import accelerate_color_campaign as acceleration
from test_color_game_campaign import install_fast_runner


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def files_below(path):
    return {str(file.relative_to(path)): digest(file)
            for file in path.rglob("*") if file.is_file() and "__pycache__" not in file.parts}


def original_inputs(root):
    paths = [root / "request.json", root / "launch.json", root / "source-sha256.json"]
    paths += [root / "source" / name for name in read(root / "source-sha256.json")]
    return {str(path): digest(path) for path in paths}


def build_saved_campaign(tmp_path, monkeypatch):
    models = tmp_path / "models.yaml"
    models.write_text('models:\n  - name: offline\n    model: offline\n    transport: stub\n'
                      '    stub_text: \'{"action":"choose","color":"red"}\'\n')
    args = argparse.Namespace(
        out=tmp_path / "campaign", models=models, model="offline", env_file=tmp_path / "empty.env",
        colors="red,blue", rounds=1, actions_per_agent=2, seed=17, start_index=20,
        round_time_limit=.2, no_fuzz_bob=False, rollouts=10, workers=2,
        prompt_additions=None, no_caffeinate=True, settings=["async_counter"],
    )
    root = cli.prepare(args)
    request = read(root / "request.json")
    assert request["model"]["transport"] == "stub"
    install_fast_runner(monkeypatch)
    initial = campaign.run_campaign(
        GameConfig(**request["game"]), ModelConfig(**request["model"]), output_dir=root / "runs",
        rollouts_per_setting=10, max_parallel_rollouts=2, settings=["async_counter"],
    )
    # Keep a complete and an interrupted rollout; the remaining jobs never started.
    initial["status"] = "interrupted"
    interrupted = initial["outcomes"][1]
    interrupted["status"] = "interrupted"
    partial_path = Path(interrupted["artifact_paths"]["json"])
    partial = read(partial_path)
    partial["status"] = "interrupted"
    save(partial_path, partial)
    for outcome in initial["outcomes"][2:]:
        shutil.rmtree(outcome["output_dir"])
        outcome.update(status="queued", summary={}, error=None,
                       artifact_paths={"plan": outcome["artifact_paths"]["plan"]})
        for key in ("started_utc", "finished_utc"):
            outcome.pop(key, None)
    save(root / "runs" / "campaign.json", initial)
    return root, request, copy.deepcopy(initial)


def operational_override(root, *, workers=8):
    path = root / "test-operational-plan.json"
    save(path, {"schema": "color-game-acceleration/v1", "campaign_dir": str(root),
                "previous_parallel": 2, "new_parallel": workers, "rpm": 20})
    return {"previous_parallel": 2, "operational_plan_path": str(path),
            "operational_plan_sha256": digest(path)}


def resume(root, request, **changes):
    kwargs = {"output_dir": root / "runs", "rollouts_per_setting": 10,
              "max_parallel_rollouts": 8, "settings": ["async_counter"], "resume": True}
    kwargs.update(changes)
    return campaign.run_campaign(GameConfig(**request["game"]), ModelConfig(**request["model"]), **kwargs)


def test_explicit_parallel_change_only_starts_queued_jobs_and_retains_original_data(tmp_path, monkeypatch):
    root, request, initial = build_saved_campaign(tmp_path, monkeypatch)
    override = operational_override(root)
    frozen = original_inputs(root)
    retained = {str(path): digest(path) for job in initial["outcomes"][:2]
                for path in Path(job["output_dir"]).rglob("*") if path.is_file()}
    plans = files_below(root / "runs" / "plans")
    calls = []
    original = campaign.run_rollout
    def run(config, *args, **kwargs):
        calls.append(config.rollout_index)
        return original(config, *args, **kwargs)
    monkeypatch.setattr(campaign, "run_rollout", run)
    result = resume(root, request, resume_parallel_override=override)
    assert sorted(calls) == list(range(22, 30))
    assert len(result["outcomes"]) == result["rollouts_per_setting"] == 10
    assert result["max_parallel_rollouts"] == 8
    assert result["outcomes"][:2] == initial["outcomes"][:2]
    assert result["campaign_id"] == initial["campaign_id"]
    for key in ("base_config", "model", "selected_settings", "created_utc"):
        assert result[key] == initial[key]
    assert result["parallel_amendments"][-1]["previous_parallel"] == 2
    assert result["parallel_amendments"][-1]["new_parallel"] == 8
    assert result["parallel_amendments"][-1]["operational_plan_sha256"] == override["operational_plan_sha256"]
    assert all(digest(Path(path)) == value for path, value in retained.items())
    assert files_below(root / "runs" / "plans") == plans
    assert original_inputs(root) == frozen


@pytest.mark.parametrize("fault", ["no_override", "bad_hash", "missing_receipt", "unknown_key",
                                   "wrong_campaign", "wrong_previous", "wrong_workers", "model", "sample_size"])
def test_invalid_operational_override_fails_before_model_calls_or_campaign_writes(tmp_path, monkeypatch, fault):
    root, request, _ = build_saved_campaign(tmp_path, monkeypatch)
    override = operational_override(root)
    kwargs = {"resume_parallel_override": override}
    if fault == "no_override":
        kwargs = {}
    elif fault == "bad_hash":
        override["operational_plan_sha256"] = "0" * 64
    elif fault == "missing_receipt":
        Path(override["operational_plan_path"]).unlink()
    elif fault == "unknown_key":
        override["new_model"] = "other"
    elif fault == "model":
        request["model"]["model"] = "different-offline-model"
    elif fault == "sample_size":
        kwargs["rollouts_per_setting"] = 11
    else:
        path = Path(override["operational_plan_path"])
        receipt = read(path)
        if fault == "wrong_campaign":
            receipt["campaign_dir"] = str(root / "foreign")
        elif fault == "wrong_previous":
            receipt["previous_parallel"] = 3
        else:
            receipt["new_parallel"] = 7
        save(path, receipt)
        override["operational_plan_sha256"] = digest(path)
    before = files_below(root)
    monkeypatch.setattr(campaign, "ModelAgent", lambda _: pytest.fail("Validation must precede model calls"))
    with pytest.raises((ValueError, FileNotFoundError)):
        resume(root, request, **kwargs)
    assert files_below(root) == before


def test_original_job_receipts_are_validated_before_parallel_amendment(tmp_path, monkeypatch):
    root, request, initial = build_saved_campaign(tmp_path, monkeypatch)
    override = operational_override(root)
    path = Path(initial["outcomes"][-1]["artifact_paths"]["plan"])
    prepared = read(path)
    prepared["model"]["model"] = "changed-model"
    save(path, prepared)
    before = files_below(root)
    monkeypatch.setattr(campaign, "ModelAgent", lambda _: pytest.fail("No model may start"))
    with pytest.raises(ValueError, match="resume mismatch"):
        resume(root, request, resume_parallel_override=override)
    assert files_below(root) == before
    assert "parallel_amendments" not in read(root / "runs" / "campaign.json")


def test_worker_admits_each_local_request_attempt_and_releases_before_retry(tmp_path, monkeypatch):
    order = []
    active = threading.local()
    @contextmanager
    def admission():
        assert not getattr(active, "value", False)
        active.value = True
        order.append("enter")
        try:
            yield
        finally:
            active.value = False
            order.append("release")
    def retry(event):
        assert not active.value
        assert event["attempt"] == 1
        order.append("retry")
    def before_run(config, alice, bob):
        attempts = iter([TimeoutError("local test"), "ok"])
        def local_request():
            assert active.value
            order.append("attempt")
            result = next(attempts)
            if isinstance(result, Exception):
                raise result
            return result
        assert client._with_retries(alice.config, local_request) == "ok"
    monkeypatch.setattr(client.time, "sleep", lambda _: order.append("sleep"))
    monkeypatch.setattr(client.random, "uniform", lambda *_: 0.)
    install_fast_runner(monkeypatch, before_run=before_run)
    result = campaign.run_campaign(
        GameConfig(rounds=1), ModelConfig(name="offline", model="offline", transport="stub"),
        output_dir=tmp_path / "runs", rollouts_per_setting=1, max_parallel_rollouts=1,
        settings=["async_counter"], request_attempt_context=admission, request_on_retry=retry,
    )
    assert result["status"] == "complete"
    assert order == ["enter", "attempt", "release", "retry", "sleep", "enter", "attempt", "release"]


def test_default_campaign_does_not_replace_request_control_without_new_hooks(tmp_path, monkeypatch):
    install_fast_runner(monkeypatch)
    monkeypatch.setattr(campaign, "request_control", lambda **kwargs:
                        pytest.fail("Absent hooks must not create a new request-control scope"))
    result = campaign.run_campaign(
        GameConfig(rounds=1), ModelConfig(name="offline", model="offline", transport="stub"),
        output_dir=tmp_path / "runs", rollouts_per_setting=1, max_parallel_rollouts=1,
        settings=["async_counter"],
    )
    assert result["status"] == "complete"


def prepared_execution(tmp_path, monkeypatch, *, with_plan=True):
    root, request, initial = build_saved_campaign(tmp_path, monkeypatch)
    plan = tmp_path / "fixed-analysis-plan.json"
    save(plan, {"schema": "offline-fixed-design/v1", "directory": str(root),
                "rollouts": 10, "seed": 17, "start_index": 20, "model": request["model"]})
    overlay = tmp_path / "campaign-overlay.py"
    overlay.write_text(Path(campaign.__file__).read_text() + "\n# Offline operational overlay fixture.\n")
    execution = acceleration.prepare_execution(
        root, workers=8, rpm=20, plan_paths=(plan,) if with_plan else (),
        execution_id="offline-test", campaign_overlay=overlay,
    )
    return root, execution, plan, overlay, initial


def test_execution_snapshot_only_overlays_campaign_and_keeps_original_inputs(tmp_path, monkeypatch):
    root, request, _ = build_saved_campaign(tmp_path, monkeypatch)
    frozen = original_inputs(root)
    overlay = tmp_path / "campaign-overlay.py"
    overlay.write_text(Path(campaign.__file__).read_text() + "\n# Offline operational overlay fixture.\n")
    execution = acceleration.prepare_execution(root, workers=8, rpm=20, execution_id="snapshot-test",
                                               campaign_overlay=overlay)
    receipt = acceleration.verify_execution(execution)
    assert receipt["schema"] == "color-game-acceleration/v1"
    assert receipt["campaign_dir"] == str(root)
    assert receipt["previous_parallel"] == 2
    assert receipt["new_parallel"] == 8
    assert receipt["requests_per_minute"] == 20
    assert original_inputs(root) == frozen
    original_hashes = read(root / "source-sha256.json")
    changed = []
    for name, expected in original_hashes.items():
        clone = execution / "source" / name
        assert clone.is_file()
        if digest(clone) != expected:
            changed.append(name)
    assert changed == ["experiments/color_game/campaign.py"]
    assert digest(execution / "source/experiments/color_game/campaign.py") == digest(overlay)
    assert not (execution / "source" / ".env").exists()
    assert request["model"]["transport"] == "stub"


@pytest.mark.parametrize("changed", ["source/ai_collusion/client.py", "source/experiments/color_game/campaign.py",
                                     "original_source", "original_request", "original_plan"])
def test_execution_verification_rejects_changed_snapshot_or_frozen_inputs(tmp_path, monkeypatch, changed):
    root, execution, plan, _, _ = prepared_execution(tmp_path, monkeypatch)
    if changed == "original_source":
        path = root / "source/ai_collusion/client.py"
    elif changed == "original_request":
        path = root / "request.json"
    elif changed == "original_plan":
        path = plan
    else:
        path = execution / changed
    path.write_text(path.read_text() + "\n ")
    with pytest.raises(ValueError):
        acceleration.verify_execution(execution)


def test_active_original_lock_prevents_launch_before_changes(tmp_path, monkeypatch):
    root, execution, _, _, _ = prepared_execution(tmp_path, monkeypatch)
    save(root / "stop-request.json", {"reason": "keep this stop receipt"})
    lock = cli._acquire_lock(root)
    try:
        before = files_below(root)
        monkeypatch.setattr(acceleration.subprocess, "Popen", lambda *a, **k: pytest.fail("No process may start"))
        with pytest.raises(ValueError, match="running|active|lock"):
            acceleration.start_execution(execution)
        assert files_below(root) == before
    finally:
        lock.close()


def test_all_terminal_campaign_cannot_start_acceleration_worker(tmp_path, monkeypatch):
    root, execution, _, _, _ = prepared_execution(tmp_path, monkeypatch)
    manifest = read(root / "runs/campaign.json")
    for job in manifest["outcomes"]:
        if job["status"] == "queued":
            job["status"] = "interrupted"
    manifest["status"] = "interrupted"
    save(root / "runs/campaign.json", manifest)
    save(root / "stop-request.json", {"reason": "retain original stop receipt"})
    lock = cli._acquire_lock(root)
    lock.close()
    before = files_below(root)
    monkeypatch.setattr(acceleration.subprocess, "Popen", lambda *a, **k:
                        pytest.fail("A terminal campaign must not start a process"))
    with pytest.raises(ValueError, match="No queued jobs"):
        acceleration.start_execution(execution)
    assert files_below(root) == before
    assert not (execution / "start-receipt.json").exists()
    assert not cli._is_active(root)


def test_detached_stub_resume_runs_from_clone_and_preserves_terminal_files(tmp_path, monkeypatch):
    root, execution, _, _, initial = prepared_execution(tmp_path, monkeypatch)
    request = read(root / "request.json")
    assert request["model"]["transport"] == "stub"
    frozen = original_inputs(root)
    retained = {str(path): digest(path) for job in initial["outcomes"][:2]
                for path in Path(job["output_dir"]).rglob("*") if path.is_file()}
    result = acceleration.start_execution(execution, foreground=True)
    assert result in (0, 1)
    worker = read(execution / "worker-final.json")
    assert worker["status"] in {"complete", "complete_with_errors"}, worker
    assert not cli._is_active(root)
    campaign_record = read(root / "runs/campaign.json")
    assert len(campaign_record["outcomes"]) == 10
    assert campaign_record["max_parallel_rollouts"] == 8
    assert campaign_record["outcomes"][:2] == initial["outcomes"][:2]
    for outcome in campaign_record["outcomes"][2:]:
        assert outcome["status"].startswith("complete")
        transcript = read(Path(outcome["artifact_paths"]["json"]))
        assert {m["transport"] for m in transcript["models"].values()} == {"stub"}
    assert all(digest(Path(path)) == value for path, value in retained.items())
    assert original_inputs(root) == frozen
    process = read(root / "process.json")
    assert process["execution_dir"] == str(execution)
    assert str(execution / "source/scripts/accelerate_color_campaign.py") in process["command"]


@pytest.mark.parametrize("workers,rpm", [(0, 20), (True, 20), (8, 0), (8, -1), (8, float("nan"))])
def test_invalid_operational_limits_fail_before_execution_files(tmp_path, monkeypatch, workers, rpm):
    root, _, _ = build_saved_campaign(tmp_path, monkeypatch)
    before = files_below(root)
    with pytest.raises(ValueError):
        acceleration.prepare_execution(root, workers=workers, rpm=rpm, execution_id="invalid")
    assert files_below(root) == before
