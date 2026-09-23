"""Retry only budget-failed seeds, preserve attempts, and select 45 valid sources."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_kimi_native_repair import RUN_PATH, SNAPSHOT, CONTROL as ORIGINAL
from scripts.run_working_extension import save, upload, COLLECTION
from ai_collusion.run_health import health

CONTROL = ROOT / "data/kimi-budget-recovery-20260910"
SELECTED = CONTROL / "selected"


def records(path):
    return {json.loads(p.read_text())["seed"]: p for p in path.glob("*env-neutral*.json")}


def budget_failure(row):
    return "in_flight_budget_exhausted" in json.dumps(row.get("error"))


def valid(row):
    return not row.get("error") and not any(health(row)[key] for key in (
        "provider_response_error", "provider_blocked", "unparsed_native_tool_tokens",
        "model_reports_tools_disabled"))


def retry(seed):
    source = json.loads(records(RUN_PATH)[seed].read_text())
    assert budget_failure(source)
    for attempt in range(1, 4):
        run_id = f"working-write-on-native-budget-retry-20260910-kimi-k3-seed{seed}-a{attempt}"
        path = ROOT / "runs" / run_id
        path.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, "-m", "ai_collusion.wiki_cli", "play",
            "--page", "dse/DataUSAStateSequenceCollab2027", "--rev", "4",
            "--spec", "wikitasks/sector61_state_seven.yaml", "--models", str(ROOT / "experiments/kimi-native-tools.yaml"),
            "--arms", "experiments/hinted-write-instructions.yaml", "--arm", "working-write-on",
            "--env-model", "env-gpt-5.6", "--only", "kimi-k3", "--seed", str(seed),
            "--workers", "1", "--max-turns", "100", "-n", "1", "--out", str(ROOT / "runs"), "--run-id", run_id]
        with (path / "runner.log").open("a") as log:
            subprocess.run(command, cwd=SNAPSHOT, stdout=log, stderr=subprocess.STDOUT, check=True)
        result_path = records(path)[seed]
        result = json.loads(result_path.read_text())
        for key in ("context", "model_config", "env_model_config", "resolved_config", "arm"):
            assert result[key] == source[key], f"Retry changed {key} for seed {seed}"
        if valid(result):
            save(CONTROL / f"seed{seed}.json", {"status": "verified", "seed": seed,
                "original_source": str(records(RUN_PATH)[seed]), "source_path": str(result_path), "attempt": attempt})
            print(f"seed {seed}: retry verified ({result['episode']['end_reason']})", flush=True)
            return result_path
        if not budget_failure(result):
            raise RuntimeError(f"seed {seed}: new non-budget failure; inspect {result_path}")
        if attempt < 3:
            time.sleep(30)
    raise RuntimeError(f"seed {seed}: budget error persists after three attempts")


def main():
    from dotenv import load_dotenv
    load_dotenv(".env", override=False)
    CONTROL.mkdir(parents=True, exist_ok=True)
    import fcntl
    with (CONTROL / "launch.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        save(CONTROL / "status.json", {"status": "retrying", "workers": 10})
        selected_retries = {}
        with ThreadPoolExecutor(max_workers=10) as pool:
            pending = {}
            while True:
                current = records(RUN_PATH)
                for seed, path in current.items():
                    if budget_failure(json.loads(path.read_text())) and seed not in pending:
                        pending[seed] = pool.submit(retry, seed)
                for seed, future in pending.items():
                    if future.done():
                        selected_retries[seed] = future.result()
                state = json.loads((ORIGINAL / "status.json").read_text())
                save(CONTROL / "status.json", {"status": "retrying", "original_saved": len(current),
                    "retry_seeds": sorted(pending), "verified_retry_seeds": sorted(selected_retries), "workers": 10})
                if len(current) == 45 and len(selected_retries) == len(pending) and state["status"] == "finished":
                    break
                if state["status"] != "finished" and subprocess.run(
                        ["tmux", "has-session", "-t", "collusion-kimi-repair-20260910"], capture_output=True).returncode:
                    raise RuntimeError("Original Kimi supervisor stopped before upload completed")
                time.sleep(15)
        chosen = {**current, **selected_retries}
        assert set(chosen) == set(range(5, 50))
        assert all(valid(json.loads(p.read_text())) for p in chosen.values())
        SELECTED.mkdir(exist_ok=True)
        entries = []
        for seed, path in sorted(chosen.items()):
            link = SELECTED / f"seed{seed:02d}__env-neutral.json"
            if link.is_symlink():
                assert link.resolve() == path.resolve()
            else:
                link.symlink_to(path)
            entries.append({"seed": seed, "source_path": str(path),
                "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "budget_retry": seed in selected_retries})
            if seed in selected_retries:
                upload("kimi-k3", path.parent, control=CONTROL / f"seed{seed}", expected_count=1)
        from ai_collusion.docent_cli import make_client
        client = make_client()
        ids = json.loads((ORIGINAL / "kimi-k3-upload.json").read_text())["agent_run_ids"]
        for run_id in ids:
            run = client.get_agent_run(COLLECTION, run_id)
            if run.metadata.get("seed") in selected_retries:
                client.update_agent_run_metadata(COLLECTION, run_id, {
                    "analysis_status": "excluded_provider_budget_failure",
                    "replacement_source": str(selected_retries[run.metadata["seed"]])})
                client.tag_transcript(COLLECTION, run_id, "Excluded: provider budget failure")
        save(CONTROL / "selection.json", {"selected": entries, "excluded_budget_attempts": len(selected_retries)})
        save(CONTROL / "status.json", {"status": "finished", "selected": 45,
            "retry_seeds": sorted(selected_retries), "uploaded_retries": len(selected_retries)})
        client.update_collection_metadata(COLLECTION, {"kimi_repair": {
            "status": "validated", "selected_runs": 45, "budget_failures_retried": len(selected_retries),
            "interface": "native tools"}})
        print("45 Kimi sources validated and uploaded; ready for classifier", flush=True)


if __name__ == "__main__":
    main()
