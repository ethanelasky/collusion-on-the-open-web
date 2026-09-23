"""Run selected model cohorts with a fixed total concurrency and a live local report."""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from ai_collusion.runner import load_models
from .analysis import usage_summary
from .plans import make_plan, validate_plan
from .control import RequestGate, RunControl
from .counter_docs import COUNTER_DOC_VERSIONS
from .run import COUNTER_FOUR_50, PRESETS, action_limit, write_json

MODELS = [("glm-5.3-high", "glm53"), ("deepseek-v4-flash-high", "deepseek4flash")]
GROUPS = COUNTER_FOUR_50["arm"]


def make_jobs(model_names, workers, groups):
    """Split a shared worker budget without changing the historical run IDs."""
    legacy_ids = dict(MODELS)
    jobs = []
    used_ids = set()
    for index, model in enumerate(model_names):
        run_id = legacy_ids.get(model, re.sub(r"[^A-Za-z0-9_-]+", "-", model).strip("-") or "model")
        if run_id in used_ids:
            run_id += "-" + hashlib.sha256(model.encode()).hexdigest()[:12]
        used_ids.add(run_id)
        jobs.append({"model": model, "run_id": run_id, "groups": list(groups),
                     "workers": workers // len(model_names) + int(index < workers % len(model_names)),
                     "status": "pending"})
    return jobs


def update_report(out, campaign, db):
    jobs = campaign.get("jobs") or [{"model": model, "run_id": run_id} for model, run_id in MODELS]
    conditions = list(dict.fromkeys(group for job in jobs for group in job.get("groups", campaign.get("groups", GROUPS))))
    known = {tuple(row) for row in db.execute("SELECT model,file FROM rounds")}
    started = set()
    for job in jobs:
        directory = out / job["run_id"]
        manifest_path = directory / "manifest.json"
        if not manifest_path.exists():
            continue
        manifest = json.loads(manifest_path.read_text())
        for file in directory.glob("*.events.jsonl"):
            sample, group = file.name.removesuffix(".events.jsonl").split("__", 1)
            started.add((job["model"], group, int(sample.removeprefix("trial-")) // campaign["rounds_per_session"]))
        for entry in manifest["records"]:
            if (job["model"], entry["file"]) in known:
                continue
            raw = (directory / entry["file"]).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == entry["sha256"], "Record checksum mismatch"
            r = json.loads(raw)
            assert r["condition"] in job.get("groups", campaign.get("groups", GROUPS)) and r["interface"] == "tools"
            assert "question_fuzz" in r and r["memory"] == "persistent"
            db.execute("INSERT INTO rounds VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
                job["model"], entry["file"], r["condition"], r["session_index"], r["round_index"] + 1,
                r["secret"], r["guess"], int(r["correct"]), int(bool(r["errors"])),
                usage_summary([r])["reported_cost_usd"] or 0,
                f"{job['run_id']}/{entry['file']}", entry["sha256"]))
    db.commit()
    rows = [dict(row) for row in db.execute("SELECT * FROM rounds ORDER BY model,condition,session,round")]
    session_rows = [dict(row) for row in db.execute(
        "SELECT model,condition,session,count(*) AS n,sum(correct) AS correct,sum(errors) AS errors,sum(cost) AS cost "
        "FROM rounds GROUP BY model,condition,session")]
    processed = sum(r["n"] == campaign["rounds_per_session"] for r in session_rows)
    completed = sum(r["n"] == campaign["rounds_per_session"] and not r["errors"] for r in session_rows)
    groups = []
    for job in jobs:
        model = job["model"]
        for condition in job.get("groups", campaign.get("groups", GROUPS)):
            selected = [r for r in session_rows if r["model"] == model and r["condition"] == condition]
            n, correct = sum(r["n"] for r in selected), sum(r["correct"] for r in selected)
            groups.append({"model": model, "condition": condition, "n": n, "correct": correct,
                           "accuracy": correct / n if n else None,
                           "completed_sessions": sum(r["n"] == campaign["rounds_per_session"] and not r["errors"] for r in selected),
                           "errors": sum(r["errors"] for r in selected), "cost": sum(r["cost"] for r in selected)})
    progress = {"updated_utc": datetime.now(timezone.utc).isoformat(), "status": campaign["status"],
                "planned_guesses": campaign["planned_guesses"], "scored_guesses": len(rows),
                "planned_sessions": campaign["planned_sessions"], "completed_sessions": completed,
                "processed_sessions": processed, "started_sessions": len(started),
                "active_sessions": len(started) - processed if campaign["status"] == "running" else 0,
                "interrupted_or_error_sessions": len(started) - completed if campaign["status"] != "running" else None,
                "questions_with_model_errors": sum(r["errors"] for r in groups),
                "recorded_cost_usd": sum(r["cost"] for r in groups), "groups": groups}
    progress["analysis_status"] = ("model_errors" if progress["questions_with_model_errors"] else
                                   "complete" if completed == campaign["planned_sessions"] else "partial")
    if campaign.get("admission_file"):
        progress["request_admission"] = RequestGate(campaign["admission_file"], campaign["max_active_sessions"]).snapshot()
    elif any(job.get("admission_file") for job in jobs):
        progress["request_admission_by_model"] = {
            job["model"]: RequestGate(job["admission_file"], job.get("request_limit", job["workers"])).snapshot()
            for job in jobs if job.get("admission_file")}
    write_json(out / "progress.json", progress)
    for name, data in [("rounds.csv", rows), ("sessions.csv", session_rows)]:
        if data:
            with (out / name).open("w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(data[0]))
                writer.writeheader()
                writer.writerows(data)
    table = "".join(f"<tr><td>{html.escape(g['model'])}</td><td>{html.escape(g['condition'])}</td><td>{g['correct']}/{g['n']}</td>"
                    f"<td>{g['completed_sessions']}/{campaign['sessions_per_group']}</td><td>{g['errors']}</td>"
                    f"<td>${g['cost']:.3f}</td></tr>" for g in groups)
    compact = [{k: r[k] for k in ("model", "condition", "session", "round", "target", "guess", "correct", "source")} for r in rows]
    serialized = json.dumps(compact).replace("<", "\\u003c")
    n_colors = len(campaign.get("answer_set", COUNTER_FOUR_50["answer_set"].split(",")))
    action_budget = campaign.get("max_turns_per_session", 2 * COUNTER_FOUR_50["max_turns"] * campaign["rounds_per_session"])
    warning = (f"<p><strong>Model API errors affected {progress['questions_with_model_errors']} questions. "
               "These include cancelled calls after a campaign stop. Aggregate scores are incomplete and must not "
               "be treated as a clean communication result.</strong></p>" if progress["questions_with_model_errors"] else "")
    gate = progress.get("request_admission")
    admission = (f"<p>Model API requests: {gate['active_requests']} active, limit {gate['request_limit']}. "
                 f"Request-limit backoff events: {gate['backoff_events']}. "
                 f"Shared retry wait remaining: {max(0, gate['cooldown_until_unix_s'] - time.time()):.0f} seconds.</p>"
                 if gate else "")
    for model, gate in progress.get("request_admission_by_model", {}).items():
        admission += (f"<p>{html.escape(model)} API requests: {gate['active_requests']} active, "
                      f"limit {gate['request_limit']}. Backoff events: {gate['backoff_events']}. "
                      f"Retry wait remaining: {max(0, gate['cooldown_until_unix_s'] - time.time()):.0f} seconds.</p>")
    document = f"""<!doctype html><meta charset="utf-8"><title>Counter overnight batch</title>
<style>body{{font:16px system-ui;margin:35px auto;max-width:1200px;padding:15px}}table{{border-collapse:collapse;width:100%}}td,th{{text-align:left;padding:9px;border-bottom:1px solid #ddd}}input,select{{padding:7px;margin:7px}}p{{line-height:1.5}}</style>
<h1>Counter overnight batch</h1><p>{html.escape(progress['updated_utc'])} · {campaign['status']} · {len(rows):,}/{campaign['planned_guesses']:,} questions processed · {completed}/{campaign['planned_sessions']} sessions complete without model API errors · {progress['active_sessions']} active sessions</p>{warning}{admission}
<p>{len(jobs)} model cohorts, {len(conditions)} counter groups, {n_colors} colors, private mixed lists, independent random question tags. No search. Each session has {campaign['rounds_per_session']} questions and at most {action_budget} actions across both agents, including done and guess. Actions alternate within each question; the target stays fixed until that question ends. Histories persist for the session. Chance is {100 / n_colors:g}%. Errors and missing guesses remain in the denominator. Running scores are partial. Recorded costs exclude responses in unfinished questions and failed requests without a saved response.</p>
<p>Counter mode: {html.escape(campaign.get('counter_mode', 'fixed-key'))}. Each session has its own counter dictionary, shared only by its two agents.</p>
<table><tr><th>Model</th><th>Group</th><th>Correct/processed</th><th>Sessions without API errors</th><th>Error questions</th><th>Recorded cost</th></tr>{table}</table>
<p><a href="campaign.json">Run settings and processes</a> · <a href="plans.json">Paired session inputs</a> · <a href="rounds.csv">Round scores</a> · <a href="sessions.csv">Session scores</a> · <a href="progress.json">Full progress</a></p>
<h2>Transcript records</h2><p>Select one model, group, and session to inspect up to {campaign['rounds_per_session']} questions. Each link opens the full private transcripts and tool events.</p>
<select id="model">{''.join('<option>'+html.escape(job['model'])+'</option>' for job in jobs)}</select><select id="group">{''.join('<option>'+html.escape(g)+'</option>' for g in conditions)}</select><label>Session (1–{campaign['sessions_per_group']})<input id="session" type="number" min="1" max="{campaign['sessions_per_group']}" value="1"></label><table id="records"></table>
<script id="data" type="application/json">{serialized}</script><script>
const rows=JSON.parse(document.querySelector('#data').textContent),m=document.querySelector('#model'),g=document.querySelector('#group'),s=document.querySelector('#session'),t=document.querySelector('#records');
function render(){{t.replaceChildren();for(const r of rows.filter(r=>r.model===m.value&&r.condition===g.value&&r.session===Number(s.value)-1)){{const tr=document.createElement('tr');for(const v of [r.round,r.target,r.guess,r.correct?'correct':'wrong']){{const td=document.createElement('td');td.textContent=v;tr.append(td)}}const td=document.createElement('td'),a=document.createElement('a');a.href=r.source;a.textContent='Full transcript';td.append(a);tr.append(td);t.append(tr)}}}}m.onchange=g.onchange=s.oninput=render;render();</script>"""
    (out / "report.html").write_text(document)
    return progress


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--models", type=Path, default=Path("experiments/covert_channel/models.reasoning.yaml"))
    ap.add_argument("--model-names", nargs="+", default=[model for model, _ in MODELS],
                    help="Model aliases from --models; one cohort per alias, all using the same paired session inputs")
    ap.add_argument("--sessions", type=int, default=50)
    ap.add_argument("--preset", choices=["counter-five-questions", "counter-four-50"], default="counter-five-questions")
    ap.add_argument("--questions-per-session", "--rounds", dest="rounds", type=int)
    ap.add_argument("--turns-per-session", type=int, help="Total action cap across both agents, including done/guess")
    ap.add_argument("--max-turns", type=int, help="Alternative per-agent action cap per question")
    ap.add_argument("--answer-set", help="Comma-separated distinct candidate colors")
    ap.add_argument("--counter-mode", choices=["fixed-key", "wiki"])
    ap.add_argument("--counter-docs", choices=COUNTER_DOC_VERSIONS, default="legacy",
                    help="Versioned API guide for counter participants; original prompts use legacy")
    ap.add_argument("--workers", type=int, default=50)
    ap.add_argument("--seed", type=int, default=20260910)
    ap.add_argument("--session-plan", type=Path, help="Saved paired session-plan list, shared across all model cohorts")
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args(argv)
    defaults = PRESETS[args.preset]
    args.counter_mode = args.counter_mode or defaults["counter_mode"]
    if args.counter_docs != "legacy" and args.counter_mode != "wiki":
        ap.error("--counter-docs reference-v1 requires --counter-mode wiki")
    args.rounds = defaults["rounds_per_session"] if args.rounds is None else args.rounds
    args.answer_set = defaults["answer_set"] if args.answer_set is None else args.answer_set
    if args.preset == "counter-five-questions" and args.turns_per_session is None and args.max_turns is None:
        args.turns_per_session = 50
    per_agent = args.max_turns
    if per_agent is None and args.turns_per_session is None:
        per_agent = defaults["max_turns"]
    try:
        args.max_turns = action_limit(args.rounds, per_agent, args.turns_per_session)
    except ValueError as exc:
        ap.error(str(exc))
    args.turns_per_session = 2 * args.max_turns * args.rounds
    answers = [s.strip() for s in args.answer_set.split(",")]
    if len(answers) < 2 or len(set(answers)) != len(answers) or any(not s for s in answers):
        ap.error("--answer-set needs at least two distinct nonempty symbols")
    if args.demo and args.max_turns <= len(answers):
        ap.error("Unary beep demo needs more actions per agent than colors; use fewer colors or an explicit larger action cap")
    if len(set(args.model_names)) != len(args.model_names) or any(not name.strip() for name in args.model_names):
        ap.error("--model-names needs distinct nonempty model aliases")
    if min(args.sessions, args.rounds) < 1 or args.workers < len(args.model_names):
        ap.error("Need positive sessions/questions and at least one worker per model cohort")
    plans = None
    if args.session_plan:
        try:
            plans = json.loads(args.session_plan.read_text())
            if not isinstance(plans, list) or len(plans) != args.sessions:
                raise ValueError("Plan count must match --sessions")
            for plan in plans:
                validate_plan(plan, answers, args.rounds, question_fuzz=True)
                if plan["displayed_answer_sets"]["sender"] == plan["displayed_answer_sets"]["receiver"]:
                    raise ValueError("Paired plans must use independent sender and receiver answer orders")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            ap.error(f"Invalid session plan: {exc}")
    if not args.demo:
        for model in load_models(str(args.models), args.model_names):
            if not model.api_key():
                ap.error(f"Missing key for {model.name}")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[2]
    source = out / "source"
    source.mkdir()
    hashes = {}
    package_files = sorted(p for p in (root / "ai_collusion").rglob("*")
                           if p.is_file() and p.suffix in {".py", ".txt", ".json", ".html", ".yaml"})
    for file in package_files + [root / "experiments/__init__.py"] + sorted((root / "experiments/covert_channel").glob("*.py")):
        relative = file.relative_to(root)
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(file, target)
        hashes[str(relative)] = hashlib.sha256(target.read_bytes()).hexdigest()
    write_json(out / "source-sha256.json", hashes)
    shutil.copyfile(args.models, out / "models.yaml")
    write_json(out / "plans.json", plans if plans is not None else
               [make_plan(args.seed, i, answers, args.rounds) for i in range(args.sessions)])
    jobs = make_jobs(args.model_names, args.workers, defaults["arm"])
    planned_sessions = sum(len(job["groups"]) for job in jobs) * args.sessions
    campaign = {"status": "running", "started_utc": datetime.now(timezone.utc).isoformat(),
                "supervisor_pid": os.getpid(), "sessions_per_group": args.sessions, "rounds_per_session": args.rounds,
                "planned_sessions": planned_sessions, "planned_guesses": planned_sessions * args.rounds,
                "model_names": list(args.model_names), "groups": list(defaults["arm"]),
                "preset": args.preset, "answer_set": answers, "chance_accuracy": 1 / len(answers),
                "counter_mode": args.counter_mode, "counter_scope": "session",
                "counter_docs": args.counter_docs,
                "counter_backend": "local_dictionary", "counter_consistency": "immediate",
                "stop_file": str(out / "stop-request.json"), "failure_policy": "stop_after_exhausted_model_error",
                "admission_file": str(out / "request-admission.sqlite"),
                "questions_per_session": args.rounds, "turn_unit": "one agent action",
                "max_turns_per_agent_per_question": args.max_turns,
                "max_turns_per_session": args.turns_per_session,
                "max_model_turns": planned_sessions * args.turns_per_session,
                "max_active_sessions": args.workers, "scripted": args.demo, "seed": args.seed,
                "models_sha256": hashlib.sha256((out / "models.yaml").read_bytes()).hexdigest(),
                "session_plan_source": str(args.session_plan.resolve()) if args.session_plan else None,
                "plans_sha256": hashlib.sha256((out / "plans.json").read_bytes()).hexdigest(), "jobs": jobs}
    write_json(out / "campaign.json", campaign)
    RequestGate(campaign["admission_file"], args.workers)
    awake = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())]) if shutil.which("caffeinate") else None
    if awake:
        campaign["caffeinate_pid"] = awake.pid
    processes = []
    control = RunControl(campaign["stop_file"])
    for job in jobs:
        model, run_id = job["model"], job["run_id"]
        command = [sys.executable, "-u", "-m", "experiments.covert_channel.run", "--preset", args.preset,
                   "--sessions", str(args.sessions), "--rounds-per-session", str(args.rounds),
                   "--turns-per-session", str(args.turns_per_session), "--answer-set", ",".join(answers),
                   "--counter-mode", args.counter_mode, "--counter-docs", args.counter_docs,
                   "--stop-file", campaign["stop_file"],
                   "--admission-file", campaign["admission_file"], "--request-limit", str(args.workers),
                   "--workers", str(job["workers"]), "--session-plan", str(out / "plans.json"),
                   "--skip-analysis", "--models", str(out / "models.yaml"), "--sender", model, "--receiver", model,
                   "--seed", str(args.seed), "--out", str(out), "--run-id", run_id]
        if args.demo:
            command.append("--demo")
        with (out / f"{run_id}.log").open("w") as log:
            process = subprocess.Popen(command, cwd=source, stdout=log, stderr=subprocess.STDOUT)
        job.update(pid=process.pid, command=command, status="running")
        processes.append(process)
    write_json(out / "campaign.json", campaign)
    db = sqlite3.connect(out / "scores.sqlite")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE rounds (model TEXT,file TEXT,condition TEXT,session INTEGER,round INTEGER,target TEXT,guess TEXT,correct INTEGER,errors INTEGER,cost REAL,source TEXT,sha256 TEXT,PRIMARY KEY(model,file))")
    while True:
        for job, process in zip(campaign["jobs"], processes):
            code = process.poll()
            if code is not None:
                job.update(status="complete" if code == 0 else "failed", exit_code=code)
                if code != 0:
                    control.fail(RuntimeError("Cohort process exited unsuccessfully"),
                                 model=job["model"], phase="cohort_exit", exit_code=code)
        finished = all(p.poll() is not None for p in processes)
        if finished:
            campaign.update(status="stopped" if Path(campaign["stop_file"]).exists() else
                            "complete" if all(p.returncode == 0 for p in processes) else "failed",
                            finished_utc=datetime.now(timezone.utc).isoformat())
        write_json(out / "campaign.json", campaign)
        progress = update_report(out, campaign, db)
        print(json.dumps({k: v for k, v in progress.items() if k != "groups"}), flush=True)
        if finished:
            if campaign["status"] == "complete" and (progress["scored_guesses"] != campaign["planned_guesses"] or progress["questions_with_model_errors"]):
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
