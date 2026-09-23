"""Validate the native-tool repair, then complete the replacement Kimi batch."""
from pathlib import Path
import json
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_working_extension import save, upload

CONTROL = ROOT / "data/kimi-native-repair-20260910"
SNAPSHOT = ROOT / "data/run-snapshots/kimi-native-repair-20260910"
RUN_ID = "working-write-on-native-tools-repair-20260910-kimi-k3"
RUN_PATH = ROOT / "runs" / RUN_ID


def main():
    from dotenv import load_dotenv
    load_dotenv(".env", override=False)
    CONTROL.mkdir(parents=True, exist_ok=True)
    import fcntl
    with (CONTROL / "launch.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not SNAPSHOT.exists():
            shutil.copytree(ROOT / "data/run-snapshots/working-write-on-50-20260910", SNAPSHOT, symlinks=True)
            for name in ["client.py", "native_tools.py", "rate_limit.py"]:
                shutil.copy2(ROOT / "ai_collusion" / name, SNAPSHOT / "ai_collusion" / name)
        RUN_PATH.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, "-m", "ai_collusion.wiki_cli", "play",
            "--page", "dse/DataUSAStateSequenceCollab2027", "--rev", "4",
            "--spec", "wikitasks/sector61_state_seven.yaml", "--models", str(ROOT / "experiments/kimi-native-tools.yaml"),
            "--arms", "experiments/hinted-write-instructions.yaml", "--arm", "working-write-on",
            "--env-model", "env-gpt-5.6", "--only", "kimi-k3", "--seed", "5",
            "--workers", "10", "--max-turns", "100", "--out", str(ROOT / "runs"), "--run-id", RUN_ID]
        for samples in [3, 45]:
            manifest = RUN_PATH / "manifest.json"
            if samples == 3 and manifest.exists() and json.loads(manifest.read_text())["n_samples"] > 3:
                continue
            save(CONTROL / "status.json", {"status": "pilot" if samples == 3 else "running", "samples": samples})
            with (RUN_PATH / "runner.log").open("a") as log:
                subprocess.run([*command, "-n", str(samples)], cwd=SNAPSHOT,
                               stdout=log, stderr=subprocess.STDOUT, check=True)
            records = [json.loads(p.read_text()) for p in RUN_PATH.glob("*env-neutral*.json")]
            if samples == 3:
                assert len(records) == 3
                assert all(not r.get("error") for r in records), "Pilot provider errors"
                assert all(r["episode"]["end_reason"] == "all_rounds_resolved" for r in records), "Pilot did not resolve all rounds"
                assert all(any(t.get("call") for t in r["episode"]["turns"]) for r in records)
                original = next((ROOT / "runs/hinted-write-on-100-20260909-kimi-k3").glob("*env-neutral*.json"))
                assert all(r["context"] == json.loads(original.read_text())["context"] for r in records)
                save(CONTROL / "pilot.json", {"verified": True, "episodes": 3,
                    "all_rounds_resolved": 3, "original_context_preserved": True})
        upload("kimi-k3", RUN_PATH, control=CONTROL)
        save(CONTROL / "status.json", {"status": "finished", "saved": 45, "uploaded": 45})


if __name__ == "__main__":
    main()
