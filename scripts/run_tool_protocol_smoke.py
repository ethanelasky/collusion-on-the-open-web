"""Two full seven-round smoke episodes per model, gated on the first episode."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import argparse
import json
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_working_extension import save
from ai_collusion.run_health import health

CASES = {"gpt-5.6": 30, "qwen3.8-27b": 43, "glm-5.3": 6, "kimi-k3": 6}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="v1")
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(".env", override=False)
    control = ROOT / "data" / f"tool-protocol-smoke-20260910-{args.version}"
    snapshot = ROOT / "data/run-snapshots" / f"tool-protocol-fix-20260910-{args.version}"
    control.mkdir(parents=True, exist_ok=True)
    import fcntl
    with (control / "launch.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not snapshot.exists():
            snapshot.mkdir(parents=True)
            for name in ("ai_collusion", "experiments", "wikitasks"):
                shutil.copytree(ROOT / name, snapshot / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            (snapshot / "data").mkdir()
            (snapshot / "data/collusion-wiki").symlink_to(ROOT / "data/collusion-wiki", target_is_directory=True)
        save(control / "status.json", {"status": "running", "target": 8, "version": args.version})

        def family(model):
            run_id = f"tool-protocol-smoke-20260910-{args.version}-{model}"
            path = ROOT / "runs" / run_id
            path.mkdir(parents=True, exist_ok=True)
            command = [sys.executable, "-m", "ai_collusion.wiki_cli", "play",
                "--page", "dse/DataUSAStateSequenceCollab2027", "--rev", "4",
                "--spec", "wikitasks/sector61_state_seven.yaml", "--models", "experiments/native-tool-models.yaml",
                "--arms", "experiments/working-search-only.yaml", "--arm", "working-search-only-write-on",
                "--env-model", "env-gpt-5.6", "--only", model, "--seed", str(CASES[model]),
                "--workers", "1", "--max-turns", "100", "--out", str(ROOT / "runs"), "--run-id", run_id]
            for samples in (1, 2):
                manifest = path / "manifest.json"
                if samples == 1 and manifest.exists() and json.loads(manifest.read_text())["n_samples"] > 1:
                    continue
                with (path / "runner.log").open("a") as log:
                    subprocess.run([*command, "-n", str(samples)], cwd=snapshot, stdout=log, stderr=subprocess.STDOUT, check=True)
                rows = [json.loads(p.read_text()) for p in path.glob("*env-neutral*.json")]
                passed = len(rows) == samples and all(not r.get("error") and not health(r)["provider_response_error"]
                    and not health(r)["provider_blocked"] and r["episode"]["end_reason"] == "all_rounds_resolved"
                    for r in rows)
                summary = {"model": model, "saved": len(rows), "passed": passed,
                    "episodes": [{"seed": r["seed"], "turns": r["episode"]["n_turns"], "end": r["episode"]["end_reason"],
                        "correct": sum(bool(x.get("correct")) for x in r["episode"]["rounds"]),
                        "recovery_attempts": sum(max(0, len(t.get("generation_attempts", []))-1) for t in r["episode"]["turns"]),
                        "error": r.get("error", {}).get("message") if r.get("error") else None} for r in rows]}
                save(control / f"{model}.json", summary)
                print(json.dumps(summary), flush=True)
                if not passed:
                    return summary
            return summary

        with ThreadPoolExecutor(max_workers=4) as pool:
            summaries = list(pool.map(family, CASES))
        passed = all(s["passed"] and s["saved"] == 2 for s in summaries)
        save(control / "status.json", {"status": "passed" if passed else "failed", "families": summaries})


if __name__ == "__main__":
    main()
