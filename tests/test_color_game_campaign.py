"""Offline coverage for repeated, bounded color-game campaigns."""
import gc
import json
import re
import shutil
import threading
import time
import weakref
from pathlib import Path

import pytest

from ai_collusion.client import ModelConfig
from experiments.color_game import campaign
from experiments.color_game.config import GameConfig, SETTINGS


def config(**changes):
    return GameConfig(**{"rounds": 1, "colors": ("red", "blue"), "actions_per_agent": 3,
                         "round_time_limit_s": 1.0, "seed": 124, **changes})


def model():
    return ModelConfig(name="offline", model="offline", transport="stub", extra_body={"marker": "original"})


class OfflineAgent:
    def __init__(self, snapshot):
        self.config = snapshot

    def __call__(self, request):
        role = request["role"]
        target = "red"
        if role == "alice":
            text = "\n".join(message.get("content", "") for message in request["messages"])
            target = json.loads(re.findall(r'Your private assigned color is ("[^"]+")', text)[-1])
        if "get" not in request["available_actions"] or request["action_index"] >= (2 if role == "alice" else 1):
            return {"action": "choose", "color": target}
        return {"action": "get", "url": "https://api.counterapi.dev/v1/campaign-check/shared"
                + ("/up" if role == "alice" and request["action_index"] == 1 else "")}


class Trajectory(dict):
    pass


def install_fast_runner(monkeypatch, *, before_run=None, references=None):
    monkeypatch.setattr(campaign, "ModelAgent", OfflineAgent)

    def fake_run(config, alice, bob, *, output_dir, plan, system_prompts, on_event):
        if before_run:
            before_run(config, alice, bob)
        output_dir.mkdir()
        on_event({"kind": "request", "messages": [{"content": "DO_NOT_QUEUE" * 1000}],
                  "system": "PRIVATE_PROMPT" * 1000, "role": "alice", "round_index": 0})
        on_event({"kind": "response", "response": {"text": "RAW_RESPONSE" * 1000}})
        on_event({"kind": "round_end", "round_index": 0, "match": True})
        trajectory = Trajectory({
            "schema": "color-game/v1", "config": config.to_dict(), "status": "complete", "plan": plan,
            "models": {"alice": campaign.asdict(alice.config), "bob": campaign.asdict(bob.config)},
            "system_prompts": system_prompts, "prompt_sha256": {
                role: campaign.hashlib.sha256(text.encode()).hexdigest() for role, text in system_prompts.items()},
            "rounds": [{"valid_for_analysis": True, "actions_used": {"alice": 1, "bob": 1}, "match": True,
                        "actions": [{"error": None}, {"error": None}]}],
            "summary": {"recorded_rounds": 1, "matched": 1, "total_actions": 2, "valid_rounds": 1,
                        "valid_accuracy": 1, "errors": 0, "infrastructure_errors": 0, "pending_responses": 0},
            "large_response_marker": "ONLY_IN_ROLLOUT" * 1000,
        })
        (output_dir / "rollout.json").write_text(json.dumps(trajectory))
        if references is not None:
            references.append(weakref.ref(trajectory))
        return trajectory

    monkeypatch.setattr(campaign, "run_rollout", fake_run)
    monkeypatch.setattr(campaign, "save_html", lambda trajectory, path: path.write_text("<html>Transcript</html>"))


def test_exactly_50_per_setting_preplanned_paired_and_bounded(tmp_path, monkeypatch):
    target = tmp_path / "campaign"
    guard = threading.Lock()
    barrier = threading.Barrier(50)
    active = peak = seen = 0
    agent_ids = []
    config_ids = []
    retained_agents = []

    def before_run(cfg, alice, bob):
        nonlocal active, peak, seen
        with guard:
            active += 1
            peak = max(peak, active)
            seen += 1
            first_wave = seen <= 50
            retained_agents.extend([alice, bob])
            agent_ids.extend([id(alice), id(bob)])
            config_ids.extend([id(alice.config), id(bob.config)])
        saved = json.loads((target / "campaign.json").read_text())
        assert len(saved["outcomes"]) == 150
        assert all(Path(item["artifact_paths"]["plan"]).is_file() for item in saved["outcomes"])
        if first_wave:
            barrier.wait(timeout=10)
        with guard:
            active -= 1

    install_fast_runner(monkeypatch, before_run=before_run)
    result = campaign.run_campaign(config(rollout_index=7), model(), output_dir=target,
                                   rollouts_per_setting=50, max_parallel_rollouts=50)
    assert result["status"] == "complete"
    assert result["summary"]["completed_rollouts"] == 150
    assert peak == 50
    assert len(set(agent_ids)) == len(set(config_ids)) == 300
    assert [item["setting"] for item in result["outcomes"]] == list(SETTINGS) * 50
    assert len({item["namespace"] for item in result["outcomes"]}) == 150
    assert set(result["settings"]) == set(SETTINGS)
    assert all(item["completed_rollouts"] == 50 for item in result["settings"].values())
    for repeat in range(50):
        jobs = result["outcomes"][repeat * 3:(repeat + 1) * 3]
        assert {item["rollout_index"] for item in jobs} == {repeat + 7}
        for field in ("assigned_colors", "fuzz_tags"):
            assert jobs[0]["plan"][field] == jobs[1]["plan"][field] == jobs[2]["plan"][field]
    assert result["summary"]["valid_accuracy"] == 1
    assert json.loads((target / "campaign.json").read_text()) == result


def test_private_counter_stores_and_saved_transcripts(tmp_path, monkeypatch):
    monkeypatch.setattr(campaign, "ModelAgent", OfflineAgent)
    result = campaign.run_campaign(config(), model(), output_dir=tmp_path / "campaign",
                                   rollouts_per_setting=2, max_parallel_rollouts=4)
    assert result["summary"]["completed_rollouts"] == 6
    assert result["summary"]["infrastructure_errors"] == 0
    for item in result["outcomes"]:
        trajectory = json.loads(Path(item["artifact_paths"]["json"]).read_text())
        assert item["plan"] == trajectory["plan"]
        assert item["prompt_sha256"] == trajectory["prompt_sha256"]
        expected_requests = sum(len(rnd["actions"]) for rnd in trajectory["rounds"])
        assert item["summary"]["requests_started"] == expected_requests
        assert item["progress"]["requests_started"] == expected_requests
        assert item["summary"]["model_api_errors"] == 0
        assert Path(item["artifact_paths"]["html"]).is_file()
        if item["setting"] == "guessing_only":
            assert trajectory["counter_state"] == []
        else:
            assert trajectory["counter_state"] == [{"namespace": "campaign-check", "key": "shared", "count": 1}]
            alice_reads = [a for a in trajectory["rounds"][0]["actions"]
                           if a["role"] == "alice" and a["action"]["action"] == "get"]
            assert alice_reads[0]["result"]["body"]["data"]["count"] == 0
        plan = json.loads(Path(item["artifact_paths"]["plan"]).read_text())
        assert plan["system_prompts"] == trajectory["system_prompts"]


def test_full_trajectories_are_released_and_events_remain_small(tmp_path, monkeypatch):
    references, events = [], []
    caller = threading.get_ident()
    install_fast_runner(monkeypatch, references=references)

    def callback(event):
        assert threading.get_ident() == caller
        events.append(event)

    result = campaign.run_campaign(config(), model(), output_dir=tmp_path / "campaign",
                                   rollouts_per_setting=3, max_parallel_rollouts=4, on_event=callback)
    gc.collect()
    assert len(references) == 9 and all(ref() is None for ref in references)
    assert "trajectories" not in result
    assert "ONLY_IN_ROLLOUT" not in json.dumps(result)
    serialized_events = json.dumps(events)
    assert "DO_NOT_QUEUE" not in serialized_events
    assert "RAW_RESPONSE" not in serialized_events
    assert "PRIVATE_PROMPT" not in serialized_events
    assert all("response" not in event and "messages" not in event for event in events)


def test_slow_observer_cannot_block_rollout_and_final_progress_is_exact(tmp_path, monkeypatch):
    install_fast_runner(monkeypatch)
    original = campaign.run_rollout
    entered, finished = threading.Event(), threading.Event()

    def run(cfg, *args, **kwargs):
        emit = kwargs["on_event"]
        emit({"kind": "request", "role": "alice", "round_index": 0})
        assert entered.wait(timeout=2)
        for _ in range(1000):
            emit({"kind": "round_clock_tick", "round_index": 0, "round_remaining_s": 10})
            emit({"kind": "action_result", "role": "alice", "round_index": 0})
        finished.set()
        return original(cfg, *args, **kwargs)

    def callback(event):
        if event["kind"] == "request":
            entered.set()
            assert finished.wait(timeout=2), "Rollout blocked while the observer was busy"

    monkeypatch.setattr(campaign, "run_rollout", run)
    result = campaign.run_campaign(config(), model(), output_dir=tmp_path / "campaign",
                                   rollouts_per_setting=1, max_parallel_rollouts=1, on_event=callback)
    assert result["status"] == "complete"
    assert sum(item["dropped_progress_events"] for item in result["outcomes"]) > 0
    assert all(item["progress"]["rounds_completed"] == 1 for item in result["outcomes"])
    assert all(item["progress"]["actions_completed"] == 2 for item in result["outcomes"])
    assert result["callback_errors"] == []


@pytest.mark.parametrize("failed_setting", ["guessing_only", "sync_counter"])
def test_provider_failure_is_saved_and_excluded_from_valid_accuracy(tmp_path, monkeypatch, failed_setting):
    calls = []

    class BadRequestError(Exception):
        status_code = 400

    class FailedAgent(OfflineAgent):
        def __call__(self, request):
            is_failed = (request.get("phase") is not None if failed_setting == "sync_counter" else
                         "get" not in request["available_actions"] and request.get("phase") is None)
            if is_failed:
                calls.append((request["role"], request["round_index"]))
                raise BadRequestError("private provider body")
            return super().__call__(request)

    monkeypatch.setattr(campaign, "ModelAgent", FailedAgent)
    result = campaign.run_campaign(config(rounds=2), model(), output_dir=tmp_path / "campaign",
                                   rollouts_per_setting=2, max_parallel_rollouts=3)
    assert calls and all(round_index == 0 for _, round_index in calls)
    assert len(calls) == (2 if failed_setting == "guessing_only" else 4)
    assert result["status"] == "complete_with_errors"
    failed = [item for item in result["outcomes"] if item["setting"] == failed_setting]
    assert len(failed) == 2 and all(item["status"] == "failed" for item in failed)
    assert all(item["error"]["category"] == "fatal_model_api" for item in failed)
    assert all(item["summary"]["valid_rounds"] == 0 for item in failed)
    assert result["settings"][failed_setting]["valid_accuracy"] is None
    assert result["summary"]["completed_rollouts"] == 4
    for item in failed:
        data = json.loads(Path(item["artifact_paths"]["json"]).read_text())
        assert data["status"] == "failed"
        assert data["summary"]["infrastructure_errors"] == (1 if failed_setting == "guessing_only" else 2)
        assert item["summary"]["model_api_errors"] == (1 if failed_setting == "guessing_only" else 2)
        assert data["rounds"][0]["actions"][0]["error"]["status_code"] == 400
        assert Path(item["artifact_paths"]["html"]).exists()
    assert "private provider body" not in json.dumps(result)


def test_one_worker_failure_and_bad_prompt_leave_other_jobs_running(tmp_path, monkeypatch):
    install_fast_runner(monkeypatch)
    original = campaign.run_rollout

    def run(cfg, *args, **kwargs):
        if cfg.setting == "async_counter" and cfg.rollout_index == 0:
            raise RuntimeError("private failure")
        return original(cfg, *args, **kwargs)

    def transform(role, text):
        if "This setting has no communication tools." in text:
            raise ValueError("bad prompt")
        return text

    monkeypatch.setattr(campaign, "run_rollout", run)
    result = campaign.run_campaign(config(), model(), output_dir=tmp_path / "campaign",
                                   rollouts_per_setting=2, max_parallel_rollouts=3, prompt_transform=transform)
    assert result["status"] == "complete_with_errors"
    assert result["summary"]["failed_rollouts"] == 3
    assert result["summary"]["completed_rollouts"] == 3
    assert result["outcomes"][0]["error"]["category"] == "prompt_preparation"
    assert result["outcomes"][1]["error"]["category"] == "rollout"


def test_interrupt_cancels_queued_work_and_preserves_started_jobs(tmp_path, monkeypatch):
    class SlowAgent(OfflineAgent):
        def __call__(self, request):
            time.sleep(0.05)
            return super().__call__(request)

    monkeypatch.setattr(campaign, "ModelAgent", SlowAgent)
    requested = False

    def callback(event):
        nonlocal requested
        if event["kind"] == "request" and not requested:
            requested = True
            raise KeyboardInterrupt()

    target = tmp_path / "campaign"
    with pytest.raises(KeyboardInterrupt):
        campaign.run_campaign(config(), model(), output_dir=target,
                              rollouts_per_setting=3, max_parallel_rollouts=2, on_event=callback)
    saved = json.loads((target / "campaign.json").read_text())
    assert saved["status"] == "interrupted"
    assert len(saved["outcomes"]) == 9
    started = [item for item in saved["outcomes"] if "started_utc" in item]
    assert len(started) == 2
    assert all(item["status"] == "interrupted" for item in started)
    assert all(item["status"] == "queued" for item in saved["outcomes"] if item not in started)
    assert all(Path(item["artifact_paths"]["json"]).exists() for item in started)


def test_observer_errors_are_counted_without_discarding_results(tmp_path, monkeypatch):
    install_fast_runner(monkeypatch)

    def callback(event):
        raise RuntimeError("private observer data")

    result = campaign.run_campaign(config(), model(), output_dir=tmp_path / "campaign",
                                   rollouts_per_setting=1, max_parallel_rollouts=3, on_event=callback)
    assert result["status"] == "complete_with_errors"
    assert result["summary"]["completed_rollouts"] == 3
    assert any(item["kind"] == "campaign_end" for item in result["callback_errors"])
    assert sum(item["count"] for item in result["callback_errors"]) > len(result["callback_errors"])
    assert "private observer data" not in json.dumps(result)


@pytest.mark.parametrize("kwargs", [{"rollouts_per_setting": 0}, {"rollouts_per_setting": True},
                                   {"max_parallel_rollouts": 0}, {"max_parallel_rollouts": 1.5}])
def test_invalid_counts_fail_before_model_calls(tmp_path, monkeypatch, kwargs):
    monkeypatch.setattr(campaign, "ModelAgent", lambda model: pytest.fail("No model must start"))
    with pytest.raises(ValueError):
        campaign.run_campaign(config(), model(), output_dir=tmp_path / "campaign", **kwargs)


def test_existing_artifacts_are_not_overwritten(tmp_path, monkeypatch):
    monkeypatch.setattr(campaign, "ModelAgent", lambda model: pytest.fail("No model must start"))
    (tmp_path / "keep.txt").write_text("keep")
    with pytest.raises(FileExistsError):
        campaign.run_campaign(config(), model(), output_dir=tmp_path)
    assert (tmp_path / "keep.txt").read_text() == "keep"


def test_resume_only_queued_plans_and_recover_completed_without_replaying_partial(tmp_path, monkeypatch):
    install_fast_runner(monkeypatch)
    target = tmp_path / "campaign"
    initial = campaign.run_campaign(config(), model(), output_dir=target,
                                    rollouts_per_setting=2, max_parallel_rollouts=3)
    saved = json.loads((target / "campaign.json").read_text())
    jobs = saved["outcomes"]
    jobs[1].update(status="failed", error={"type": "ExportFailure", "category": "html_export"})
    jobs[2]["status"] = "running"  # Game finished; manager crashed before its receipt.
    jobs[3]["status"] = "running"
    partial_path = Path(jobs[3]["artifact_paths"]["json"])
    partial = json.loads(partial_path.read_text())
    partial["status"] = "running"
    partial["rounds"][0].pop("actions_used")
    partial_path.write_text(json.dumps(partial))
    for index in (4, 5):
        jobs[index]["status"] = "queued"
        jobs[index].pop("started_utc", None)
        jobs[index].pop("finished_utc", None)
        jobs[index]["summary"] = {}
        jobs[index]["artifact_paths"] = {"plan": jobs[index]["artifact_paths"]["plan"]}
        shutil.rmtree(jobs[index]["output_dir"])
    # A crash between submit() and manager persistence can leave a queued label
    # even though work began. An existing journal makes this job non-replayable.
    stale_queued_dir = Path(jobs[5]["output_dir"])
    stale_queued_dir.mkdir()
    (stale_queued_dir / "events.jsonl").write_text('{"kind":"request"}\n')
    saved["status"] = "running"
    (target / "campaign.json").write_text(json.dumps(saved))
    original_bytes = {str(path): path.read_bytes() for path in target.rglob("*")
                      if path.is_file() and path.name != "campaign.json"}
    terminal_outcomes = json.loads(json.dumps(jobs[:2]))
    calls = []
    original = campaign.run_rollout

    def run(cfg, *args, **kwargs):
        calls.append((cfg.setting, cfg.rollout_index, kwargs["plan"], kwargs["system_prompts"]))
        return original(cfg, *args, **kwargs)

    monkeypatch.setattr(campaign, "run_rollout", run)
    resumed = campaign.run_campaign(config(), model(), output_dir=target, rollouts_per_setting=2,
                                    max_parallel_rollouts=3, resume=True)
    assert len(calls) == 1 and calls[0][:2] == ("async_counter", 1)
    assert calls[0][2] == initial["outcomes"][4]["plan"]
    plan_receipt = json.loads(Path(initial["outcomes"][4]["artifact_paths"]["plan"]).read_text())
    assert calls[0][3] == plan_receipt["system_prompts"]
    assert resumed["campaign_id"] == initial["campaign_id"]
    assert resumed["created_utc"] == initial["created_utc"]
    assert resumed["outcomes"][:2] == terminal_outcomes
    assert [item["status"] for item in resumed["outcomes"]] == [
        "complete", "failed", "complete", "interrupted", "complete", "interrupted"]
    assert resumed["outcomes"][3]["summary"]["valid_rounds"] == 0
    audit = resumed["recoveries"][0]["outcomes"]
    assert len(audit) == 6 and all("rollout_index" in item for item in audit)
    assert audit[2]["reason"] == "completed_trajectory_recovered"
    assert audit[3]["reason"] == audit[5]["reason"] == "partial_not_replayed"
    assert audit[4]["reason"] == "queued_plan_retained"
    for path, content in original_bytes.items():
        assert Path(path).read_bytes() == content
    # A further resume is a no-op for every terminal job, including failures.
    monkeypatch.setattr(campaign, "ModelAgent", lambda cfg: pytest.fail("No terminal job may run again"))
    again = campaign.run_campaign(config(), model(), output_dir=target, rollouts_per_setting=2,
                                  max_parallel_rollouts=3, resume=True)
    assert again["outcomes"] == resumed["outcomes"]
    assert len(again["recoveries"]) == 2


@pytest.mark.parametrize("mismatch", ["config", "model", "count", "parallel", "instructions",
                                     "target", "prompt", "namespace", "saved_trajectory", "trajectory_model"])
def test_resume_mismatch_rejects_before_calls_or_writes(tmp_path, monkeypatch, mismatch):
    install_fast_runner(monkeypatch)
    target = tmp_path / "campaign"
    result = campaign.run_campaign(config(), model(), output_dir=target,
                                   rollouts_per_setting=1, max_parallel_rollouts=3)
    cfg, mdl = config(), model()
    kwargs = {"rollouts_per_setting": 1, "max_parallel_rollouts": 3}
    if mismatch == "config":
        cfg = config(seed=999)
    elif mismatch == "model":
        mdl = ModelConfig(name="changed", model="offline", transport="stub")
    elif mismatch == "count":
        kwargs["rollouts_per_setting"] = 2
    elif mismatch == "parallel":
        kwargs["max_parallel_rollouts"] = 2
    elif mismatch == "instructions":
        kwargs["prompt_additions"] = {"alice": "Changed protocol."}
    elif mismatch in {"target", "prompt"}:
        path = Path(result["outcomes"][-1]["artifact_paths"]["plan"])
        receipt = json.loads(path.read_text())
        if mismatch == "target":
            receipt["plan"]["assigned_colors"] = ["invalid"]
        else:
            receipt["system_prompts"]["alice"] += " changed"
        path.write_text(json.dumps(receipt))
    elif mismatch == "namespace":
        path = target / "campaign.json"
        receipt = json.loads(path.read_text())
        receipt["outcomes"][-1]["namespace"] = "changed"
        path.write_text(json.dumps(receipt))
    else:
        path = Path(result["outcomes"][-1]["artifact_paths"]["json"])
        receipt = json.loads(path.read_text())
        if mismatch == "trajectory_model":
            receipt["models"]["bob"]["model"] = "different-model"
        else:
            receipt["plan"]["namespace"] = "changed"
        path.write_text(json.dumps(receipt))
    before = {str(path): path.read_bytes() for path in target.rglob("*") if path.is_file()}
    monkeypatch.setattr(campaign, "ModelAgent", lambda cfg: pytest.fail("No model may start before validation"))
    with pytest.raises(ValueError, match="Campaign resume mismatch"):
        campaign.run_campaign(cfg, mdl, output_dir=target, resume=True, **kwargs)
    assert {str(path): path.read_bytes() for path in target.rglob("*") if path.is_file()} == before


def test_invalid_response_sidecar_rejects_before_recovery_writes(tmp_path, monkeypatch):
    install_fast_runner(monkeypatch)
    target = tmp_path / "campaign"
    result = campaign.run_campaign(config(), model(), output_dir=target,
                                   rollouts_per_setting=1, max_parallel_rollouts=3)
    # The first job could be recovered and needs an HTML export. A later job
    # has an invalid response sidecar; validation must fail before that export.
    result["outcomes"][0]["status"] = "running"
    html = Path(result["outcomes"][0]["artifact_paths"]["html"])
    html.unlink()
    (target / "campaign.json").write_text(json.dumps(result))
    last_json = Path(result["outcomes"][-1]["artifact_paths"]["json"])
    record = json.loads(last_json.read_text())
    sidecar = last_json.parent / "invalid-response.json"
    sidecar.write_text("invalid json")
    record["rounds"][0]["actions"][0]["response_path"] = str(sidecar)
    last_json.write_text(json.dumps(record))
    before = {str(path): path.read_bytes() for path in target.rglob("*") if path.is_file()}
    monkeypatch.setattr(campaign, "ModelAgent", lambda cfg: pytest.fail("No model may start"))
    with pytest.raises(ValueError):
        campaign.run_campaign(config(), model(), output_dir=target, rollouts_per_setting=1,
                              max_parallel_rollouts=3, resume=True)
    assert not html.exists()
    assert {str(path): path.read_bytes() for path in target.rglob("*") if path.is_file()} == before


def test_cooperative_stop_keeps_unstarted_plans_available_for_resume(tmp_path, monkeypatch):
    install_fast_runner(monkeypatch)
    stop = threading.Event()
    stop.set()
    target = tmp_path / "campaign"
    stopped = campaign.run_campaign(config(), model(), output_dir=target, rollouts_per_setting=1,
                                    max_parallel_rollouts=3, stop_event=stop)
    assert stopped["status"] == "interrupted"
    assert all(item["status"] == "queued" for item in stopped["outcomes"])
    resumed = campaign.run_campaign(config(), model(), output_dir=target, rollouts_per_setting=1,
                                    max_parallel_rollouts=3, resume=True)
    assert resumed["summary"]["completed_rollouts"] == 3
    assert [item["plan"] for item in resumed["outcomes"]] == [item["plan"] for item in stopped["outcomes"]]


@pytest.mark.parametrize("selection", [("async_counter",), ("sync_counter", "guessing_only")])
def test_selected_settings_resume_retains_plans_order_and_counts(tmp_path, monkeypatch, selection):
    install_fast_runner(monkeypatch)
    stop = threading.Event()
    stop.set()
    target = tmp_path / "campaign"
    stopped = campaign.run_campaign(config(rollout_index=50), model(), output_dir=target,
                                    rollouts_per_setting=2, max_parallel_rollouts=50,
                                    settings=selection, stop_event=stop)
    assert stopped["selected_settings"] == list(selection)
    assert stopped["max_parallel_rollouts"] == 2 * len(selection)
    assert stopped["summary"]["planned_rollouts"] == stopped["summary"]["planned_rounds"] == 2 * len(selection)
    assert [item["setting"] for item in stopped["outcomes"]] == list(selection) * 2
    assert list(stopped["settings"]) == list(selection)
    assert [item["rollout_index"] for item in stopped["outcomes"]] == [50] * len(selection) + [51] * len(selection)
    before = {str(path): path.read_bytes() for path in target.rglob("*") if path.is_file()}
    with pytest.raises(ValueError, match="Campaign resume mismatch"):
        campaign.run_campaign(config(rollout_index=50), model(), output_dir=target,
                              rollouts_per_setting=2, max_parallel_rollouts=50, resume=True)
    assert {str(path): path.read_bytes() for path in target.rglob("*") if path.is_file()} == before
    resumed = campaign.run_campaign(config(rollout_index=50), model(), output_dir=target,
                                    rollouts_per_setting=2, max_parallel_rollouts=50,
                                    settings=selection, resume=True)
    assert resumed["summary"]["completed_rollouts"] == 2 * len(selection)
    assert [item["plan"] for item in resumed["outcomes"]] == [item["plan"] for item in stopped["outcomes"]]


@pytest.mark.parametrize("selection", [[], ["async_counter", "async_counter"], ["invalid"],
                                     "async_counter", None])
def test_invalid_setting_selection_fails_before_artifacts_or_calls(tmp_path, monkeypatch, selection):
    monkeypatch.setattr(campaign, "ModelAgent", lambda cfg: pytest.fail("No model may start"))
    target = tmp_path / "campaign"
    with pytest.raises(ValueError, match="settings"):
        campaign.run_campaign(config(), model(), output_dir=target, settings=selection)
    assert not target.exists()


def test_legacy_campaign_without_selection_resumes_default_arms(tmp_path, monkeypatch):
    install_fast_runner(monkeypatch)
    stop = threading.Event()
    stop.set()
    target = tmp_path / "campaign"
    result = campaign.run_campaign(config(), model(), output_dir=target,
                                   rollouts_per_setting=1, max_parallel_rollouts=3, stop_event=stop)
    result.pop("selected_settings")
    (target / "campaign.json").write_text(json.dumps(result))
    resumed = campaign.run_campaign(config(), model(), output_dir=target,
                                    rollouts_per_setting=1, max_parallel_rollouts=3, resume=True)
    assert resumed["summary"]["completed_rollouts"] == 3
    assert list(resumed["settings"]) == list(SETTINGS)
