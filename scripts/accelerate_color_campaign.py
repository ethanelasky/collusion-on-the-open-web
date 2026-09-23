"""Prepare and start an audited concurrency-only resume of an async campaign.

Preparation is offline. Starting requires the original supervisor lock to be
free. Old supervisors cannot drain: stop interruptions remain in the fixed
sample, and only queued jobs with empty directories can start on resume.
Original request, launch, source manifest, and source files remain unchanged.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import Counter, deque
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

CAMPAIGN_MODULE = "experiments/color_game/campaign.py"
RUNNER_MODULE = "scripts/accelerate_color_campaign.py"
ALLOWED_OVERLAYS = {CAMPAIGN_MODULE, RUNNER_MODULE}


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stable_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=f".{path.name}.", delete=False) as f:
            temporary = Path(f.name)
            json.dump(value, f, indent=2, ensure_ascii=False); f.write("\n")
            f.flush(); os.fsync(f.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def checked_path(root, relative):
    file = (root / relative).resolve()
    if not file.is_relative_to(root.resolve()):
        raise ValueError("Source path is outside its snapshot")
    return file


def verify_original(campaign_dir):
    campaign_dir = Path(campaign_dir).resolve()
    request, launch = read(campaign_dir / "request.json"), read(campaign_dir / "launch.json")
    hashes = read(campaign_dir / "source-sha256.json")
    if stable_sha(request) != launch.get("request_sha256"):
        raise ValueError("Original request changed")
    if stable_sha(hashes) != launch.get("source_sha256"):
        raise ValueError("Original source manifest changed")
    if Path(launch["source_dir"]).resolve() != campaign_dir / "source":
        raise ValueError("Original launch source directory mismatch")
    if (request.get("selected_settings") != ["async_counter"]
            or launch.get("selected_settings") != ["async_counter"]):
        raise ValueError("Acceleration is restricted to async_counter")
    n, rounds = request["rollouts_per_setting"], request["game"]["rounds"]
    if launch.get("planned_rollouts") != n or launch.get("planned_rounds") != n * rounds:
        raise ValueError("Original launch counts changed")
    if Path(launch["campaign_dir"]).resolve() != campaign_dir / "runs":
        raise ValueError("Original launch campaign directory mismatch")
    if Path(launch["stop_file"]).resolve() != campaign_dir / "stop-request.json":
        raise ValueError("Original stop-file path mismatch")
    for relative, digest in hashes.items():
        if sha(checked_path(campaign_dir / "source", relative)) != digest:
            raise ValueError(f"Original source changed: {relative}")
    return request, launch, hashes


def prepare_execution(campaign_dir, workers, rpm, plan_paths=(), execution_id=None, campaign_overlay=None):
    """Clone the original runtime and freeze an explicit operational receipt."""
    campaign_dir = Path(campaign_dir).resolve()
    if type(workers) is not int or not 1 <= workers <= 50:
        raise ValueError("workers must be an integer from 1 to 50")
    if type(rpm) not in (int, float) or not math.isfinite(rpm) or not 0 < rpm <= 600:
        raise ValueError("rpm must be a finite positive number at most 600")
    request, launch, hashes = verify_original(campaign_dir)
    manifest = read(campaign_dir / "runs/campaign.json")
    previous = manifest.get("max_parallel_rollouts")
    if type(previous) is not int or previous < 1:
        raise ValueError("Saved parallel worker count is invalid")
    parallel = min(workers, request["rollouts_per_setting"])
    if parallel <= previous:
        raise ValueError("This acceleration must increase the saved worker count")
    if (manifest.get("model") != request["model"]
            or manifest.get("rollouts_per_setting") != request["rollouts_per_setting"]
            or manifest.get("selected_settings") != request["selected_settings"]):
        raise ValueError("Campaign differs from its original request")
    if request["model"].get("transport") != "stub" and not plan_paths:
        raise ValueError("Live acceleration requires frozen confirmation plan receipts")
    fixed_plans = [{"path": str(Path(p).expanduser().resolve()), "sha256": sha(Path(p).expanduser().resolve())}
                   for p in plan_paths]
    ident = execution_id or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
    if not isinstance(ident, str) or not ident or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in ident):
        raise ValueError("execution_id must be one safe directory component")
    execution = campaign_dir / "executions" / ident
    execution.mkdir(parents=True, exist_ok=False)
    source = execution / "source"
    source.mkdir()
    for relative in hashes:
        target = checked_path(source, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(checked_path(campaign_dir / "source", relative), target)
    overlay = Path(campaign_overlay) if campaign_overlay else Path(__file__).resolve().parents[1] / CAMPAIGN_MODULE
    shutil.copyfile(overlay, source / CAMPAIGN_MODULE)
    runner = source / RUNNER_MODULE
    runner.parent.mkdir(exist_ok=True)
    shutil.copyfile(Path(__file__).resolve(), runner)
    new_hashes = {relative: sha(checked_path(source, relative)) for relative in sorted(set(hashes) | {RUNNER_MODULE})}
    changes = {relative: {"old_sha256": hashes.get(relative), "new_sha256": digest}
               for relative, digest in new_hashes.items() if hashes.get(relative) != digest}
    if set(changes) - ALLOWED_OVERLAYS or CAMPAIGN_MODULE not in changes:
        raise ValueError("Execution requires exactly the approved campaign orchestration overlay")
    originals = {name: sha(campaign_dir / name) for name in ("request.json", "launch.json", "source-sha256.json")}
    receipt = {"schema": "color-game-acceleration/v1", "created_utc": now(), "execution_id": ident,
               "campaign_dir": str(campaign_dir), "execution_dir": str(execution),
               "previous_parallel": previous, "new_parallel": parallel, "requests_per_minute": float(rpm),
               "gate_max_active": parallel, "selected_settings": ["async_counter"],
               "planned_rollouts": request["rollouts_per_setting"], "planned_rounds": launch["planned_rounds"],
               "original_receipt_sha256": originals, "original_source_sha256": hashes,
               "execution_source_sha256": new_hashes, "allowed_overlays": sorted(ALLOWED_OVERLAYS),
               "changed_source_files": changes, "fixed_plan_receipts": fixed_plans,
               "model_config": request["model"], "game_config": request["game"],
               "prompt_additions": request["prompt_additions"], "env_file": request["env_file"],
               "failure_policy": "Preserve terminal and partial jobs; start only queued jobs with empty output directories; do not replace interruptions",
               "pacing_policy": "Space API starts at the fixed RPM; RequestGate reduces concurrent requests after throttling and recovers after successes",
               "stop_policy": "Use the original stop-request.json; active requests can finish after a stop and their partial trajectories remain retained"}
    save(execution / "operational-plan.json", receipt)
    (execution / "operational-plan.sha256").write_text(sha(execution / "operational-plan.json") + "\n")
    verify_execution(execution)
    return execution


def verify_execution(execution_dir, expected_amendment_sha256=None):
    execution = Path(execution_dir).resolve()
    file = execution / "operational-plan.json"
    digest = sha(file)
    expected = expected_amendment_sha256 or (execution / "operational-plan.sha256").read_text().strip()
    if digest != expected:
        raise ValueError("Operational plan hash changed")
    plan = read(file)
    if plan.get("schema") != "color-game-acceleration/v1" or plan.get("execution_dir") != str(execution):
        raise ValueError("Invalid execution receipt")
    original = Path(plan["campaign_dir"]).resolve()
    if execution.parent != original / "executions":
        raise ValueError("Execution is outside its original campaign")
    request, launch, hashes = verify_original(original)
    for name, expected_hash in plan["original_receipt_sha256"].items():
        if name not in {"request.json", "launch.json", "source-sha256.json"} or sha(original / name) != expected_hash:
            raise ValueError("Original frozen receipt changed")
    if set(plan["original_receipt_sha256"]) != {"request.json", "launch.json", "source-sha256.json"}:
        raise ValueError("Missing original frozen receipt")
    if hashes != plan["original_source_sha256"]:
        raise ValueError("Original source mapping changed")
    if (request["model"] != plan["model_config"] or request["game"] != plan["game_config"]
            or request["prompt_additions"] != plan["prompt_additions"] or request["env_file"] != plan["env_file"]
            or request["rollouts_per_setting"] != plan["planned_rollouts"]
            or launch["planned_rounds"] != plan["planned_rounds"]):
        raise ValueError("Execution changed model, game, count, prompts, or environment path")
    if set(plan.get("allowed_overlays", [])) != ALLOWED_OVERLAYS:
        raise ValueError("Unknown source override allowlist")
    if set(plan["execution_source_sha256"]) != set(hashes) | {RUNNER_MODULE}:
        raise ValueError("Execution source file set changed")
    changed = {}
    for relative, expected_hash in plan["execution_source_sha256"].items():
        if sha(checked_path(execution / "source", relative)) != expected_hash:
            raise ValueError(f"Execution source changed: {relative}")
        if hashes.get(relative) != expected_hash:
            changed[relative] = {"old_sha256": hashes.get(relative), "new_sha256": expected_hash}
    if changed != plan["changed_source_files"] or set(changed) - ALLOWED_OVERLAYS:
        raise ValueError("Unapproved execution source override")
    for fixed in plan["fixed_plan_receipts"]:
        if sha(Path(fixed["path"])) != fixed["sha256"]:
            raise ValueError("A frozen confirmation plan changed")
    return plan


def acquire_lock(campaign_dir):
    lock = (Path(campaign_dir) / ".run.lock").open("a+")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise ValueError("The original campaign still has an active supervisor") from None
    return lock


def start_execution(execution_dir, foreground=False):
    execution = Path(execution_dir).resolve()
    plan = verify_execution(execution)
    original = Path(plan["campaign_dir"])
    lock = acquire_lock(original)
    try:
        # Recheck under the original lock, before archiving controls or spawning.
        plan = verify_execution(execution)
        current = read(original / "runs/campaign.json")
        if current.get("max_parallel_rollouts") != plan["previous_parallel"]:
            raise ValueError("The campaign worker count changed since preparation")
        queued = [o for o in current.get("outcomes", []) if o.get("status") == "queued"]
        runnable = [o for o in queued if not Path(o["output_dir"]).exists() or not any(Path(o["output_dir"]).iterdir())]
        if not runnable:
            raise ValueError("No queued jobs with empty output directories remain")
        with (execution / "start-receipt.json").open("x") as stream:
            json.dump({"status": "starting", "started_utc": now(), "plan_sha256": sha(execution / "operational-plan.json")}, stream)
            stream.flush(); os.fsync(stream.fileno())
        stop = original / "stop-request.json"
        if stop.exists():
            archive = original / "stop-history" / (uuid.uuid4().hex + ".json")
            archive.parent.mkdir(exist_ok=True); stop.replace(archive)
        process_file = original / "process.json"
        if process_file.exists():
            save(original / "process-history" / (uuid.uuid4().hex + ".json"), read(process_file))
        command = [sys.executable, "-u", str(execution / "source" / RUNNER_MODULE), "_worker",
                   "--execution", str(execution), "--lock-fd", str(lock.fileno()),
                   "--expected-amendment-sha256", sha(execution / "operational-plan.json")]
        with (original / "supervisor.log").open("ab") as log:
            process = subprocess.Popen(command, cwd=execution / "source", stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                                       pass_fds=(lock.fileno(),))
        metadata = {"pid": process.pid, "command": command, "started_utc": now(), "resume": True,
                    "execution_dir": str(execution), "log": str(original / "supervisor.log")}
        save(process_file, metadata); save(execution / "start-receipt.json", {"status": "started", **metadata})
    finally:
        lock.close()  # The child owns the inherited open-file lock description.
    if foreground:
        return process.wait()
    return metadata


def worker(args):
    execution = args.execution.resolve()
    plan = verify_execution(execution, args.expected_amendment_sha256)
    original = Path(plan["campaign_dir"])
    lock = os.fdopen(args.lock_fd, "a+") if args.lock_fd is not None else acquire_lock(original)
    # The script uses only verified copies from the original runtime plus the
    # explicit campaign overlay. No current-worktree game module is imported.
    sys.path.insert(0, str(execution / "source"))
    from ai_collusion.client import ModelConfig
    from ai_collusion.rate_limit import RequestPacer
    from ai_collusion.runner import load_repo_env
    from experiments.covert_channel.control import RunControl
    from experiments.color_game.config import GameConfig
    from experiments.color_game.campaign import run_campaign
    from experiments.color_game.campaign_report import update_report

    request = read(original / "request.json")
    load_repo_env(Path(request["env_file"]))
    model = ModelConfig(**request["model"])
    if model.transport != "stub" and not model.api_key():
        raise ValueError(f"Missing key in {model.api_key_env}")
    pacer = RequestPacer(plan["requests_per_minute"])
    control = RunControl(original / "stop-request.json", execution / "admission.sqlite", plan["gate_max_active"])
    monitor_done = threading.Event()
    status = {"status": "running", "pid": os.getpid(), "started_utc": now(), "resume": True,
              "execution_dir": str(execution), "report_errors": []}
    awake = None
    if request["keep_awake"] and shutil.which("caffeinate"):
        awake = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        status["caffeinate_pid"] = awake.pid
    save(original / "worker.json", status)
    retry_lock = threading.Lock()
    retry_count = 0
    request_lock = threading.Lock()
    request_counts = Counter()
    starts = deque()
    telemetry_started = time.time()

    def request_event(event):
        with request_lock:
            with (execution / "requests.jsonl").open("a") as stream:
                stream.write(json.dumps(event) + "\n")
            if event["kind"] == "start":
                request_counts["attempts"] += 1
                starts.append(event["time_unix_s"])
            else:
                request_counts["successes" if event["success"] else "failures"] += 1
                if event.get("status_code") == 429:
                    request_counts["http_429"] += 1

    def admission_snapshot():
        at = time.time()
        with request_lock:
            while starts and starts[0] < at - 3600:
                starts.popleft()
            values = {**request_counts, "attempts_last_minute": sum(t >= at-60 for t in starts),
                      "attempts_last_hour": len(starts),
                      "mean_attempt_rpm_since_start": request_counts["attempts"]*60/max(at-telemetry_started, 1)}
        return {"updated_utc": now(), "requests_per_minute": plan["requests_per_minute"],
                "rollout_workers": plan["new_parallel"], "retry_events": retry_count,
                **values, **control.gate.snapshot()}

    @contextmanager
    def attempt():
        # Pace after gate admission so a shared cooldown cannot release a
        # burst of calls whose pacing reservations expired while waiting.
        with control.attempt():
            while True:
                if control.cancelled():
                    raise KeyboardInterrupt()
                until = control.gate.snapshot()["cooldown_until_unix_s"]
                if until > time.time():
                    control.event.wait(min(.25, until-time.time()))
                    continue
                pacer.wait()
                if control.cancelled():
                    raise KeyboardInterrupt()
                if control.gate.snapshot()["cooldown_until_unix_s"] <= time.time():
                    break
            ident, started = uuid.uuid4().hex, time.time()
            request_event({"kind": "start", "request_id": ident, "time_unix_s": started})
            try:
                yield
            except BaseException as exc:
                request_event({"kind": "finish", "request_id": ident, "time_unix_s": time.time(),
                               "success": False, "status_code": getattr(exc, "status_code", None),
                               "duration_s": time.time()-started})
                raise
            else:
                request_event({"kind": "finish", "request_id": ident, "time_unix_s": time.time(),
                               "success": True, "status_code": None, "duration_s": time.time()-started})

    def on_retry(event):
        nonlocal retry_count
        control.on_retry(event)
        safe = {k: event[k] for k in ("status_code", "retryable", "reason", "attempt", "wait_s", "time_unix_s") if k in event}
        with retry_lock:
            retry_count += 1
            with (execution / "retry-events.jsonl").open("a") as stream:
                stream.write(json.dumps(safe) + "\n")

    def signal_stop(signum, frame):
        save(original / "stop-request.json", {"reason": "process_signal", "signal": signum, "at_utc": now()})
        control.event.set()

    previous = {sig: signal.signal(sig, signal_stop) for sig in (signal.SIGTERM, signal.SIGINT)}

    def monitor():
        while not monitor_done.wait(.25):
            control.cancelled()

    monitor_thread = threading.Thread(target=monitor, daemon=True)
    monitor_thread.start()
    last_report = 0.

    def progress(event):
        nonlocal last_report
        kind = event["kind"]
        if kind in {"campaign_start", "campaign_rollout_complete", "campaign_end", "model_error"}:
            print(json.dumps({k: event[k] for k in ("kind", "job_id", "status", "error") if k in event}), flush=True)
        if time.monotonic() - last_report >= 3 or kind in {"campaign_start", "campaign_end"}:
            try:
                update_report(original / "runs")
                save(execution / "admission-status.json", admission_snapshot())
            except Exception as exc:
                if type(exc).__name__ not in status["report_errors"]:
                    status["report_errors"].append(type(exc).__name__)
            last_report = time.monotonic()

    code = 1
    try:
        result = run_campaign(GameConfig(**request["game"]), model, output_dir=original / "runs",
                              settings=request["selected_settings"], rollouts_per_setting=request["rollouts_per_setting"],
                              max_parallel_rollouts=plan["new_parallel"], prompt_additions=request["prompt_additions"],
                              on_event=progress, stop_event=control.event, resume=True,
                              resume_parallel_override={"previous_parallel": plan["previous_parallel"],
                                  "operational_plan_path": str(execution / "operational-plan.json"),
                                  "operational_plan_sha256": sha(execution / "operational-plan.json")},
                              request_attempt_context=attempt, request_on_retry=on_retry)
        status.update(status=result["status"], summary=result["summary"])
        code = 0 if result["status"] == "complete" else 1
    except KeyboardInterrupt:
        status["status"] = "interrupted"; code = 130
    except Exception as exc:
        status.update(status="failed", error={"type": type(exc).__name__})
    finally:
        monitor_done.set(); monitor_thread.join(timeout=1)
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        try:
            update_report(original / "runs")
            save(execution / "admission-status.json", admission_snapshot())
        except Exception as exc:
            status["report_errors"].append(type(exc).__name__)
        status.update(finished_utc=now(), exit_code=code)
        save(original / "worker.json", status); save(execution / "worker-final.json", status)
        if awake:
            awake.terminate(); awake.wait(timeout=5)
        lock.close()
    print(json.dumps({"status": status["status"], "exit_code": code, "execution_dir": str(execution)}), flush=True)
    return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--campaign", type=Path, required=True)
    prepare.add_argument("--workers", type=int, default=8)
    prepare.add_argument("--rpm", type=float, required=True)
    prepare.add_argument("--plan", type=Path, action="append", default=[])
    prepare.add_argument("--execution-id")
    for command in ("verify", "start", "_worker"):
        sub = commands.add_parser(command)
        sub.add_argument("--execution", type=Path, required=True)
        if command == "start":
            sub.add_argument("--foreground", action="store_true")
        if command == "_worker":
            sub.add_argument("--lock-fd", type=int)
            sub.add_argument("--expected-amendment-sha256")
    args = parser.parse_args()
    if args.command == "prepare":
        path = prepare_execution(args.campaign, args.workers, args.rpm, args.plan, args.execution_id)
        print(json.dumps({"status": "prepared", "execution_dir": str(path), "plan_sha256": sha(path / "operational-plan.json")}))
    elif args.command == "verify":
        plan = verify_execution(args.execution)
        print(json.dumps({"status": "verified", "execution_dir": plan["execution_dir"],
                          "workers": plan["new_parallel"], "rpm": plan["requests_per_minute"]}))
    elif args.command == "start":
        value = start_execution(args.execution, foreground=args.foreground)
        if isinstance(value, int):
            return value
        print(json.dumps({"status": "started", **value}))
    else:
        try:
            return worker(args)
        except Exception as exc:
            execution = args.execution.resolve()
            original = execution.parent.parent
            failure = {"status": "failed", "pid": os.getpid(), "finished_utc": now(),
                       "execution_dir": str(execution), "exit_code": 1,
                       "error": {"type": type(exc).__name__, "phase": "startup"}}
            if execution.parent.name == "executions" and (original / "request.json").is_file():
                save(original / "worker.json", failure)
                save(execution / "worker-final.json", failure)
            print(json.dumps(failure), flush=True)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
