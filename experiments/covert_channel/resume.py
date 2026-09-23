"""Continue saved counter sessions without repeating completed model requests.

Only deterministic, direct-tool schedules can be resumed. Completed records are
copied byte for byte. Counter events restore the session store; the last record
restores each private history. An unfinished question replays saved responses
through the original harness and validates every saved request and tool result.
The first request without a saved response is the first new provider call.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from ai_collusion.runner import load_models
from ai_collusion.client import ModelConfig
from .browser import model_messages
from .channel import Channel, GROUP_DIRECTIONS
from .control import RequestGate, RunControl
from .overnight import update_report
from .run import Journal, run_trial, write_json


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


class ReplayBoundary(BaseException):
    """Offline validation reached the first request that needs the provider."""


class ReplayMismatch(BaseException):
    """A saved request cannot be reproduced. Never continue from this state."""


def read_journal(path):
    """Keep a torn final write as provenance, without treating it as a response."""
    raw = Path(path).read_bytes()
    lines = raw.splitlines(keepends=True)
    events, discarded = [], 0
    for index, line in enumerate(lines):
        try:
            events.append(json.loads(line))
        except (ValueError, UnicodeDecodeError):
            if index != len(lines) - 1 or line.endswith(b"\n"):
                raise ValueError(f"Corrupt journal: {path}")
            discarded = len(line)
    return events, discarded


class ResponseReplay:
    def __init__(self, events, fallback=None):
        self.fallback = fallback
        self.requests = []
        self.saved_turns = {}
        self.saved_channels = []
        self.channel_position = 0
        self.verified_turns = set()
        for event in events:
            kind = event.get("kind")
            if kind == "request":
                if self.requests and self.requests[-1][1] is None:
                    raise ValueError("Journal has a request before the previous response")
                self.requests.append([event, None])
            elif kind == "response":
                if not self.requests or self.requests[-1][1] is not None:
                    raise ValueError("Journal response has no matching request")
                request = self.requests[-1][0]
                if any(request[k] != event[k] for k in ("agent_id", "turn")):
                    raise ValueError("Journal response belongs to a different turn")
                self.requests[-1][1] = event["response"]
            elif kind == "turn":
                if event.get("error") or event.get("source") == "model-error":
                    raise ValueError("Cannot resume a question containing a terminal model error")
                self.saved_turns[(event["agent_id"], event["turn"])] = event
            elif kind == "channel":
                self.saved_channels.append(event)
        self.position = 0
        self.replayed = 0

    def __call__(self, cfg, system, messages, **kwargs):
        if self.position < len(self.requests):
            request, response = self.requests[self.position]
            expected = model_messages(request["messages"], cfg.transport)
            if system != request["system"] or messages != expected:
                raise ReplayMismatch(f"Saved request differs at {request['agent_id']} turn {request['turn']}")
            self.position += 1
            if response is not None:
                self.replayed += 1
                return copy.deepcopy(response)
        if self.fallback is None:
            self.assert_consumed()
            raise ReplayBoundary()
        self.assert_consumed()
        return self.fallback(cfg, system, messages, **kwargs)

    def check_event(self, event):
        if event.get("kind") == "channel":
            if self.channel_position < len(self.saved_channels):
                original = self.saved_channels[self.channel_position]
                for key in ("agent_id", "url", "op", "namespace", "key", "counter_mode", "at_global_s",
                            "status", "body", "effects"):
                    if event.get(key) != original.get(key):
                        raise ReplayMismatch(f"Saved counter {key} differs at event {self.channel_position}")
                self.channel_position += 1
            return
        if event.get("kind") != "turn":
            return
        original = self.saved_turns.get((event["agent_id"], event["turn"]))
        if original is not None:
            for key in ("request", "response", "action", "result", "source", "effects", "error"):
                if event.get(key) != original.get(key):
                    raise ReplayMismatch(f"Saved {key} differs at {event['agent_id']} turn {event['turn']}")
            self.verified_turns.add((event["agent_id"], event["turn"]))

    def assert_consumed(self):
        if self.position != len(self.requests):
            raise ReplayMismatch("Question ended before all saved requests were replayed")
        if self.channel_position != len(self.saved_channels) or self.verified_turns != set(self.saved_turns):
            raise ReplayMismatch("Saved channel or turn events were not replayed before continuation")


def restore_records(counter, records):
    """Restore only private visible histories and the original counter state."""
    histories = {}
    for record in records:
        for event in record["channel_events"]:
            result = counter.resolve(event["agent_id"], event["url"], event["at_global_s"])
            if result.body != f"HTTP {event['status']}\n{event['body']}" or result.effects != event["effects"]:
                raise ValueError("Saved counter event cannot be reproduced")
            counter.events[-1] = copy.deepcopy(event)
        histories = {role: copy.deepcopy(record["agents"][role]["messages_final"])
                     for role in ("sender", "receiver")}
    return histories


def load_cohort(directory, plans, model):
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    cfg = manifest["config"]
    if cfg["interface"] != "tools" or cfg["memory"] != "persistent" or cfg["schedule"] not in ("staged", "interleaved"):
        raise ValueError("Resume requires persistent direct tools and a deterministic schedule")
    if len(plans) != cfg["trials"]:
        raise ValueError("Saved plan count differs from the source manifest")
    for original in manifest["models"]:
        old, new = asdict(ModelConfig.from_dict(original)), asdict(model)
        old.pop("api_key_env", None)
        new.pop("api_key_env", None)
        if old != new:
            raise ValueError("Continuation must retain the model and all generation settings")
    records, files = {}, {}
    listed_files = {entry["file"] for entry in manifest["records"]}
    if {path.name for path in directory.glob("trial-*.json")} != listed_files:
        raise ValueError("Record files differ from the manifest; reconcile orphan records before continuation")
    for entry in manifest["records"]:
        path = directory / entry["file"]
        if sha256(path) != entry["sha256"]:
            raise ValueError(f"Record checksum mismatch: {path}")
        record = json.loads(path.read_text())
        identity = (record["session_index"], record["condition"], record["round_index"])
        if identity in records or record["errors"] or record["status"] not in ("complete", "incomplete"):
            raise ValueError("Source contains duplicate or unsuccessful completed records")
        i, arm, q = identity
        if not 0 <= i < len(plans) or arm not in cfg["arm"] or not 0 <= q < cfg["rounds_per_session"]:
            raise ValueError("Record is outside the saved experiment plan")
        plan = plans[i]
        for key, expected in (("secret", plan["targets"][q]), ("nonce", plan["nonce"]),
                              ("displayed_answer_sets", plan["displayed_answer_sets"]),
                              ("question_fuzz", plan["question_tags"][q]), ("answer_set", plan["answer_set"])):
            if record[key] != expected:
                raise ValueError(f"Record {key} differs from the saved plan")
        records[identity], files[identity] = record, path
    sessions = []
    for i, plan in enumerate(plans):
        for arm in cfg["arm"]:
            previous = [records[(i, arm, q)] for q in range(cfg["rounds_per_session"]) if (i, arm, q) in records]
            if [r["round_index"] for r in previous] != list(range(len(previous))):
                raise ValueError("Completed questions must form a contiguous session prefix")
            journal = None
            for q in range(len(previous), cfg["rounds_per_session"]):
                path = directory / f"trial-{i * cfg['rounds_per_session'] + q:05d}__{arm}.events.jsonl"
                if path.exists():
                    if q != len(previous) or journal is not None:
                        raise ValueError("Journal exists beyond the next unfinished question")
                    journal = path
                    events, _ = read_journal(path)
                    starts = [event for event in events if event.get("kind") == "trial_start"]
                    if len(starts) != 1 or any(starts[0].get(k) != v for k, v in {
                        "session_index": i, "condition": arm, "round_index": q,
                        "secret": plan["targets"][q], "nonce": plan["nonce"],
                        "question_fuzz": plan["question_tags"][q],
                        "displayed_answer_sets": plan["displayed_answer_sets"],
                    }.items()):
                        raise ValueError("Journal start differs from the saved plan")
                    ResponseReplay(events)
            sessions.append((i, arm, plan, previous, journal))
    return manifest, sessions


def trial_kwargs(cfg, plan, q, counter, histories):
    return dict(max_turns=cfg["max_turns"], schedule=cfg["schedule"], interface="tools",
                max_seconds=cfg["max_seconds"], channel=counter, histories=histories,
                round_index=q, rounds_per_session=cfg["rounds_per_session"], memory="persistent",
                displayed_answer_sets=plan["displayed_answer_sets"], question_fuzz=plan["question_tags"][q],
                counter_mode=cfg["counter_mode"], counter_docs=cfg.get("counter_docs", "legacy"))


def validate_sessions(manifest, sessions, model):
    cfg = manifest["config"]
    stats = {"completed_questions": 0, "completed_sessions": 0, "partial_questions": 0,
             "saved_responses_replayed": 0, "discarded_trailing_bytes": 0}
    for i, arm, plan, previous, journal in sessions:
        counter = Channel(arm, plan["nonce"], GROUP_DIRECTIONS.get(arm, cfg["direction"] or "one-way"),
                          counter_mode=cfg["counter_mode"])
        histories = restore_records(counter, previous)
        stats["completed_questions"] += len(previous)
        stats["completed_sessions"] += len(previous) == cfg["rounds_per_session"]
        if journal:
            events, discarded = read_journal(journal)
            replay = ResponseReplay(events)
            try:
                run_trial(arm, plan["targets"][len(previous)], plan["nonce"], plan["answer_set"], model, model,
                          **trial_kwargs(cfg, plan, len(previous), counter, histories),
                          generate_fn=replay, emit=replay.check_event)
            except ReplayBoundary:
                pass
            replay.assert_consumed()
            stats["partial_questions"] += 1
            stats["saved_responses_replayed"] += replay.replayed
            stats["discarded_trailing_bytes"] += discarded
    return stats


def run_cohort(source, out, models_file, model_name, workers, request_limit):
    from .participants import generate

    plans = json.loads((out.parent / "plans.json").read_text())
    model = load_models(str(models_file), [model_name])[0]
    original, sessions = load_cohort(source, plans, model)
    manifest = json.loads((out / "manifest.json").read_text())
    cfg = original["config"]
    lock = threading.Lock()
    control = RunControl(out.parent / "stop-request.json", out / "request-admission.sqlite", request_limit)

    def work(session):
        i, arm, plan, previous, source_journal = session
        if control.cancelled() or len(previous) == cfg["rounds_per_session"]:
            return
        counter = Channel(arm, plan["nonce"], GROUP_DIRECTIONS.get(arm, cfg["direction"] or "one-way"),
                          counter_mode=cfg["counter_mode"])
        histories = restore_records(counter, previous)
        for q in range(len(previous), cfg["rounds_per_session"]):
            if control.cancelled():
                return
            sample = i * cfg["rounds_per_session"] + q
            stem = f"trial-{sample:05d}__{arm}"
            journal = Journal(out / f"{stem}.events.jsonl")
            journal({"kind": "trial_start", "sample_index": sample, "secret": plan["targets"][q],
                     "nonce": plan["nonce"], "answer_set": plan["answer_set"], "condition": arm,
                     "session_index": i, "round_index": q, "displayed_answer_sets": plan["displayed_answer_sets"],
                     "memory": "persistent", "feedback": "none", "question_fuzz": plan["question_tags"][q]})
            events, discarded = read_journal(source_journal) if source_journal and q == len(previous) else ([], 0)
            replay = ResponseReplay(events, generate)
            provenance = {"source_campaign": str(source.parent), "source_run": str(source),
                          "source_journal": str(source_journal) if events else None,
                          "source_journal_sha256": sha256(source_journal) if events else None,
                          "restored_questions": len(previous), "discarded_trailing_bytes": discarded,
                          "strategy": "restore_completed_records_then_replay_saved_responses",
                          "pending_request_without_response_may_be_reissued": bool(events and replay.requests and replay.requests[-1][1] is None)}
            journal({"kind": "continuation", **provenance})

            def emit(event):
                replay.check_event(event)
                journal(event)

            record = run_trial(arm, plan["targets"][q], plan["nonce"], plan["answer_set"], model, model,
                               **trial_kwargs(cfg, plan, q, counter, histories),
                               generate_fn=replay, emit=emit, control=control)
            replay.assert_consumed()
            record.update(run_id=manifest["run_id"], sample_index=sample, session_index=i,
                          counter_instance_id=f"{manifest['run_id']}:{arm}:session-{i}", timestamp=now(),
                          continuation={**provenance, "saved_responses_replayed": replay.replayed})
            path = out / f"{stem}.json"
            write_json(path, record)
            histories = {role: agent["messages_final"] for role, agent in record["agents"].items()}
            with lock:
                manifest["records"].append({"file": path.name, "sha256": sha256(path)})
                write_json(out / "manifest.json", manifest)
            print(f"Session {i + 1} question {q + 1} {arm}: {record['status']} correct={record['correct']}", flush=True)
            if record["errors"]:
                control.fail(RuntimeError("Continuation question returned an error"), model=model_name,
                             phase="continuation_question", session_index=i, condition=arm, question_index=q)
                return

    def guarded_work(session):
        try:
            return work(session)
        except BaseException as exc:
            control.fail(exc, model=model_name, phase="continuation_worker", session_index=session[0], condition=session[1])
            raise

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(guarded_work, session) for session in sessions]
            for future in as_completed(futures):
                future.result()
        manifest["status"] = "stopped" if control.cancelled() else "complete"
    except BaseException:
        manifest["status"] = "interrupted"
        raise
    finally:
        manifest["request_admission"] = control.gate.snapshot()
        write_json(out / "manifest.json", manifest)
    return 0 if manifest["status"] == "complete" else 1


def ensure_stopped(campaign):
    for pid in [campaign.get("supervisor_pid"), *(job.get("pid") for job in campaign["jobs"])]:
        if not pid:
            continue
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        state = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True).stdout.strip()
        if state and not state.startswith("Z"):
            raise ValueError(f"Source process {pid} is still alive; stop source processes before continuation")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, required=True, help="Existing campaign directory, left unchanged")
    ap.add_argument("--out", type=Path, help="New campaign directory")
    ap.add_argument("--models", type=Path, help="Same generation settings; API key environment name may change")
    ap.add_argument("--workers-per-model", type=int, default=50)
    ap.add_argument("--request-limit-per-model", type=int, default=50)
    ap.add_argument("--check-only", action="store_true", help="Validate saved histories/counters offline; no API calls or writes")
    ap.add_argument("--cohort", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if min(args.workers_per_model, args.request_limit_per_model) < 1:
        ap.error("Worker and request limits must be positive")
    source = args.source.resolve()
    if args.cohort:
        return run_cohort(source, args.out.resolve(), args.models, args.cohort,
                          args.workers_per_model, args.request_limit_per_model)
    campaign = json.loads((source / "campaign.json").read_text())
    models_file = (args.models or source / "models.yaml").resolve()
    models = {model.name: model for model in load_models(str(models_file), campaign["model_names"])}
    plans = json.loads((source / "plans.json").read_text())
    if sha256(source / "plans.json") != campaign["plans_sha256"]:
        ap.error("Source plans checksum mismatch")
    if not args.check_only:
        ensure_stopped(campaign)
        if args.out is None:
            ap.error("--out is required to launch a continuation")
        for model in models.values():
            if model.transport != "stub" and not model.api_key():
                ap.error(f"Missing API key for {model.name}")
    validated = {}
    loaded = {}
    for job in campaign["jobs"]:
        loaded[job["run_id"]] = load_cohort(source / job["run_id"], plans, models[job["model"]])
        validated[job["model"]] = validate_sessions(*loaded[job["run_id"]], models[job["model"]])
    print(json.dumps({"offline_validation": validated}), flush=True)
    if args.check_only:
        return 0
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[2]
    snapshot = out / "source"
    hashes = {}
    for package in ("ai_collusion", "experiments"):
        for path in sorted((root / package).rglob("*")):
            if path.is_file() and path.suffix in {".py", ".txt", ".json", ".html", ".yaml"} and "__pycache__" not in path.parts:
                target = snapshot / path.relative_to(root)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
                hashes[str(path.relative_to(root))] = sha256(target)
    write_json(out / "source-sha256.json", hashes)
    shutil.copyfile(models_file, out / "models.yaml")
    shutil.copyfile(source / "plans.json", out / "plans.json")
    write_json(out / "continuation-validation.json", validated)
    write_json(out / "source-campaign.json", campaign)
    campaign = copy.deepcopy(campaign)
    campaign.pop("finished_utc", None)
    campaign.pop("admission_file", None)
    campaign.update(status="running", started_utc=now(), supervisor_pid=os.getpid(),
                    continued_from=str(source), source_campaign_sha256=sha256(source / "campaign.json"),
                    max_active_sessions=args.workers_per_model * len(campaign["jobs"]),
                    stop_file=str(out / "stop-request.json"), models_sha256=sha256(out / "models.yaml"),
                    request_limit_per_model=args.request_limit_per_model,
                    admission_scope="independent_per_model", continuation_validation=validated)
    for job in campaign["jobs"]:
        original, _ = loaded[job["run_id"]]
        destination = out / job["run_id"]
        destination.mkdir()
        original_journals = destination / "continuation-source-journals"
        original_journals.mkdir()
        shutil.copyfile(source / job["run_id"] / "manifest.json", destination / "source-manifest.json")
        manifest = copy.deepcopy(original)
        manifest.update(status="running", timestamp=now(), models=[asdict(models[job["model"]])] * 2,
                        continuation={"source_run": str(source / job["run_id"]),
                                      "source_manifest_sha256": sha256(destination / "source-manifest.json"),
                                      "copied_records_unchanged": len(original["records"]),
                                      "validated": validated[job["model"]]})
        manifest["config"].update(out=str(out), workers=args.workers_per_model, models=str(out / "models.yaml"),
                                  session_plan=str(out / "plans.json"), stop_file=str(out / "stop-request.json"),
                                  admission_file=str(destination / "request-admission.sqlite"),
                                  request_limit=args.request_limit_per_model)
        completed_names = {entry["file"].removesuffix(".json") for entry in original["records"]}
        for entry in original["records"]:
            shutil.copyfile(source / job["run_id"] / entry["file"], destination / entry["file"])
        journal_hashes = {}
        for journal in (source / job["run_id"]).glob("*.events.jsonl"):
            target = destination / journal.name if journal.name.removesuffix(".events.jsonl") in completed_names else original_journals / journal.name
            shutil.copyfile(journal, target)
            journal_hashes[str(target.relative_to(destination))] = sha256(target)
        manifest["continuation"]["source_journal_sha256"] = journal_hashes
        write_json(destination / "manifest.json", manifest)
        job.update(workers=args.workers_per_model, request_limit=args.request_limit_per_model,
                   status="pending", admission_file=str(destination / "request-admission.sqlite"))
        job.pop("pid", None)
        job.pop("exit_code", None)
        RequestGate(job["admission_file"], args.request_limit_per_model)
    write_json(out / "campaign.json", campaign)
    awake = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())]) if shutil.which("caffeinate") else None
    if awake:
        campaign["caffeinate_pid"] = awake.pid
    processes = []
    for job in campaign["jobs"]:
        command = [sys.executable, "-u", "-m", "experiments.covert_channel.resume", "--cohort", job["model"],
                   "--source", str(source / job["run_id"]), "--out", str(out / job["run_id"]),
                   "--models", str(out / "models.yaml"), "--workers-per-model", str(args.workers_per_model),
                   "--request-limit-per-model", str(args.request_limit_per_model)]
        with (out / f"{job['run_id']}.log").open("w") as log:
            process = subprocess.Popen(command, cwd=snapshot, stdout=log, stderr=subprocess.STDOUT)
        job.update(pid=process.pid, command=command, status="running")
        processes.append(process)
    db = sqlite3.connect(out / "scores.sqlite")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE rounds (model TEXT,file TEXT,condition TEXT,session INTEGER,round INTEGER,target TEXT,guess TEXT,correct INTEGER,errors INTEGER,cost REAL,source TEXT,sha256 TEXT,PRIMARY KEY(model,file))")
    control = RunControl(out / "stop-request.json")
    while True:
        for job, process in zip(campaign["jobs"], processes):
            code = process.poll()
            if code is not None:
                job.update(status="complete" if code == 0 else "failed", exit_code=code)
                if code != 0:
                    control.fail(RuntimeError("Continuation cohort failed"), model=job["model"], exit_code=code)
        finished = all(process.poll() is not None for process in processes)
        if finished:
            campaign.update(status="complete" if all(p.returncode == 0 for p in processes) else "failed", finished_utc=now())
        write_json(out / "campaign.json", campaign)
        progress = update_report(out, campaign, db)
        print(json.dumps({key: value for key, value in progress.items() if key != "groups"}), flush=True)
        if finished:
            if progress["scored_guesses"] != campaign["planned_guesses"] or progress["questions_with_model_errors"]:
                campaign["status"] = "failed"
                write_json(out / "campaign.json", campaign)
                update_report(out, campaign, db)
            break
        time.sleep(30)
    db.close()
    if awake:
        awake.terminate()
    return 0 if campaign["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
