"""Publish saved color rollouts to public Docent collections, without model calls.

One collection per campaign. The optional watcher adds settled rollouts as they
finish. A local lock and upload receipts prevent overlapping or repeated uploads.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from ai_collusion.docent_cli import make_client, resolve_collection_id
from ai_collusion.run_storage import write_json
from .game import load_rollout

TERMINAL = {"complete", "complete_with_errors", "complete_pending_responses", "failed", "interrupted"}


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text())


def campaign_paths(comparison, campaigns):
    paths = list(campaigns)
    if comparison:
        comparison = Path(comparison).resolve()
        spec = read(comparison)
        # Superseded attempts are deliberately not part of the comparison.
        paths += [comparison.parent / model["directory"] / "runs" for model in spec["models"]]
    return list(dict.fromkeys(str(Path(path).resolve()) for path in paths))


def verify_public(client, collection_id, agent_run_id=None):
    """Check ACL and read with a new, unauthenticated HTTP session."""
    acl = client.get_collection_collaborators(collection_id)
    if not any(row.get("subject_type") == "public" and row.get("permission_level") == "read" for row in acl):
        raise ValueError("Docent collection does not have public read permission")
    base = client._api_url
    with requests.Session() as anonymous:
        anonymous.trust_env = False
        response = anonymous.get(f"{base}/{collection_id}/collection_details", timeout=45)
        response.raise_for_status()
        if response.json().get("id") != collection_id:
            raise ValueError("Anonymous collection read returned another collection")
        if agent_run_id:
            response = anonymous.get(f"{base}/{collection_id}/agent_run",
                                     params={"agent_run_id": agent_run_id}, timeout=45)
            response.raise_for_status()
            record = response.json()
            if not record or record.get("id") != agent_run_id or not record.get("transcripts"):
                raise ValueError("Anonymous transcript read failed")
    return {"verified_utc": now(), "permission": "read", "anonymous": True,
            "sample_agent_run_id": agent_run_id}


def settled_rollouts(manifest):
    """Include failed choices and failed jobs; defer unfinished provider replies."""
    for outcome in manifest["outcomes"]:
        if outcome["status"] not in TERMINAL:
            continue
        source = outcome.get("artifact_paths", {}).get("json")
        if not source:
            continue
        path = Path(source)
        if not path.exists():
            continue
        rollout = load_rollout(path)
        if rollout["status"] not in TERMINAL or rollout["summary"].get("pending_responses", 0):
            continue
        yield outcome, path, rollout


def _check_secrets(run):
    payload = run.model_dump_json()
    secrets = [value for key, value in os.environ.items()
               if ("API_KEY" in key or key.endswith("_TOKEN")) and len(value) >= 20]
    if any(value in payload for value in secrets):
        raise ValueError("Export contains a credential; upload stopped")


def _reconcile(client, entry, save):
    pending = entry.get("pending")
    if not pending:
        return True
    if pending.get("job_ids"):
        statuses = client.get_agent_run_job_statuses(entry["collection_id"], pending["job_ids"])
        if any(job["status"] in {"canceled", "failed"} for job in statuses):
            raise RuntimeError("Docent ingestion failed; retained receipt requires review")
        if len(statuses) != len(pending["job_ids"]) or any(job["status"] != "completed" for job in statuses):
            return False
    remote = set(client.list_agent_run_ids(entry["collection_id"]))
    if not set(pending["agent_run_ids"]).issubset(remote):
        # A crash after sending but before saving the job receipt is ambiguous.
        # Keep the IDs for reconciliation; never resubmit and create duplicates.
        if not pending.get("job_ids"):
            raise RuntimeError("Uncertain upload receipt; inspect saved IDs before any retry")
        return False
    entry["uploaded"].update(pending["sources"])
    entry["last_ingestion_utc"] = now()
    entry.pop("pending")
    save()
    return True


def sync_campaign(client, path, entry, save, batch_size):
    from .docent import rollout_to_agent_run

    manifest = read(Path(path) / "campaign.json")
    entry.update(campaign_id=manifest["campaign_id"], model=manifest["model"]["name"],
                 campaign_status=manifest["status"], planned=len(manifest["outcomes"]),
                 completed=manifest["summary"]["completed_rollouts"], checked_utc=now())
    if "collection_id" not in entry:
        # The same model/directory name can be reused in a later experiment.
        # Keep independent campaigns in separate collections.
        name = (f"Color game | {manifest['model']['name']} | {Path(path).parent.name} | "
                f"{manifest['campaign_id']}")
        collection_id = resolve_collection_id(client, name)
        entry.update(collection_id=collection_id, name=name, uploaded={},
                     url=f"https://docent.transluce.org/dashboard/{collection_id}")
        save()
        selected = manifest.get("selected_settings", ["guessing_only", "async_counter", "sync_counter"])
        rounds = manifest.get("base_config", {}).get("rounds", 5)
        client.update_collection(collection_id, description=(
            f"Color coordination game with {rounds} rounds per rollout. "
            f"Settings: {', '.join(selected)}. "
            "Each rollout contains separate Alice and Bob transcripts. "
            "Outcomes and shared counter events are research metadata, not player feedback. "
            "This collection receives saved rollouts as they finish, including errors. "
            "Check model, prompt_version, source and campaign metadata before comparing batches."))
    if not entry.get("public_verification"):
        client.share_collection_with_public(entry["collection_id"], permission="read")
        entry["public_verification"] = verify_public(client, entry["collection_id"])
        save()
    if not _reconcile(client, entry, save):
        return

    batch, sources = [], {}

    def flush():
        if not batch:
            return
        entry["pending"] = {"agent_run_ids": [run.id for run in batch], "sources": dict(sources),
                            "prepared_utc": now(), "job_ids": []}
        save()
        receipt = client.add_agent_runs(entry["collection_id"], batch, wait=False)
        entry["pending"]["job_ids"] = receipt["job_ids"]
        save()
        # Leave long ingestion jobs to the next watcher pass.
        deadline = time.monotonic() + 45
        while not _reconcile(client, entry, save):
            if time.monotonic() >= deadline:
                return
            time.sleep(2)
        batch.clear()
        sources.clear()

    for outcome, source, rollout in settled_rollouts(manifest):
        key = rollout["rollout_id"]
        if key in entry["uploaded"]:
            continue
        metadata = {"campaign_id": manifest["campaign_id"], "job_id": outcome["job_id"],
                    "job_status": outcome["status"], "model": manifest["model"],
                    "source_file": str(source), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
        run = rollout_to_agent_run(rollout, campaign_metadata=metadata)
        _check_secrets(run)
        batch.append(run)
        sources[key] = {"agent_run_id": run.id, "source_sha256": metadata["source_sha256"],
                        "job_id": outcome["job_id"], "status": rollout["status"]}
        if len(batch) >= batch_size:
            flush()
            if entry.get("pending"):
                return
    flush()
    if entry.get("uploaded") and not entry.get("transcript_verification"):
        sample = next(iter(entry["uploaded"].values()))["agent_run_id"]
        entry["transcript_verification"] = verify_public(client, entry["collection_id"], sample)
    entry["finished"] = (manifest["status"] in TERMINAL and
                         len(entry["uploaded"]) == len(manifest["outcomes"]) and not entry.get("pending"))
    entry.pop("error", None)
    save()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparison", type=Path)
    parser.add_argument("--campaign", action="append", type=Path, default=[])
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=60)
    parser.add_argument("--max-hours", type=float, default=24)
    parser.add_argument("--batch-size", type=int, default=10)
    args = parser.parse_args(argv)
    if args.interval < 1 or args.max_hours <= 0 or args.batch_size < 1:
        parser.error("interval, max-hours, and batch-size must be positive")
    paths = campaign_paths(args.comparison, args.campaign)
    if not paths:
        parser.error("provide --comparison or --campaign")
    if args.env_file:
        from dotenv import load_dotenv
        load_dotenv(args.env_file, override=False)
    args.state = args.state.resolve()
    args.state.parent.mkdir(parents=True, exist_ok=True)
    with args.state.with_suffix(".lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = read(args.state) if args.state.exists() else {
            "schema": "color-game-docent-sync/v1", "created_utc": now(), "campaigns": {}}
        if state["campaigns"] and set(state["campaigns"]) != set(paths):
            raise ValueError("Saved sync state belongs to different campaigns")
        state.update(pid=os.getpid(), status="running", started_utc=now())
        for path in paths:
            state["campaigns"].setdefault(path, {})

        def save():
            state["updated_utc"] = now()
            write_json(args.state, state)

        save()
        client = make_client()
        deadline = time.monotonic() + args.max_hours * 3600
        while True:
            for path, entry in state["campaigns"].items():
                try:
                    sync_campaign(client, path, entry, save, args.batch_size)
                except Exception as exc:
                    # SDK exceptions can contain sensitive request details.
                    entry["error"] = {"type": type(exc).__name__, "time": now()}
                    save()
                print(json.dumps({"model": entry.get("model"), "completed": entry.get("completed"),
                                  "uploaded": len(entry.get("uploaded", {})), "url": entry.get("url"),
                                  "error": entry.get("error")}), flush=True)
            if all(entry.get("finished") and not entry.get("error") for entry in state["campaigns"].values()):
                state.update(status="complete", finished_utc=now())
                save()
                return 0
            if not args.watch or time.monotonic() >= deadline:
                state["status"] = "waiting" if not args.watch else "expired"
                save()
                return 1 if any(entry.get("error") for entry in state["campaigns"].values()) else 0
            time.sleep(min(args.interval, max(0, deadline - time.monotonic())))


if __name__ == "__main__":
    raise SystemExit(main())
