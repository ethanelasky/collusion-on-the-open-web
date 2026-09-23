"""Run one isolated rollout for every available setting at the same time.

Workers own separate rollout directories. The calling thread alone dispatches
notebook callbacks and writes the batch manifest. Full trajectories are returned
in memory; the manifest points to their durable JSON, event, source, and HTML
artifacts instead of duplicating all model responses.
"""
from __future__ import annotations

import copy
import hashlib
import math
import queue
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from ai_collusion.auth_stop import AuthenticationStop

from .config import arm_configs, make_plan
from .game import _save_json, load_rollout, run_rollout
from .model import ModelAgent
from .notebook import PromptEditor, normalize_config
from .view import save_html


def _error(exc, category):
    # Arbitrary exception messages can contain provider credentials or bodies.
    return {"type": type(exc).__name__, "category": category}


def _paths(directory):
    candidates = {"json": directory / "rollout.json", "jsonl": directory / "events.jsonl",
                  "html": directory / "transcript.html", "source": directory / "source"}
    return {name: str(path) for name, path in candidates.items() if path.exists()}


def _summary(outcomes, callback_errors):
    summaries = [item.get("summary") or {} for item in outcomes]
    costs = [s["cost"] for s in summaries
             if type(s.get("cost")) in (int, float) and math.isfinite(s["cost"]) and s["cost"] >= 0]
    return {
        "arms": len(outcomes),
        "completed_arms": sum(item["status"].startswith("complete") for item in outcomes),
        "failed_arms": sum(item["status"] == "failed" for item in outcomes),
        "interrupted_arms": sum(item["status"] == "interrupted" for item in outcomes),
        "arms_with_errors": sum(bool(item.get("error")) or bool(s.get("errors"))
                                or bool(s.get("infrastructure_errors"))
                                for item, s in zip(outcomes, summaries)),
        "planned_rounds": sum(item["config"]["rounds"] for item in outcomes),
        "recorded_rounds": sum(s.get("recorded_rounds", 0) for s in summaries),
        "matched": sum(s.get("matched", 0) for s in summaries),
        "total_actions": sum(s.get("total_actions", 0) for s in summaries),
        "infrastructure_errors": sum(s.get("infrastructure_errors", 0) for s in summaries),
        "pending_responses": sum(s.get("pending_responses", 0) for s in summaries),
        "reported_cost_usd": sum(costs) if costs else None,
        "arms_with_reported_cost": len(costs),
        "callback_errors": len(callback_errors),
        "cost_note": "Saved provider-reported costs only; missing costs are not zero.",
    }


def run_all_configs(base_config, model_config, *, output_dir, prompt_additions=None,
                    prompt_transform=None, on_event=None) -> dict:
    """Run the available settings concurrently and return ordered outcomes.

    The settings are guessing only, sequential counter use, and simultaneous
    counter use. The simultaneous setting always uses its shared round deadline;
    the batch does not select a separate timing mode.

    ``outcomes`` follows ``arm_configs`` order. ``trajectories`` maps each arm to
    its full saved rollout, including a partial rollout after a failure when one
    exists. HTTP 401 stops the whole experiment; other failed arms do not cancel
    their peers. Prompt additions and model
    settings are copied before workers start. Transforms run on the caller's
    thread during prompt preparation, never concurrently in model workers.

    Every callback receives the rollout event plus ``batch_id``, ``arm_index``,
    ``arm``, and ``setting``. Callback failures are recorded and do not discard
    experiment results. An interrupt requests that workers stop at their next
    journal event; an already running provider call may take time to return.
    """
    config = normalize_config(base_config)
    configs = arm_configs(config)
    directory = Path(output_dir).expanduser().resolve()
    if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
        raise FileExistsError(f"Batch output directory is not empty: {directory}")
    model_snapshot = copy.deepcopy(model_config)
    additions_snapshot = copy.deepcopy(prompt_additions if prompt_additions is not None else {})
    outcomes = []
    prepared = {}
    for index, arm_config in enumerate(configs):
        arm = arm_config.arm
        arm_dir = directory / f"{index:02d}-{arm}"
        outcome = {"arm": arm, "config": arm_config.to_dict(), "status": "queued",
                   "output_dir": str(arm_dir), "summary": {}, "artifact_paths": {},
                   "error": None, "progress": {"rounds_completed": 0, "actions_completed": 0}}
        try:
            plan = make_plan(arm_config)
            editor = PromptEditor(additions=copy.deepcopy(additions_snapshot), transform=prompt_transform)
            prompts = editor.resolve(arm_config, plan=plan)
            outcome["namespace"] = plan["namespace"]
            outcome["prompt_sha256"] = {
                role: hashlib.sha256(text.encode()).hexdigest() for role, text in prompts.items()}
            prepared[index] = {"prompts": prompts, "plan": plan}
        except Exception as exc:
            outcome.update(status="failed", error=_error(exc, "prompt_preparation"))
        outcomes.append(outcome)
    if len({outcome["arm"] for outcome in outcomes}) != len(outcomes):
        raise ValueError("Each available configuration must have a distinct arm name")

    directory.mkdir(parents=True, exist_ok=True)
    batch = {
        "schema": "color-game-batch/v1", "batch_id": str(uuid.uuid4()), "status": "running",
        "created_utc": datetime.now(timezone.utc).isoformat(), "output_dir": str(directory),
        "base_config": config.to_dict(), "model": asdict(model_snapshot),
        "max_parallel_rollouts": len(configs),
        "artifact_paths": {"manifest": str(directory / "batch.json")},
        "outcomes": outcomes, "trajectories": {}, "callback_errors": [], "summary": {},
    }

    def persist():
        batch["summary"] = _summary(outcomes, batch["callback_errors"])
        # Only this calling thread writes the manifest.
        _save_json(directory / "batch.json", {k: v for k, v in batch.items() if k != "trajectories"})

    def dispatch(event):
        if on_event is None:
            return
        try:
            on_event(copy.deepcopy(event))
        except Exception as exc:
            batch["callback_errors"].append({**_error(exc, "callback"),
                                             "kind": event.get("kind"), "arm": event.get("arm")})

    messages = queue.Queue()
    stop = threading.Event()

    def worker(index):
        arm_config = configs[index]
        arm_dir = Path(outcomes[index]["output_dir"])
        cancelled = False

        def emit(event):
            nonlocal cancelled
            if stop.is_set() and not cancelled:
                cancelled = True
                raise KeyboardInterrupt()
            messages.put(("event", index, event))

        trajectory = None
        failure = None
        status = "failed"
        try:
            emit({"kind": "batch_arm_started"})
            trajectory = run_rollout(
                arm_config, ModelAgent(copy.deepcopy(model_snapshot)), ModelAgent(copy.deepcopy(model_snapshot)),
                output_dir=arm_dir, system_prompts=prepared[index]["prompts"],
                plan=prepared[index]["plan"], on_event=emit)
            status = trajectory["status"]
        except AuthenticationStop:
            raise
        except BaseException as exc:
            failure = _error(exc, "rollout")
            status = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
            if (arm_dir / "rollout.json").is_file():
                try:
                    trajectory = load_rollout(arm_dir)
                except Exception as load_error:
                    failure["partial_load_error"] = _error(load_error, "partial_rollout_load")
        if trajectory is not None:
            try:
                save_html(trajectory, arm_dir / "transcript.html")
            except Exception as exc:
                failure = failure or _error(exc, "html_export")
                status = "failed"
        return {"status": status, "error": failure,
                "summary": copy.deepcopy(trajectory.get("summary", {})) if trajectory else {},
                "artifact_paths": _paths(arm_dir)}, trajectory

    persist()
    dispatch({"kind": "batch_start", "batch_id": batch["batch_id"], "arms": len(configs)})
    interrupted = False
    with ThreadPoolExecutor(max_workers=len(configs), thread_name_prefix="color-arm") as executor:
        for index in prepared:
            future = executor.submit(worker, index)
            future.add_done_callback(lambda completed, index=index: messages.put(("done", index, completed)))
        pending = set(prepared)
        while pending:
            kind, index, value = None, None, None
            try:
                kind, index, value = messages.get(timeout=0.1)
                outcome = outcomes[index]
                context = {"batch_id": batch["batch_id"], "arm_index": index,
                           "arm": outcome["arm"], "setting": outcome["config"]["setting"]}
                if kind == "event":
                    if value["kind"] == "batch_arm_started":
                        outcome["status"] = "running"
                    progress = outcome["progress"]
                    progress["last_event"] = value["kind"]
                    if "round_index" in value:
                        progress["round_index"] = value["round_index"]
                    if value["kind"] == "round_end":
                        progress["rounds_completed"] += 1
                    if value["kind"] == "action_result":
                        progress["actions_completed"] += 1
                    if not interrupted:
                        dispatch({**value, **context})
                    if value["kind"] in {"batch_arm_started", "round_end", "model_error"}:
                        persist()
                else:
                    try:
                        update, trajectory = value.result()
                    except AuthenticationStop:
                        raise
                    except BaseException as exc:
                        update, trajectory = {"status": "failed", "error": _error(exc, "worker")}, None
                    outcome.update(update)
                    if trajectory is not None:
                        batch["trajectories"][outcome["arm"]] = trajectory
                    pending.remove(index)
                    persist()
                    if not interrupted:
                        dispatch({"kind": "batch_arm_complete", **context, "status": outcome["status"],
                                  "summary": outcome["summary"], "error": outcome["error"]})
            except queue.Empty:
                continue
            except KeyboardInterrupt:
                interrupted = True
                stop.set()
                batch["status"] = "interrupting"
                # A signal can interrupt handling after a completed future was
                # removed from the queue but before its outcome was recorded.
                # Keep that completion available so shutdown cannot wait forever.
                if kind == "done" and index in pending:
                    messages.put((kind, index, value))
                persist()

    # Dict iteration now follows config order, regardless of completion order.
    batch["trajectories"] = {item["arm"]: batch["trajectories"][item["arm"]]
                             for item in outcomes if item["arm"] in batch["trajectories"]}
    batch["summary"] = _summary(outcomes, batch["callback_errors"])
    if interrupted:
        batch["status"] = "interrupted"
    elif batch["summary"]["failed_arms"] == len(outcomes):
        batch["status"] = "failed"
    elif batch["summary"]["arms_with_errors"] or batch["callback_errors"]:
        batch["status"] = "complete_with_errors"
    elif batch["summary"]["pending_responses"]:
        batch["status"] = "complete_pending_responses"
    else:
        batch["status"] = "complete"
    batch["finished_utc"] = datetime.now(timezone.utc).isoformat()
    persist()
    if interrupted:
        raise KeyboardInterrupt()
    dispatch({"kind": "batch_end", "batch_id": batch["batch_id"],
              "status": batch["status"], "summary": batch["summary"]})
    # A final callback failure is also part of the durable receipt.
    if batch["callback_errors"] and batch["status"] == "complete":
        batch["status"] = "complete_with_errors"
    persist()
    return batch
