"""Prepare or launch 45 additional historical working-condition episodes per model.

Run from the current checkout. Rollouts use a verified frozen implementation;
the current exporter provides readable Docent summaries and prefill labels.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PREFIX = "working-write-on-50-extension-20260910"
MODELS = ["gpt-5.6", "qwen3.8-27b", "glm-5.3", "kimi-k3"]
COLLECTION = "f0c4850a-a84b-41c6-a2e5-5656beac8a21"
SNAPSHOT = ROOT / "data/run-snapshots/working-write-on-50-20260910"
CONTROL = ROOT / "data/working-write-on-50-20260910"


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".pending")
    pending.write_text(json.dumps(value, indent=2) + "\n")
    pending.replace(path)


def prepare():
    if not SNAPSHOT.exists():
        SNAPSHOT.mkdir(parents=True)
        archive = subprocess.check_output(["git", "archive", "9217583"], cwd=ROOT)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tf:
            tf.extractall(SNAPSHOT, filter="data")
        (SNAPSHOT / "data").mkdir(exist_ok=True)
        (SNAPSHOT / "data/collusion-wiki").symlink_to(ROOT / "data/collusion-wiki", target_is_directory=True)
    check = ROOT / "scripts/check_working_extension.py"
    result = subprocess.check_output([sys.executable, "-c",
        "import runpy,sys; script=sys.argv.pop(1); runpy.run_path(script,run_name='__main__')",
        str(check), str(ROOT)], cwd=SNAPSHOT, text=True)
    report = json.loads(result)
    save(CONTROL / "preflight.json", report)
    print(result, flush=True)


def upload(model, run_path, *, control=CONTROL, expected_count=45):
    from ai_collusion.docent_cli import ensure_public, load_run_dir, make_client, record_to_agent_run
    from ai_collusion.docent_prefill import annotate_prefills
    from ai_collusion.docent_presentation import annotate_presentation

    ledger_path = control / f"{model}-upload.json"
    client = make_client()
    ensure_public(client, COLLECTION)
    if ledger_path.exists():
        ledger = json.loads(ledger_path.read_text())
        ids = ledger["agent_run_ids"]
        if any(client.get_agent_run(COLLECTION, i) is None for i in ids):
            raise RuntimeError(f"Uncertain prior upload: inspect {ledger_path} before retrying")
    else:
        manifest, records = load_run_dir(run_path)
        assert len(records) == expected_count
        runs = [record_to_agent_run(r, manifest) for r in records]
        ids = [r.id for r in runs]
        save(ledger_path, {"status": "sending", "collection_id": COLLECTION, "agent_run_ids": ids})
        client.add_agent_runs(COLLECTION, runs)
    annotate_prefills(client, COLLECTION, agent_run_ids=ids)
    annotate_presentation(client, COLLECTION, ids)
    save(ledger_path, {"status": "verified", "collection_id": COLLECTION, "agent_run_ids": ids})


def run_model(model):
    run_id = f"{PREFIX}-{model}"
    run_path = ROOT / "runs" / run_id
    run_path.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "ai_collusion.wiki_cli", "play",
        "--page", "dse/DataUSAStateSequenceCollab2027", "--rev", "4",
        "--spec", "wikitasks/sector61_state_seven.yaml", "--models", "experiments/hinted-models.yaml",
        "--arms", "experiments/hinted-write-instructions.yaml", "--arm", "working-write-on",
        "--env-model", "env-gpt-5.6", "--only", model, "-n", "45", "--seed", "5",
        "--workers", "10", "--max-turns", "100", "--out", str(ROOT / "runs"), "--run-id", run_id]
    with (run_path / "runner.log").open("a") as log:
        result = subprocess.run(command, cwd=SNAPSHOT, stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f"{model}: rollout process failed; see {run_path / 'runner.log'}")
    from scripts.validate_rollouts import validate
    validation = validate(run_path, model, samples=45, rounds=7, max_turns=100,
                          arm_ids=("working-write-on",), wiki_write_instructions=True)
    save(CONTROL / f"{model}-validation.json", validation)
    # Keep provider-error records available for inspection, as in the original batch.
    upload(model, run_path)
    save(CONTROL / f"{model}-status.json", {"rollouts": "finished", "uploaded": 45,
                                          "validation_passed": validation["valid"]})
    print(f"{model}: 45 new episodes saved and uploaded; validation={validation['valid']}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launch", action="store_true", help="start paid API rollouts after verification")
    parser.add_argument("--env-file", type=Path, help="load named credentials from this local dotenv file")
    args = parser.parse_args()
    from ai_collusion.runner import load_repo_env
    load_repo_env()
    if args.env_file:
        from dotenv import load_dotenv
        if not args.env_file.is_file():
            raise SystemExit("Credential file does not exist")
        load_dotenv(args.env_file, override=False)
    prepare()
    if not args.launch:
        print("Prepared only; no API model calls made. Pass --launch to start.")
        return
    missing = [k for k in ["OPENAI_API_KEY", "OPENROUTER_API_KEY"] if not os.getenv(k)]
    if missing:
        raise SystemExit("Missing credentials: " + ", ".join(missing))
    # Prevent overlapping supervisors from launching duplicate samples.
    import fcntl
    with (CONTROL / "launch.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        save(CONTROL / "status.json", {"status": "running", "pid": os.getpid(), "additional_episodes": 180})
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(run_model, MODELS))
        save(CONTROL / "status.json", {"status": "finished", "additional_episodes": 180})


if __name__ == "__main__":
    main()
