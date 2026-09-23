"""Bounded, durable repeated runs of the three color-game settings.

The campaign keeps plans, summaries, and artifact paths in memory. Full model
requests and responses stay in each rollout's journal and JSON transcript.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import queue
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

from ai_collusion.auth_stop import AuthenticationStop
from ai_collusion.client import request_control

from .batch import _error, _paths
from .config import SETTINGS, make_plan
from .game import _save_json, load_rollout, run_rollout
from .model import ModelAgent
from .notebook import PromptEditor, normalize_config
from .view import save_html

_FATAL_HTTP_STATUSES = {400, 401, 402, 403, 404, 405, 413, 415, 422}
_TERMINAL = {"complete", "complete_with_errors", "complete_pending_responses", "failed", "interrupted"}


class CampaignModelError(RuntimeError):
    """Stop one rollout after a saved, non-retryable model request error."""


def _utc():
    return datetime.now(timezone.utc).isoformat()


def _small_event(event):
    """Do not place prompts, responses, request histories, or tool URLs in queues."""
    fields = ("kind", "event_index", "time_unix_s", "role", "round_index", "step", "tick",
              "phase", "remaining_s", "round_elapsed_s", "round_remaining_s", "match",
              "valid_for_analysis", "alice_color", "bob_color", "assigned_color", "status")
    result = {key: value[:256] if isinstance(value, str) else value
              for key in fields if isinstance(value := event.get(key), (str, int, float, bool))}
    error = event.get("error")
    if isinstance(error, dict):
        result["error"] = {
            key: value[:256] if isinstance(value, str) else value
            for key in ("type", "category", "status_code", "retryable", "attempts", "retry_exhausted",
                        "retry_stop_reason")
            if isinstance(value := error.get(key), (str, int, float, bool))
        }
    action = event.get("action")
    if isinstance(action, dict) and isinstance(action.get("action"), str):
        result["action_type"] = action["action"][:32]
    return result


def _summarize(outcomes):
    summaries = [item.get("summary") or {} for item in outcomes]
    valid_rounds = sum(s.get("valid_rounds", 0) for s in summaries)
    valid_matches = sum(s.get("valid_matches", 0) for s in summaries)
    costs = [s["cost"] for s in summaries if type(s.get("cost")) in (int, float)
             and math.isfinite(s["cost"]) and s["cost"] >= 0]
    return {
        "planned_rollouts": len(outcomes),
        "queued_rollouts": sum(o["status"] == "queued" for o in outcomes),
        "running_rollouts": sum(o["status"] == "running" for o in outcomes),
        "completed_rollouts": sum(o["status"].startswith("complete") for o in outcomes),
        "failed_rollouts": sum(o["status"] == "failed" for o in outcomes),
        "interrupted_rollouts": sum(o["status"] == "interrupted" for o in outcomes),
        "rollouts_with_errors": sum(bool(o.get("error")) or bool(s.get("errors"))
                                    or bool(s.get("infrastructure_errors"))
                                    for o, s in zip(outcomes, summaries)),
        "planned_rounds": sum(o["config"]["rounds"] for o in outcomes),
        "recorded_rounds": sum(s.get("recorded_rounds", 0) for s in summaries),
        "matched": sum(s.get("matched", 0) for s in summaries),
        "valid_rounds": valid_rounds, "valid_matches": valid_matches,
        "valid_accuracy": valid_matches / valid_rounds if valid_rounds else None,
        "total_actions": sum(s.get("total_actions", 0) for s in summaries),
        "requests_started": sum(s.get("requests_started", 0) for s in summaries),
        "model_api_errors": sum(s.get("model_api_errors", 0) for s in summaries),
        "infrastructure_errors": sum(s.get("infrastructure_errors", 0) for s in summaries),
        "missing_choices": sum(s.get("missing_choices", 0) for s in summaries),
        "pending_responses": sum(s.get("pending_responses", 0) for s in summaries),
        "reported_cost_usd": sum(costs) if costs else None,
        "rollouts_with_reported_cost": len(costs),
        "cost_note": "Saved provider-reported costs only; missing costs are not zero.",
        "accuracy_note": "Valid accuracy includes only completed rounds marked valid by the game.",
    }


def _trajectory_summary(trajectory):
    summary = copy.deepcopy(trajectory.get("summary", {}))
    valid = [rnd for rnd in trajectory.get("rounds", [])
             if rnd.get("valid_for_analysis") and "actions_used" in rnd]
    actions = [action for rnd in trajectory.get("rounds", []) for action in rnd.get("actions", [])]
    summary.update(valid_rounds=len(valid), valid_matches=sum(bool(rnd.get("match")) for rnd in valid),
                   requests_started=len(actions), model_api_errors=sum(
                       any(isinstance(action.get(key), dict) and action[key].get("category") == "model_api"
                           for key in ("error", "response_error")) for action in actions))
    return summary


def _settled_progress(trajectory, summary):
    return {"rounds_completed": sum("actions_used" in rnd for rnd in trajectory.get("rounds", []))
            if trajectory else 0, "actions_completed": summary.get("total_actions", 0),
            "requests_started": summary.get("requests_started", 0),
            "model_api_errors": summary.get("model_api_errors", 0), "last_event": "settled"}


def _same_json(left, right):
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def selected_settings(settings=SETTINGS):
    """Validate an explicit, ordered selection without changing the default arms."""
    if (not isinstance(settings, (list, tuple)) or not settings
            or any(setting not in SETTINGS for setting in settings)
            or len(set(settings)) != len(settings)):
        raise ValueError("settings must contain distinct color-game setting names")
    return tuple(settings)


def _parallel_resume_receipt(directory, parallel, override):
    """Bind an explicit concurrency-only override to an immutable receipt."""
    if override is None:
        return parallel, None
    required = {"previous_parallel", "operational_plan_path", "operational_plan_sha256"}
    if not isinstance(override, dict) or set(override) != required:
        raise ValueError("Unknown or incomplete resume_parallel_override fields")
    previous = override["previous_parallel"]
    if type(previous) is not int or previous < 1:
        raise ValueError("previous_parallel must be a positive integer")
    path = Path(override["operational_plan_path"]).expanduser().resolve()
    contents = path.read_bytes()
    digest = hashlib.sha256(contents).hexdigest()
    if digest != override["operational_plan_sha256"]:
        raise ValueError("Operational plan hash changed")
    amendment = json.loads(contents)
    expected = {"schema": "color-game-acceleration/v1", "campaign_dir": str(directory.parent),
                "previous_parallel": previous, "new_parallel": parallel}
    for key, value in expected.items():
        if not _same_json(amendment.get(key), value):
            raise ValueError(f"Operational plan mismatch: {key}")
    return previous, {"previous_parallel": previous, "new_parallel": parallel,
                      "operational_plan_path": str(path), "operational_plan_sha256": digest}


def _resume_campaign(directory, config, model_snapshot, count, parallel, additions, transform, settings,
                     resume_parallel_override=None):
    """Validate the complete receipt before changing it or starting any job."""
    manifest_path = directory / "campaign.json"
    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_parallel, amendment = _parallel_resume_receipt(directory, parallel, resume_parallel_override)
    expected = {"schema": "color-game-campaign/v1", "output_dir": str(directory),
                "base_config": config.to_dict(), "model": asdict(model_snapshot),
                "rollouts_per_setting": count, "max_parallel_rollouts": expected_parallel}
    for field, value in expected.items():
        if not _same_json(saved.get(field), value):
            raise ValueError(f"Campaign resume mismatch: {field}")
    if not _same_json(saved.get("selected_settings", list(SETTINGS)), list(settings)):
        raise ValueError("Campaign resume mismatch: selected_settings")
    outcomes = saved.get("outcomes")
    if not isinstance(outcomes, list) or len(outcomes) != count * len(settings):
        raise ValueError("Campaign resume mismatch: outcome count")
    prepared = {}
    configs = [replace(config, rollout_index=config.rollout_index + repeat, setting=setting)
               for repeat in range(count) for setting in settings]
    for index, (outcome, arm_config) in enumerate(zip(outcomes, configs)):
        job_id = f"{index:04d}-{arm_config.setting}-r{arm_config.rollout_index:04d}"
        arm_dir = directory / job_id
        expected_job = {"job_index": index, "job_id": job_id, "arm": arm_config.arm,
                        "setting": arm_config.setting, "rollout_index": arm_config.rollout_index,
                        "config": arm_config.to_dict(), "output_dir": str(arm_dir)}
        for field, value in expected_job.items():
            if not _same_json(outcome.get(field), value):
                raise ValueError(f"Campaign resume mismatch: job {index} {field}")
        if outcome.get("status") not in _TERMINAL | {"queued", "running"}:
            raise ValueError(f"Campaign resume mismatch: job {index} status")
        # A failed prompt preparation has no runnable plan and remains terminal.
        if (outcome["status"] == "failed" and (outcome.get("error") or {}).get("category") == "prompt_preparation"
                and "plan" not in outcome.get("artifact_paths", {})):
            continue
        plan_path = directory / "plans" / f"{job_id}.json"
        if outcome.get("artifact_paths", {}).get("plan") != str(plan_path):
            raise ValueError(f"Campaign resume mismatch: job {index} plan path")
        receipt = json.loads(plan_path.read_text(encoding="utf-8"))
        plan = receipt.get("plan")
        if not isinstance(plan, dict) or not isinstance(plan.get("namespace"), str):
            raise ValueError(f"Campaign resume mismatch: job {index} plan")
        if plan != make_plan(arm_config, namespace=plan["namespace"]):
            raise ValueError(f"Campaign resume mismatch: job {index} target/tag plan")
        if outcome.get("plan") != plan or outcome.get("namespace") != plan["namespace"]:
            raise ValueError(f"Campaign resume mismatch: job {index} namespace/plan")
        prompts = receipt.get("system_prompts")
        if (not isinstance(prompts, dict) or set(prompts) != {"alice", "bob"}
                or any(not isinstance(text, str) or not text.strip() for text in prompts.values())):
            raise ValueError(f"Campaign resume mismatch: job {index} prompts")
        hashes = {role: hashlib.sha256(text.encode()).hexdigest() for role, text in prompts.items()}
        expected_receipt = {"job_id": job_id, "config": arm_config.to_dict(), "model": asdict(model_snapshot),
                            "prompt_sha256": hashes}
        for field, value in expected_receipt.items():
            if not _same_json(receipt.get(field), value):
                raise ValueError(f"Campaign resume mismatch: job {index} receipt {field}")
        if outcome.get("prompt_sha256") != hashes:
            raise ValueError(f"Campaign resume mismatch: job {index} prompt hashes")
        regenerated = PromptEditor(additions=copy.deepcopy(additions), transform=transform).resolve(arm_config, plan=plan)
        if regenerated != prompts:
            raise ValueError(f"Campaign resume mismatch: job {index} prompt instructions")
        record_path = arm_dir / "rollout.json"
        if record_path.exists():
            # Hydration is read-only and validates response sidecars before the
            # recovery pass can write even a missing HTML export.
            record = load_rollout(arm_dir)
            if (record.get("schema") != "color-game/v1" or record.get("config") != arm_config.to_dict()
                    or record.get("plan") != plan or record.get("system_prompts") != prompts
                    or record.get("prompt_sha256") != hashes):
                raise ValueError(f"Campaign resume mismatch: job {index} saved trajectory")
            recorded_models = record.get("models")
            expected_model = asdict(model_snapshot)
            if (not isinstance(recorded_models, dict) or set(recorded_models) != {"alice", "bob"}
                    or any(not isinstance(metadata, dict) or any(
                        not _same_json(metadata.get(field), value) for field, value in expected_model.items())
                        for metadata in recorded_models.values())):
                raise ValueError(f"Campaign resume mismatch: job {index} trajectory model")
            if str(record.get("status", "")).startswith("complete") and (
                    len(record.get("rounds", [])) != arm_config.rounds
                    or any("actions_used" not in rnd for rnd in record["rounds"])):
                raise ValueError(f"Campaign resume mismatch: job {index} incomplete completion")
            del record
        elif outcome["status"].startswith("complete"):
            raise ValueError(f"Campaign resume mismatch: job {index} missing completed trajectory")
        if outcome["status"] == "queued" and (not arm_dir.exists() or not any(arm_dir.iterdir())):
            prepared[index] = (arm_config, prompts, plan)

    # Validation above is read-only. Only now may recovery write missing HTML
    # or update the manifest. Terminal transcript and plan files remain intact.
    audit = []
    for index, outcome in enumerate(outcomes):
        old_status = outcome["status"]
        reason = "terminal_preserved"
        if index in prepared:
            reason = "queued_plan_retained"
        elif old_status not in _TERMINAL:
            arm_dir = Path(outcome["output_dir"])
            trajectory = None
            if (arm_dir / "rollout.json").is_file():
                trajectory = load_rollout(arm_dir)
            summary = _trajectory_summary(trajectory) if trajectory else {}
            if trajectory is not None and trajectory["status"].startswith("complete"):
                status, error, reason = trajectory["status"], None, "completed_trajectory_recovered"
            else:
                status, reason = "interrupted", "partial_not_replayed"
                error = {"type": "CampaignProcessInterrupted", "category": "campaign_recovery"}
            if trajectory is not None and not (arm_dir / "transcript.html").exists():
                try:
                    save_html(trajectory, arm_dir / "transcript.html")
                except Exception as exc:
                    error = error or _error(exc, "html_export")
                    status = "failed"
            outcome.update(status=status, error=error, summary=summary,
                           progress=_settled_progress(trajectory, summary), recovered_utc=_utc(),
                           artifact_paths={**outcome["artifact_paths"], **_paths(arm_dir)})
            del trajectory
        audit.append({"job_index": index, "job_id": outcome["job_id"], "rollout_index": outcome["rollout_index"],
                      "old_status": old_status, "new_status": outcome["status"], "reason": reason})
    saved.setdefault("recoveries", []).append({"resumed_utc": _utc(), "previous_status": saved["status"],
                                               "outcomes": audit})
    if amendment is not None:
        saved.setdefault("parallel_amendments", []).append({**amendment, "applied_utc": _utc()})
        saved["max_parallel_rollouts"] = parallel
    saved["status"] = "running"
    saved.pop("finished_utc", None)
    return saved, prepared


def run_campaign(base_config, model_config, *, output_dir, rollouts_per_setting=50,
                 max_parallel_rollouts=50, prompt_additions=None, prompt_transform=None,
                 on_event=None, stop_event=None, resume=False, settings=SETTINGS,
                 resume_parallel_override=None, request_attempt_context=None, request_on_retry=None) -> dict:
    """Run selected settings, with at most ``max_parallel_rollouts`` active.

    All plans and prompt text are saved before the first model is constructed.
    Settings alternate in submission order. Within each repetition, targets and
    fuzz tags are paired by seed and rollout index; namespaces are independent.
    Every job owns fresh agents, a counter store, and an output directory.
    ``settings`` defaults to all three arms. A subset preserves its given order.

    The callback runs on the calling thread and receives only small status
    events. Returned outcomes contain summaries and paths, never trajectories.
    A failed rollout does not cancel peers and is never automatically replayed.
    HTTP 401 is an exception: authentication failure stops the whole experiment.
    HTTP request/configuration failures stop that rollout after the error is
    journaled. Interrupts stop pending jobs and ask active jobs to stop at their
    next journal event. In-flight provider calls can take time to return.

    An optional threading.Event allows an external supervisor to request the
    same cooperative stop without sending a process signal.

    ``resume=True`` validates the saved configuration, model, plans, and prompts,
    then starts only queued jobs with empty output directories. Completed files
    missed by a crashed manager are recovered. Partial jobs remain interrupted;
    their clocks and paid requests are never replayed. The caller must hold an
    exclusive campaign lock so a second process cannot launch the same jobs.
    """
    for name, value in (("rollouts_per_setting", rollouts_per_setting),
                        ("max_parallel_rollouts", max_parallel_rollouts)):
        if type(value) is not int or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    config = normalize_config(base_config)
    settings = selected_settings(settings)
    directory = Path(output_dir).expanduser().resolve()
    if type(resume) is not bool:
        raise ValueError("resume must be a boolean")
    if resume_parallel_override is not None and not resume:
        raise ValueError("A parallel override requires resume=True")
    for name, hook in (("request_attempt_context", request_attempt_context), ("request_on_retry", request_on_retry)):
        if hook is not None and not callable(hook):
            raise ValueError(f"{name} must be callable")
    if "sync_counter" in settings and (request_attempt_context is not None or request_on_retry is not None):
        raise ValueError("Request admission hooks are supported only for sequential settings")
    model_snapshot = copy.deepcopy(model_config)
    additions = copy.deepcopy(prompt_additions if prompt_additions is not None else {})
    parallel = min(max_parallel_rollouts, rollouts_per_setting * len(settings))
    manifest_path = directory / "campaign.json"
    if resume:
        campaign, prepared = _resume_campaign(directory, config, model_snapshot, rollouts_per_setting,
                                              parallel, additions, prompt_transform, settings,
                                              resume_parallel_override=resume_parallel_override)
        outcomes = campaign["outcomes"]
    else:
        if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
            raise FileExistsError(f"Campaign output directory is not empty: {directory}")
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "plans").mkdir()
        outcomes, prepared = [], {}
        for repeat in range(rollouts_per_setting):
            for setting in settings:
                arm_config = replace(config, rollout_index=config.rollout_index + repeat, setting=setting)
                index = len(outcomes)
                job_id = f"{index:04d}-{arm_config.setting}-r{arm_config.rollout_index:04d}"
                plan_path = directory / "plans" / f"{job_id}.json"
                outcome = {
                    "job_index": index, "job_id": job_id, "arm": arm_config.arm,
                    "setting": arm_config.setting, "rollout_index": arm_config.rollout_index,
                    "config": arm_config.to_dict(), "status": "queued",
                    "output_dir": str(directory / job_id), "summary": {}, "error": None,
                    "artifact_paths": {"plan": str(plan_path)},
                    "progress": {"rounds_completed": 0, "actions_completed": 0,
                                 "requests_started": 0, "model_api_errors": 0},
                }
                try:
                    plan = make_plan(arm_config)
                    editor = PromptEditor(additions=copy.deepcopy(additions), transform=prompt_transform)
                    prompts = editor.resolve(arm_config, plan=plan)
                    outcome.update(plan=plan, namespace=plan["namespace"], prompt_sha256={
                        role: hashlib.sha256(text.encode()).hexdigest() for role, text in prompts.items()})
                    _save_json(plan_path, {"job_id": job_id, "config": arm_config.to_dict(), "plan": plan,
                                          "model": asdict(model_snapshot), "system_prompts": prompts,
                                          "prompt_sha256": outcome["prompt_sha256"]})
                    prepared[index] = (arm_config, prompts, plan)
                except Exception as exc:
                    outcome.update(status="failed", error=_error(exc, "prompt_preparation"))
                    if not plan_path.exists():
                        outcome["artifact_paths"] = {}
                outcomes.append(outcome)

        campaign = {
            "schema": "color-game-campaign/v1", "campaign_id": str(uuid.uuid4()), "status": "running",
            "created_utc": _utc(), "output_dir": str(directory), "base_config": config.to_dict(),
            "model": asdict(model_snapshot), "rollouts_per_setting": rollouts_per_setting,
            "selected_settings": list(settings),
            "max_parallel_rollouts": parallel, "submission_order": "round_robin_settings",
            "paired_fields": ["assigned_colors", "fuzz_tags"], "namespace_policy": "fresh_per_rollout",
            "progress_note": "Live request/error counts are best effort; final counts use saved action records. "
                             "Requests count journaled model attempts, not provider-internal HTTP retries.",
            "artifact_paths": {"manifest": str(manifest_path)}, "outcomes": outcomes,
            "callback_errors": [], "summary": {}, "settings": {},
        }
    stop = stop_event if stop_event is not None else threading.Event()
    messages = queue.Queue(maxsize=max(128, parallel * 16))
    last_saved = 0.0

    def persist():
        nonlocal last_saved
        campaign["summary"] = _summarize(outcomes)
        campaign["settings"] = {setting: _summarize([o for o in outcomes if o["setting"] == setting])
                                for setting in settings}
        campaign["updated_utc"] = _utc()
        _save_json(manifest_path, campaign)
        last_saved = time.monotonic()

    def dispatch(event):
        if on_event is None:
            return
        try:
            on_event(copy.deepcopy(event))
        except Exception as exc:
            # Keep failures visible without retaining arbitrary callback bodies.
            key = {**_error(exc, "callback"), "kind": event.get("kind")}
            existing = next((item for item in campaign["callback_errors"]
                             if all(item.get(k) == v for k, v in key.items())), None)
            if existing is None:
                campaign["callback_errors"].append({**key, "count": 1})
            else:
                existing["count"] += 1

    def worker(index):
        arm_config, prompts, plan = prepared[index]
        outcome = outcomes[index]
        arm_dir = Path(outcome["output_dir"])
        cancelled = False
        fatal_error = None
        dropped_progress_events = 0

        def emit(event):
            nonlocal cancelled, fatal_error, dropped_progress_events
            kind = event.get("kind")
            if stop.is_set() and not cancelled:
                cancelled = True
                raise KeyboardInterrupt()
            # Let the game finish registering the provider error before aborting.
            # Sequential mode emits action_result after this registration; sync
            # may next emit a request or round_end instead.
            if fatal_error is not None and kind in {"request", "round_start", "action_result", "round_end"}:
                raise CampaignModelError()
            event = _small_event(event)
            if kind == "model_error" and event.get("error", {}).get("status_code") in _FATAL_HTTP_STATUSES:
                fatal_error = event["error"]
            # Every full event is already on disk. Status delivery must never
            # block the game's clock, even when a live observer is slow.
            try:
                messages.put_nowait((index, event))
            except queue.Full:
                dropped_progress_events += 1

        trajectory = None
        failure = None
        status = "failed"
        try:
            emit({"kind": "campaign_rollout_started"})
            # Set request hooks inside each pool thread: ContextVars do not
            # automatically propagate from the supervisor into worker threads.
            scope = (request_control(attempt_context=request_attempt_context, on_retry=request_on_retry)
                     if request_attempt_context is not None or request_on_retry is not None else nullcontext())
            with scope:
                trajectory = run_rollout(
                    arm_config, ModelAgent(copy.deepcopy(model_snapshot)), ModelAgent(copy.deepcopy(model_snapshot)),
                    output_dir=arm_dir, system_prompts=prompts, plan=plan, on_event=emit)
            status = trajectory["status"]
        except AuthenticationStop:
            raise
        except BaseException as exc:
            failure = _error(exc, "fatal_model_api" if isinstance(exc, CampaignModelError) else "rollout")
            if fatal_error:
                failure["model_error"] = fatal_error
            status = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
            if (arm_dir / "rollout.json").is_file():
                try:
                    trajectory = load_rollout(arm_dir)
                except Exception as load_error:
                    failure["partial_load_error"] = _error(load_error, "partial_rollout_load")
        summary = {}
        if trajectory is not None:
            summary = _trajectory_summary(trajectory)
            try:
                save_html(trajectory, arm_dir / "transcript.html")
            except Exception as exc:
                failure = failure or _error(exc, "html_export")
                status = "failed"
        # A Future must never retain a full trajectory after a worker finishes.
        return {"status": status, "error": failure, "summary": summary, "finished_utc": _utc(),
                "dropped_progress_events": dropped_progress_events,
                "progress": _settled_progress(trajectory, summary),
                "artifact_paths": {**outcome["artifact_paths"], **_paths(arm_dir)}}

    def handle_event(index, event):
        outcome = outcomes[index]
        progress = outcome["progress"]
        # Events can remain queued after a future settles. The saved trajectory
        # then provides the exact progress counts; old events must not add twice.
        if outcome["status"] not in _TERMINAL:
            progress["last_event"] = event["kind"]
            if "round_index" in event:
                progress["round_index"] = event["round_index"]
            if event["kind"] == "round_end":
                progress["rounds_completed"] += 1
            if event["kind"] == "action_result":
                progress["actions_completed"] += 1
            if event["kind"] == "request":
                progress["requests_started"] = progress.get("requests_started", 0) + 1
            if event["kind"] == "model_error":
                progress["model_api_errors"] = progress.get("model_api_errors", 0) + 1
        if not interrupted:
            dispatch({**event, "campaign_id": campaign["campaign_id"], "job_id": outcome["job_id"],
                      "job_index": index, "arm": outcome["arm"], "setting": outcome["setting"],
                      "rollout_index": outcome["rollout_index"]})

    # This receipt contains every job before any paid call can start.
    persist()
    interrupted = False
    pending_indices = iter(prepared)
    active = {}
    dispatch({"kind": "campaign_start", "campaign_id": campaign["campaign_id"],
              "rollouts": len(outcomes), "max_parallel_rollouts": parallel})
    with ThreadPoolExecutor(max_workers=parallel, thread_name_prefix="color-campaign") as executor:
        def fill_slots():
            while len(active) < parallel and not stop.is_set():
                index = next(pending_indices, None)
                if index is None:
                    break
                outcomes[index].update(status="running", started_utc=_utc())
                active[executor.submit(worker, index)] = index

        try:
            fill_slots()
            persist()
            while active:
                try:
                    if stop.is_set():
                        interrupted = True
                        campaign["status"] = "interrupting"
                    try:
                        index, event = messages.get(timeout=0.1)
                        handle_event(index, event)
                    except queue.Empty:
                        pass
                    # Process only a bounded number before checking completions.
                    for _ in range(255):
                        try:
                            index, event = messages.get_nowait()
                        except queue.Empty:
                            break
                        handle_event(index, event)
                    finished = [future for future in active if future.done()]
                    for future in finished:
                        index = active[future]
                        try:
                            update = future.result()
                        except AuthenticationStop:
                            raise
                        except BaseException as exc:
                            update = {"status": "failed", "error": _error(exc, "worker"), "finished_utc": _utc()}
                        outcomes[index].update(update)
                        # Durable completion precedes callbacks and new work.
                        persist()
                        del active[future]
                        if not interrupted:
                            dispatch({"kind": "campaign_rollout_complete", "campaign_id": campaign["campaign_id"],
                                      "job_index": index, "job_id": outcomes[index]["job_id"],
                                      "arm": outcomes[index]["arm"], "setting": outcomes[index]["setting"],
                                      "rollout_index": outcomes[index]["rollout_index"], **update})
                    fill_slots()
                    if time.monotonic() - last_saved >= 2:
                        persist()
                except KeyboardInterrupt:
                    interrupted = True
                    stop.set()
                    campaign["status"] = "interrupting"
                    persist()
        except KeyboardInterrupt:
            # A signal during initial submission still needs to settle workers.
            interrupted = True
            stop.set()
            campaign["status"] = "interrupting"
            while active:
                try:
                    index, event = messages.get(timeout=0.1)
                    handle_event(index, event)
                except queue.Empty:
                    pass
                for future in [future for future in active if future.done()]:
                    index = active.pop(future)
                    try:
                        outcomes[index].update(future.result())
                    except AuthenticationStop:
                        raise
                    except BaseException as exc:
                        outcomes[index].update(status="failed", error=_error(exc, "worker"))
                persist()

    for outcome in outcomes:
        if outcome["status"] not in _TERMINAL | {"queued"}:
            outcome.update(status="interrupted", error={"type": "CampaignStopped", "category": "campaign"},
                           finished_utc=_utc())
    while not messages.empty():
        index, event = messages.get_nowait()
        handle_event(index, event)
    summary = _summarize(outcomes)
    if interrupted or stop.is_set():
        campaign["status"] = "interrupted"
    elif summary["failed_rollouts"] == len(outcomes):
        campaign["status"] = "failed"
    elif summary["rollouts_with_errors"] or campaign["callback_errors"]:
        campaign["status"] = "complete_with_errors"
    elif summary["pending_responses"]:
        campaign["status"] = "complete_pending_responses"
    else:
        campaign["status"] = "complete"
    campaign["finished_utc"] = _utc()
    persist()
    if interrupted:
        raise KeyboardInterrupt()
    dispatch({"kind": "campaign_end", "campaign_id": campaign["campaign_id"],
              "status": campaign["status"], "summary": campaign["summary"]})
    if campaign["callback_errors"] and campaign["status"] == "complete":
        campaign["status"] = "complete_with_errors"
    persist()
    return campaign
