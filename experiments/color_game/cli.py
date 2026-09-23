"""Launch, inspect, stop, and recover color campaigns outside Jupyter.

Uses the existing model loader, atomic artifact storage, and stop-file control.
Like covert_channel.overnight, each launch runs from a saved source snapshot.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from ai_collusion.client import ModelConfig
from ai_collusion.runner import load_models, load_repo_env
from ai_collusion.run_storage import stable_sha256, write_json
from experiments.covert_channel.control import RunControl
from .config import COLORS, SETTINGS, GameConfig


def _now():
    return datetime.now(timezone.utc).isoformat()


def _read(path):
    return json.loads(Path(path).read_text())


def _default_env():
    root = Path(__file__).resolve().parents[2]
    path = root / ".env"
    if path.exists():
        return path
    try:
        common = Path(subprocess.check_output(
            ["git", "rev-parse", "--git-common-dir"], cwd=root, text=True,
            stderr=subprocess.DEVNULL).strip())
        return (root / common).resolve().parent / ".env"
    except (OSError, subprocess.CalledProcessError):
        return path


def _snapshot(out):
    root = Path(__file__).resolve().parents[2]
    source = out / "source"
    paths = [p for p in (root / "ai_collusion").rglob("*")
             if p.is_file() and p.suffix in {".py", ".txt", ".json", ".html", ".yaml"}]
    paths += list((root / "experiments/color_game").glob("*.py"))
    paths += [root / "experiments/__init__.py", root / "experiments/covert_channel/__init__.py",
              root / "experiments/covert_channel/control.py"]
    hashes = {}
    for path in sorted(paths):
        relative = path.relative_to(root)
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        hashes[str(relative)] = hashlib.sha256(target.read_bytes()).hexdigest()
    write_json(out / "source-sha256.json", hashes)
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root,
                                         text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None
    return {"git_commit": commit, "source_dir": str(source),
            "source_sha256": stable_sha256(hashes), "python": sys.version}


def _verify(out):
    launch, request = _read(out / "launch.json"), _read(out / "request.json")
    if stable_sha256(request) != launch["request_sha256"]:
        raise ValueError("Saved launch inputs changed")
    from .campaign import selected_settings
    settings = selected_settings(request.get("selected_settings", SETTINGS))
    if (list(settings) != launch.get("selected_settings", list(SETTINGS))
            or launch["planned_rollouts"] != len(settings) * request["rollouts_per_setting"]
            or launch["planned_rounds"] != len(settings) * request["rollouts_per_setting"] * request["game"]["rounds"]):
        raise ValueError("Saved launch settings or counts changed")
    hashes = _read(out / "source-sha256.json")
    if stable_sha256(hashes) != launch["source_sha256"]:
        raise ValueError("Saved source manifest changed")
    for relative, expected in hashes.items():
        path = (out / "source" / relative).resolve()
        if not path.is_relative_to((out / "source").resolve()):
            raise ValueError("Source manifest path is outside the snapshot")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"Saved source changed: {relative}")
    return request


def _acquire_lock(out):
    lock = (out / ".run.lock").open("a+")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise ValueError("This campaign already has a running supervisor") from None
    return lock


def _is_active(out):
    try:
        lock = _acquire_lock(out)
    except ValueError:
        return True
    lock.close()
    return False


def prepare(args):
    from .campaign import selected_settings
    settings = selected_settings(getattr(args, "settings", SETTINGS))
    env_file = (args.env_file or _default_env()).expanduser().resolve()
    load_repo_env(env_file)
    model = load_models(args.models, [args.model])[0]
    if model.transport != "stub" and not model.api_key():
        raise ValueError(f"Missing key in {model.api_key_env}; set it in the shell or --env-file")
    config = GameConfig(colors=tuple(args.colors.split(",")), rounds=args.rounds,
                        actions_per_agent=args.actions_per_agent, seed=args.seed,
                        rollout_index=args.start_index, round_time_limit_s=args.round_time_limit,
                        fuzz_bob=not args.no_fuzz_bob)
    if min(args.rollouts, args.workers) < 1:
        raise ValueError("Rollout and worker counts must be positive")
    additions = _read(args.prompt_additions) if args.prompt_additions else {}
    from .notebook import PromptEditor
    PromptEditor(additions=additions).resolve(config)
    out = args.out.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=False)
    request = {"schema": "color-game-launch/v1", "game": asdict(config), "model": asdict(model),
               "selected_settings": list(settings),
               "rollouts_per_setting": args.rollouts, "max_parallel_rollouts": args.workers,
               "env_file": str(env_file), "prompt_additions": additions,
               "keep_awake": not args.no_caffeinate}
    write_json(out / "request.json", request)
    provenance = _snapshot(out)
    shutil.copyfile(args.models, out / "models.yaml")
    write_json(out / "launch.json", {"schema": "color-game-supervisor/v1", "created_utc": _now(),
               "request_sha256": stable_sha256(request), **provenance,
               "selected_settings": list(settings),
               "planned_rollouts": len(settings) * args.rollouts,
               "planned_rounds": len(settings) * args.rollouts * args.rounds,
               "campaign_dir": str(out / "runs"), "stop_file": str(out / "stop-request.json"),
               "recovery_policy": "Continue unstarted jobs; retain completed and interrupted trajectories."})
    return out


def start_worker(out, *, resume=False, foreground=False):
    _verify(out)
    lock = _acquire_lock(out)
    try:
        if resume:
            if not (out / "runs/campaign.json").is_file():
                raise ValueError("No saved campaign exists to resume")
            stop = out / "stop-request.json"
            if stop.exists():
                archived = out / "stop-history" / f"{uuid.uuid4().hex}.json"
                archived.parent.mkdir(exist_ok=True)
                stop.replace(archived)
        elif (out / "runs").exists():
            raise ValueError("Campaign records already exist; use resume")
        command = [sys.executable, "-u", "-m", "experiments.color_game.cli", "_worker",
                   "--out", str(out), "--lock-fd", str(lock.fileno())]
        if resume:
            command.append("--resume")
        process_path = out / "process.json"
        if process_path.exists():
            write_json(out / "process-history" / f"{uuid.uuid4().hex}.json", _read(process_path))
        with (out / "supervisor.log").open("ab") as log:
            process = subprocess.Popen(command, cwd=out / "source", stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                                       pass_fds=(lock.fileno(),))
        write_json(process_path, {"pid": process.pid, "command": command, "started_utc": _now(),
                                 "resume": resume, "log": str(out / "supervisor.log")})
    finally:
        # Do not flock(LOCK_UN): the child inherited this same lock description.
        lock.close()
    if foreground:
        return process.wait()
    print(json.dumps({"status": "started", "pid": process.pid, "out": str(out),
                      "planned_rollouts": _read(out / "launch.json")["planned_rollouts"],
                      "log": str(out / "supervisor.log")}), flush=True)
    return 0


def worker(args):
    out = args.out.resolve()
    lock = os.fdopen(args.lock_fd, "a+") if args.lock_fd is not None else _acquire_lock(out)
    try:
        request = _verify(out)
        load_repo_env(Path(request["env_file"]))
        model = ModelConfig(**request["model"])
        if model.transport != "stub" and not model.api_key():
            raise ValueError(f"Missing key in {model.api_key_env}")
        from .campaign import run_campaign
        from .campaign_report import update_report
    except Exception as exc:
        write_json(out / "worker.json", {"status": "failed", "pid": os.getpid(),
                   "finished_utc": _now(), "exit_code": 1, "error": {"type": type(exc).__name__,
                   "phase": "startup"}})
        lock.close()
        return 1
    control = RunControl(out / "stop-request.json")
    monitor_done = threading.Event()
    status = {"status": "running", "pid": os.getpid(), "started_utc": _now(),
              "resume": args.resume, "report_errors": []}
    awake = None
    if request["keep_awake"] and shutil.which("caffeinate"):
        awake = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        status["caffeinate_pid"] = awake.pid
    write_json(out / "worker.json", status)

    def signal_stop(signum, frame):
        write_json(out / "stop-request.json", {"reason": "process_signal", "signal": signum, "at_utc": _now()})
        control.event.set()

    previous_handlers = {s: signal.signal(s, signal_stop) for s in (signal.SIGTERM, signal.SIGINT)}

    def monitor():
        while not monitor_done.wait(0.25):
            control.cancelled()

    watcher = threading.Thread(target=monitor, name="color-stop-file", daemon=True)
    watcher.start()
    last_report = 0.0

    def progress(event):
        nonlocal last_report
        kind = event["kind"]
        if kind in {"campaign_start", "campaign_rollout_complete", "campaign_end", "round_end", "model_error"}:
            print(json.dumps({k: v for k, v in event.items() if k not in {"config", "summary", "artifact_paths"}}), flush=True)
        if time.monotonic() - last_report >= 3 or kind in {"campaign_start", "campaign_end"}:
            try:
                update_report(out / "runs")
            except Exception as exc:
                if type(exc).__name__ not in status["report_errors"]:
                    status["report_errors"].append(type(exc).__name__)
            last_report = time.monotonic()

    code = 1
    try:
        result = run_campaign(GameConfig(**request["game"]), model, output_dir=out / "runs",
                              settings=request.get("selected_settings", SETTINGS),
                              rollouts_per_setting=request["rollouts_per_setting"],
                              max_parallel_rollouts=request["max_parallel_rollouts"],
                              prompt_additions=request["prompt_additions"], on_event=progress,
                              stop_event=control.event, resume=args.resume)
        status.update(status=result["status"], summary=result["summary"])
        code = 0 if result["status"] == "complete" else 1
    except KeyboardInterrupt:
        status["status"] = "interrupted"
        code = 130
    except Exception as exc:
        status.update(status="failed", error={"type": type(exc).__name__})
    finally:
        monitor_done.set()
        watcher.join(timeout=1)
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
        if (out / "runs/campaign.json").exists():
            try:
                update_report(out / "runs")
            except Exception as exc:
                status["report_errors"].append(type(exc).__name__)
        status.update(finished_utc=_now(), exit_code=code)
        write_json(out / "worker.json", status)
        if awake:
            awake.terminate()
            awake.wait(timeout=5)
        lock.close()
    print(json.dumps(status), flush=True)
    return code


def inspect_status(out):
    out = out.resolve()
    launch = _read(out / "launch.json")
    active = _is_active(out)
    worker_state = _read(out / "worker.json") if (out / "worker.json").exists() else {}
    campaign = _read(out / "runs/campaign.json") if (out / "runs/campaign.json").exists() else {}
    state = worker_state.get("status", "starting")
    if not active and state in {"starting", "running", "interrupting"}:
        state = "interrupted"
    return {"status": state, "supervisor_active": active, "planned_rollouts": launch["planned_rollouts"],
            "planned_rounds": launch["planned_rounds"], "summary": campaign.get("summary", {}),
            "selected_settings": launch.get("selected_settings", list(SETTINGS)),
            "settings": campaign.get("settings", {}), "out": str(out),
            "log": str(out / "supervisor.log"), "report": str(out / "runs/index.html")}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    launch = commands.add_parser("launch", help="Start a detached campaign from a saved source snapshot")
    launch.add_argument("--out", type=Path, required=True)
    launch.add_argument("--models", type=Path, default=Path(__file__).with_name("models.yaml"))
    launch.add_argument("--model", default="gpt-5.6-luna-high")
    launch.add_argument("--env-file", type=Path)
    launch.add_argument("--rollouts", type=int, default=50, help="Rollouts per setting")
    launch.add_argument("--settings", nargs="+", choices=SETTINGS, default=list(SETTINGS),
                        help="Settings to run, in submission order (default: all three)")
    launch.add_argument("--workers", type=int, default=50, help="Maximum active rollouts across all settings")
    launch.add_argument("--rounds", type=int, default=5)
    launch.add_argument("--actions-per-agent", type=int, default=8)
    launch.add_argument("--colors", default=",".join(COLORS))
    launch.add_argument("--seed", type=int, default=17)
    launch.add_argument("--start-index", type=int, default=0)
    launch.add_argument("--round-time-limit", type=float, default=180)
    launch.add_argument("--no-fuzz-bob", action="store_true")
    launch.add_argument("--prompt-additions", type=Path, help="JSON with optional alice and bob instruction additions")
    launch.add_argument("--no-caffeinate", action="store_true")
    launch.add_argument("--foreground", action="store_true", help="Wait for the detached supervisor to finish")
    for command in ("status", "stop", "resume"):
        sub = commands.add_parser(command)
        sub.add_argument("--out", type=Path, required=True)
        if command == "resume":
            sub.add_argument("--foreground", action="store_true")
    internal = commands.add_parser("_worker", help=argparse.SUPPRESS)
    internal.add_argument("--out", type=Path, required=True)
    internal.add_argument("--lock-fd", type=int)
    internal.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "launch":
            return start_worker(prepare(args), foreground=args.foreground)
        if args.command == "resume":
            return start_worker(args.out.resolve(), resume=True, foreground=args.foreground)
        if args.command == "_worker":
            return worker(args)
        if args.command == "stop":
            if not (args.out / "launch.json").is_file():
                raise ValueError("Not a color campaign launch directory")
            write_json(args.out / "stop-request.json", {"reason": "user_requested_stop", "at_utc": _now()})
            print(json.dumps({"status": "stop_requested", "out": str(args.out.resolve())}))
            return 0
        print(json.dumps(inspect_status(args.out), indent=2))
        return 0
    except (ValueError, OSError, KeyError) as exc:
        # Do not print provider bodies, environment values, or arbitrary JSON.
        parser.exit(1, f"Color campaign command failed: {type(exc).__name__}. Check the saved status and configuration.\n")


if __name__ == "__main__":
    raise SystemExit(main())
