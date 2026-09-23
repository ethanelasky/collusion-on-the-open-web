"""Stage interrupted questions for exact response replay without editing source data.

Only terminal model-error tails with no subsequent response or counter event can
be removed from the active view. Original files remain in the source campaign and
are also archived in the stage. Selection never depends on guesses or correctness.
"""
from __future__ import annotations

import argparse
import copy
from dataclasses import asdict
import json
from pathlib import Path
import shutil

from ai_collusion.runner import load_models
from .channel import Channel, GROUP_DIRECTIONS
from .resume import (ReplayBoundary, ResponseReplay, ensure_stopped, load_cohort,
                     now, read_journal, restore_records, sha256, trial_kwargs, validate_sessions)
from .run import run_trial, write_json


def inventory(directory):
    files = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Recovery source must not contain symlinks: {path}")
        if path.is_file():
            files[str(path.relative_to(directory))] = sha256(path)
    return files


def recoverable_prefix(path, record):
    events, torn_bytes = read_journal(path)
    if torn_bytes:
        raise ValueError("A completed error record must have a complete journal")
    failures = [i for i, event in enumerate(events) if event.get("kind") == "turn"
                and (event.get("error") or event.get("source") == "model-error")]
    if not failures:
        raise ValueError("Error record has no terminal model-error journal turn")
    first_error = failures[0]
    requests = [i for i, event in enumerate(events[:first_error]) if event.get("kind") == "request"]
    if not requests:
        raise ValueError("Terminal error has no preceding request")
    boundary = requests[-1]
    pending = events[boundary]
    failure = events[first_error]
    if (pending["agent_id"], pending["turn"]) != (failure["agent_id"], failure["turn"]):
        raise ValueError("Terminal error does not belong to the unanswered request")
    for event in events[boundary + 1:]:
        kind = event.get("kind")
        if kind not in ("turn", "request", "model_retry"):
            raise ValueError("Cannot remove a tail containing a response, counter event, or unknown event")
        if kind == "turn" and (event.get("source") != "model-error" or not event.get("error")
                               or event.get("response") is not None or event.get("effects")
                               or event.get("action") is not None):
            raise ValueError("Cannot remove a successful or state-changing turn")
    prefix = events[:boundary + 1]
    replay = ResponseReplay(prefix)
    if not replay.requests or replay.requests[-1][1] is not None:
        raise ValueError("Recovery prefix must end with one unanswered request")
    recorded_responses = {(role, turn["turn"]): turn["response"]
                          for role, agent in record["agents"].items() for turn in agent["turns"]
                          if turn.get("response") is not None}
    journal_responses = {(event["agent_id"], event["turn"]): event["response"]
                         for event in prefix if event.get("kind") == "response"}
    if recorded_responses != journal_responses:
        raise ValueError("Recovery would omit or change a saved response")
    if record.get("guess") is not None and not any(
        event.get("kind") == "turn" and event.get("agent_id") == "receiver"
        and isinstance(event.get("action"), dict) and event["action"].get("action") == "guess"
        and event["action"].get("answer") == record["guess"] for event in prefix
    ):
        raise ValueError("Saved guess is absent from the replay prefix")
    # Keep the exact original bytes, including whitespace and newline choices.
    raw = b"".join(path.read_bytes().splitlines(keepends=True)[:boundary + 1])
    return prefix, raw, {"source_journal_sha256": sha256(path),
                         "unanswered_request": {key: pending[key] for key in ("agent_id", "turn")},
                         "prefix_events": boundary + 1, "archived_tail_events": len(events) - boundary - 1,
                         "saved_responses_preserved": len(recorded_responses),
                         "saved_guess_preserved": record.get("guess") is not None}


def inspect_campaign(source):
    campaign = json.loads((source / "campaign.json").read_text())
    ensure_stopped(campaign)
    if campaign["status"] not in ("stopped", "failed", "interrupted"):
        raise ValueError("Recovery requires a stopped, failed, or interrupted campaign")
    if sha256(source / "plans.json") != campaign["plans_sha256"]:
        raise ValueError("Source plan checksum mismatch")
    plans = json.loads((source / "plans.json").read_text())
    models = {model.name: model for model in load_models(str(source / "models.yaml"), campaign["model_names"])}
    cohorts = []
    for job in campaign["jobs"]:
        directory = source / job["run_id"]
        manifest = json.loads((directory / "manifest.json").read_text())
        cfg = manifest["config"]
        if cfg["interface"] != "tools" or cfg["memory"] != "persistent" or cfg["schedule"] not in ("staged", "interleaved"):
            raise ValueError("Recovery requires persistent tools and a deterministic schedule")
        if len(plans) != cfg["trials"]:
            raise ValueError("Plan count differs from source configuration")
        model = models[job["model"]]
        if any(asdict(model) != asdict(type(model).from_dict(original)) for original in manifest["models"]):
            raise ValueError("Source model settings differ from its manifest")
        listed = {entry["file"] for entry in manifest["records"]}
        if len(listed) != len(manifest["records"]) or listed != {p.name for p in directory.glob("trial-*.json")}:
            raise ValueError("Source has duplicate or orphan record files")
        records, failures = {}, []
        revised = copy.deepcopy(manifest)
        revised["records"] = []
        for entry in manifest["records"]:
            path = directory / entry["file"]
            if path.parent.resolve() != directory.resolve() or sha256(path) != entry["sha256"]:
                raise ValueError("Unsafe record path or record checksum mismatch")
            record = json.loads(path.read_text())
            i, arm, q = identity = record["session_index"], record["condition"], record["round_index"]
            if identity in records or not 0 <= i < len(plans) or arm not in cfg["arm"] or not 0 <= q < cfg["rounds_per_session"]:
                raise ValueError("Duplicate or out-of-plan source record")
            plan = plans[i]
            for key, value in (("secret", plan["targets"][q]), ("nonce", plan["nonce"]),
                               ("displayed_answer_sets", plan["displayed_answer_sets"]),
                               ("question_fuzz", plan["question_tags"][q]), ("answer_set", plan["answer_set"])):
                if record[key] != value:
                    raise ValueError(f"Source record {key} differs from plan")
            records[identity] = record
            if record.get("errors"):
                if record["status"] != "error":
                    raise ValueError("Error record has an inconsistent status")
                journal = path.with_suffix(".events.jsonl")
                prefix, raw, details = recoverable_prefix(journal, record)
                failures.append({"record_file": entry["file"], "record_sha256": entry["sha256"],
                                 "journal_file": journal.name, "identity": identity,
                                 "prefix": prefix, "prefix_bytes": raw, **details})
            elif record["status"] in ("complete", "incomplete"):
                revised["records"].append(entry)
            else:
                raise ValueError("Unsuccessful source record lacks a recoverable model error")
        for i, plan in enumerate(plans):
            for arm in cfg["arm"]:
                previous = [records[i, arm, q] for q in range(cfg["rounds_per_session"]) if (i, arm, q) in records]
                if [r["round_index"] for r in previous] != list(range(len(previous))):
                    raise ValueError("Saved questions do not form a contiguous session prefix")
                bad = [q for q, r in enumerate(previous) if r["errors"]]
                if bad and bad != [len(previous) - 1]:
                    raise ValueError("Saved questions follow a failed question; cannot preserve their contexts")
        for failure in failures:
            i, arm, q = failure["identity"]
            plan = plans[i]
            counter = Channel(arm, plan["nonce"], GROUP_DIRECTIONS.get(arm, cfg["direction"] or "one-way"),
                              counter_mode=cfg["counter_mode"])
            histories = restore_records(counter, [records[i, arm, earlier] for earlier in range(q)])
            replay = ResponseReplay(failure["prefix"])
            try:
                run_trial(arm, plan["targets"][q], plan["nonce"], plan["answer_set"], model, model,
                          **trial_kwargs(cfg, plan, q, counter, histories), generate_fn=replay, emit=replay.check_event)
            except ReplayBoundary:
                pass
            else:
                raise ValueError("Failed question did not reach the missing-response boundary")
            replay.assert_consumed()
        cohorts.append({"run_id": job["run_id"], "model": model, "manifest": revised, "failures": failures})
    return campaign, plans, cohorts


def stage_campaign(source, out=None):
    source = Path(source).resolve()
    hashes = inventory(source)
    campaign, plans, cohorts = inspect_campaign(source)
    summary = {"at_utc": now(), "status": "offline_validated", "source_campaign": str(source),
               "source_files_sha256": hashes, "paid_api_calls": 0, "selection_rule": "terminal model errors only; never correctness",
               "cohorts": [{"run_id": cohort["run_id"], "good_records_preserved": len(cohort["manifest"]["records"]),
                            "error_questions_recovered": len(cohort["failures"]),
                            "saved_responses_preserved": sum(f["saved_responses_preserved"] for f in cohort["failures"]),
                            "saved_guesses_preserved": sum(f["saved_guess_preserved"] for f in cohort["failures"]),
                            "questions": [{k: v for k, v in failure.items() if k not in ("prefix", "prefix_bytes")}
                                          for failure in cohort["failures"]]} for cohort in cohorts]}
    if out is None:
        return summary
    out = Path(out).resolve()
    if out == source or source in out.parents or out in source.parents:
        raise ValueError("Recovery stage must be separate from the original campaign")
    shutil.copytree(source, out)
    if inventory(out) != hashes or inventory(source) != hashes:
        raise ValueError("Source changed during staging or snapshot copy differs")
    archive = out / "recovery-originals"
    archive.mkdir()
    shutil.copyfile(out / "campaign.json", archive / "campaign.json")
    for cohort in cohorts:
        directory = out / cohort["run_id"]
        original = archive / cohort["run_id"]
        original.mkdir()
        shutil.copyfile(directory / "manifest.json", original / "manifest.json")
        for failure in cohort["failures"]:
            (directory / failure["record_file"]).rename(original / failure["record_file"])
            journal = directory / failure["journal_file"]
            journal.rename(original / failure["journal_file"])
            journal.write_bytes(failure["prefix_bytes"])
        write_json(directory / "manifest.json", cohort["manifest"])
        loaded = load_cohort(directory, plans, cohort["model"])
        validation = validate_sessions(*loaded, cohort["model"])
        next(item for item in summary["cohorts"] if item["run_id"] == cohort["run_id"])["resume_validation"] = validation
    if inventory(source) != hashes:
        raise ValueError("Original campaign changed during recovery")
    summary.update(status="ready_for_exact_resume", stage_campaign=str(out),
                   source_unchanged=True, original_archive="recovery-originals",
                   executable_source_note="Launch resume from the patched worktree; its executable source is snapshotted there.")
    write_json(out / "recovery-receipt.json", summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args(argv)
    if not args.check_only and args.out is None:
        parser.error("--out is required unless --check-only is set")
    receipt = stage_campaign(args.source, None if args.check_only else args.out)
    print(json.dumps({k: v for k, v in receipt.items() if k != "source_files_sha256"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
