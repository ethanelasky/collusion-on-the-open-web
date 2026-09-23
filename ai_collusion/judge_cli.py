"""Offline, append-only collaboration judging of saved rollout episodes."""
from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .client import ModelConfig
from .judge import (SCHEMA_VERSION, build_prompt, judge_record, load_rubric,
                    project_record, source_facts)
from .runner import load_repo_env


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _hash(value):
    return hashlib.sha256(value if isinstance(value, bytes) else _json(value).encode()).hexdigest()


def _write(path: Path, value, *, replace=False):
    """Publish a complete file, refusing to overwrite immutable artifacts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=".writing-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(tmp, path)
        else:
            os.link(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _provenance():
    root = Path(__file__).resolve().parents[1]
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root,
                                         stderr=subprocess.DEVNULL, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None
    return {"git_commit": commit, "files": {
        name: _hash(Path(__file__).with_name(name).read_bytes())
        for name in ("judge_cli.py", "judge.py", "client.py", "run_health.py", "native_tools.py", "rate_limit.py")
    }}


def summarize(envelopes, labels):
    """Explicit denominators; censored and interface-limited episodes stay separate."""
    groups = {}
    for item in envelopes:
        facts = item.get("source_facts") or {}
        key = (facts.get("model"), facts.get("arm_id") or facts.get("condition"))
        if key not in groups:
            groups[key] = {"model": key[0], "arm_id": key[1], "source_count": 0,
                           "judged_success": 0, "judge_failures": 0,
                           "source_transport_invalid": 0, "source_transport_unknown": 0,
                           "interface_limited": 0,
                           "max_turn_censored": 0, "primary_eligible": 0, "strata": {},
                           "labels": {label: {"present": 0, "absent": 0, "uncertain": 0,
                               "primary_present": 0, "primary_absent": 0,
                               "primary_denominator": 0, "primary_rate": None,
                               "stages": {}, "channels": {}} for label in labels}}
        group = groups[key]
        group["source_count"] += 1
        success = bool(item.get("judgment")) and not item.get("error")
        group["judged_success" if success else "judge_failures"] += 1
        group["source_transport_invalid"] += int(bool(facts.get("source_transport_error")))
        group["source_transport_unknown"] += int(facts.get("source_transport_status") not in {"valid", "invalid"})
        group["interface_limited"] += int(bool(facts.get("interface_limited")))
        group["max_turn_censored"] += int(bool(facts.get("censored")))
        eligible = (success and facts.get("source_transport_status") == "valid"
                    and facts.get("source_transport_error") is False
                    and facts.get("all_rounds_resolved") is True
                    and not facts.get("interface_limited") and not facts.get("censored"))
        group["primary_eligible"] += int(eligible)
        stratum = ("source_transport_invalid" if facts.get("source_transport_error") else
                   "interface_limited" if facts.get("interface_limited") else
                   "max_turn_censored" if facts.get("censored") else
                   "source_transport_unknown" if facts.get("source_transport_status") != "valid" else
                   "all_rounds_resolved" if facts.get("all_rounds_resolved") else "unresolved")
        sub = group["strata"].setdefault(stratum, {"source_count": 0, "judged_success": 0,
                  "labels": {label: {"present": 0, "absent": 0, "uncertain": 0} for label in labels}})
        sub["source_count"] += 1
        sub["judged_success"] += int(success)
        if not success:
            continue
        evidence = item["judgment"]["evidence"]
        for label, assessment in item["judgment"]["labels"].items():
            counts = group["labels"][label]
            counts[assessment] += 1
            sub["labels"][label][assessment] += 1
            if eligible and assessment in {"present", "absent"}:
                counts["primary_" + assessment] += 1
                counts["primary_denominator"] += 1
            events = [event for event in evidence if label in event["labels"]]
            # Shared events contribute once per label/stage/channel in this episode.
            for stage in {event["stage"] for event in events}:
                counts["stages"][stage] = counts["stages"].get(stage, 0) + 1
            for channel in {quote["field"] for event in events for quote in event["quotes"]}:
                counts["channels"][channel] = counts["channels"].get(channel, 0) + 1
    for group in groups.values():
        for counts in group["labels"].values():
            if counts["primary_denominator"]:
                counts["primary_rate"] = counts["primary_present"] / counts["primary_denominator"]
    return {"schema_version": SCHEMA_VERSION, "groups": sorted(groups.values(),
            key=lambda g: (str(g["model"]), str(g["arm_id"])))}


def _save_summary(out, envelopes, labels):
    summary = summarize(envelopes, labels)
    _write(out / "summary.json", summary, replace=True)
    fields = ["model", "arm_id", "source_count", "judged_success", "judge_failures",
              "source_transport_invalid", "source_transport_unknown", "interface_limited", "max_turn_censored", "primary_eligible",
              "label", "present", "absent", "uncertain", "primary_present", "primary_absent",
              "primary_denominator", "primary_rate", "stages", "channels", "strata"]
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for group in summary["groups"]:
        base = {k: v for k, v in group.items() if k != "labels"}
        for label, counts in group["labels"].items():
            writer.writerow({**base, "label": label, **counts,
                             "stages": _json(counts["stages"]), "channels": _json(counts["channels"]),
                             "strata": _json(group["strata"])})
    _write(out / "summary.csv", stream.getvalue(), replace=True)
    return summary


def _attempts(out):
    return [json.loads(path.read_text()) for path in sorted((out / "attempts").glob("*.json"))]


def _latest(attempts):
    latest = {}
    for item in attempts:
        key = item["source_path"]
        if key not in latest or (item["created_at"], item["attempt"]) > (
                latest[key]["created_at"], latest[key]["attempt"]):
            latest[key] = item
    return list(latest.values())


def _run(args):
    runs = [Path(p).resolve() for p in args.run]
    out = Path(args.out).resolve()
    if any(out == run or out.is_relative_to(run) or run.is_relative_to(out) for run in runs):
        raise ValueError("judge output must be separate from source run directories")
    paths = set()
    for run in runs:
        if not run.is_dir():
            raise ValueError(f"missing run directory: {run}")
        paths.update(p.resolve() for p in run.glob("*.json") if p.name != "manifest.json")
    paths = sorted(paths)
    if args.limit is not None:
        paths = paths[:args.limit]
    if not paths:
        raise ValueError("no source records found")
    # Preview does not load .env files or need provider credentials.
    models_data = yaml.safe_load(Path(args.models).read_text())
    models = [ModelConfig.from_dict(m, models_data.get("defaults") or {})
              for m in models_data["models"] if m.get("name") == args.judge]
    if len(models) != 1:
        raise ValueError(f"expected exactly one configured judge named {args.judge!r}")
    model = models[0]
    from .rate_limit import configure

    configure(model, getattr(args, "requests_per_minute", None))
    rubric = load_rubric(args.rubric)
    provenance = _provenance()
    settings = {"schema_version": SCHEMA_VERSION, "rubric_sha256": rubric["sha256"],
                "judge_config": asdict(model), "code_sha256": provenance["files"]}
    out.mkdir(parents=True, exist_ok=True)
    with (out / ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("another judge process is using this output directory") from exc
        manifest_path = out / "manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
            if manifest.get("settings") != settings:
                raise ValueError("output directory rubric/config/schema/code differs; choose a new --out")
        else:
            _write(manifest_path, {"settings": settings, "rubric": rubric,
                                   "requests_per_minute": getattr(args, "requests_per_minute", None),
                                   "code_provenance": provenance})
        attempts = _attempts(out)
        pending = []
        previews_failed = False
        for path in paths:
            read_error = None
            try:
                raw = path.read_bytes()
            except OSError as exc:
                raw, read_error = None, exc
            source_sha = _hash(raw) if raw is not None else None
            identity = _hash({**settings, "source_sha256": source_sha, "source_path": str(path)})
            previous = [a for a in attempts if a["identity"] == identity]
            if not args.preview and any(a.get("judgment") and not a.get("error") for a in previous):
                continue
            attempt = max((a["attempt"] for a in previous), default=0) + 1
            base = {"identity": identity, "attempt": attempt, "source_path": str(path),
                    "source_file": path.name, "source_sha256": source_sha,
                    "rubric_version": rubric["version"],
                    "created_at": datetime.now(timezone.utc).isoformat()}
            try:
                if read_error:
                    raise read_error
                record = json.loads(raw)
                facts = source_facts(record)
                base["source_facts"] = facts
                projection = project_record(record)
                system, messages = build_prompt(projection, rubric)
            except Exception as exc:
                item = {**settings, **base, "input": None, "input_sha256": None,
                        "prompt": None, "response": None, "judgment": None,
                        "started_at": base["created_at"], "finished_at": datetime.now(timezone.utc).isoformat(),
                        "duration_s": 0,
                        "error": {"type": type(exc).__name__, "message": str(exc), "phase": "source"}}
                if args.preview:
                    preview = out / "previews" / f"{identity}.error-{attempt:04d}.json"
                    # Repeated malformed previews remain durable without overwriting.
                    while preview.exists():
                        attempt += 1
                        preview = out / "previews" / f"{identity}.error-{attempt:04d}.json"
                    _write(preview, item)
                    previews_failed = True
                else:
                    _write(out / "attempts" / f"{identity}.attempt-{attempt:04d}.json", item)
                    attempts.append(item)
                continue
            if args.preview:
                target = out / "previews" / f"{identity}.json"
                if not target.exists():
                    _write(target, {**settings, **base, "input": projection,
                                    "system": system, "messages": messages})
            else:
                pending.append((record, base))
        if args.preview:
            print(f"Previewed {len(paths)} source records in {out / 'previews'}; no provider calls.")
            return int(previews_failed)
        if args.env_file:
            load_repo_env(Path(args.env_file))
        else:
            load_repo_env()

        def evaluate(record, base):
            try:
                item = judge_record(record, model, rubric, base["source_sha256"])
            except Exception as exc:
                item = {**settings, "input": None, "input_sha256": None,
                        "response": None, "judgment": None,
                        "error": {"type": type(exc).__name__, "message": str(exc), "stage": "judge"}}
            item = {**item, **base}
            _write(out / "attempts" / f"{base['identity']}.attempt-{base['attempt']:04d}.json", item)
            return item

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(evaluate, record, base) for record, base in pending]
            try:
                for future in as_completed(futures):
                    attempts.append(future.result())
                    _save_summary(out, _latest(attempts), rubric["labels"])
            except KeyboardInterrupt:
                for future in futures:
                    future.cancel()
                raise
        latest = _latest(_attempts(out))
        summary = _save_summary(out, latest, rubric["labels"])
        upload_failed = False
        if args.collection_id:
            from .judge_docent import upload_judgments
            uploads = upload_judgments(latest, args.collection_id, out / "docent-ledger.jsonl")
            upload_failed = any(item.get("status") == "failed" for item in uploads)
            for item in uploads:
                if item.get("status") == "failed":
                    print(f"Docent update failed: {item.get('error')}", file=sys.stderr)
        print(json.dumps(summary, indent=2))
        invalid = any(item.get("error") or (item.get("source_facts") or {}).get("source_transport_error")
                      for item in latest)
        return int(bool(invalid or upload_failed))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", nargs="+", required=True)
    parser.add_argument("--models", required=True)
    parser.add_argument("--judge", required=True)
    parser.add_argument("--rubric", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--requests-per-minute", type=float, help="pace judge request starts, including retries")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--env-file")
    parser.add_argument("--collection-id")
    args = parser.parse_args(argv)
    if args.requests_per_minute is not None and args.requests_per_minute <= 0:
        parser.error("--requests-per-minute must be positive")
    if args.workers < 1 or (args.limit is not None and args.limit < 1):
        parser.error("workers and limit must be positive")
    try:
        return _run(args)
    except (ValueError, OSError, KeyError, TypeError, yaml.YAMLError) as exc:
        print(f"ai-collusion-judge: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
