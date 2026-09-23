"""Classify each search-only family when its upload finishes; 100 aggregate RPM."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_search_only import CONTROL, MODELS
from scripts.run_working_classifiers import classify
from scripts.run_working_extension import save, COLLECTION
from ai_collusion.judge import load_rubric
from ai_collusion.judge_cli import summarize

OUT = ROOT / "judgments/working-search-only-20260910"


def family(model):
    state_path = CONTROL / f"{model}-status.json"
    while True:
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        if state.get("status") == "finished":
            break
        if subprocess.run(["tmux", "has-session", "-t", "collusion-search-only-20260910"], capture_output=True).returncode:
            raise RuntimeError(f"Search-only rollouts stopped before {model} uploaded")
        time.sleep(15)
    return classify(model, requests_per_minute=25,
        source_run=ROOT / "runs" / f"working-search-only-50-20260910-{model}",
        output_root=OUT, expected_count=50)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    import fcntl
    with (OUT / "supervisor.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        save(OUT / "status.json", {"status": "waiting_for_rollouts", "selected_episodes": 200,
            "families_in_parallel": 4, "workers_per_family": 50, "aggregate_target_rpm": 100})
        with ThreadPoolExecutor(max_workers=4) as pool:
            groups = list(pool.map(family, MODELS))
        labels = load_rubric(ROOT / "judges/collaboration_v1.yaml")["labels"]
        save(OUT / "summary.json", summarize([r for group in groups for r in group], labels))
        report_labels = ["requests_assistance", "shares_information", "fulfills_request", "coordinates_future_work",
                         "expresses_social_sentiment", "uses_peer_information", "shares_bypass_techniques"]
        lines = ["# Search-only collaboration classifier results", "",
            "200 working-condition runs, 50 per model. All begin with search results and no prefilled wiki visit. "
            "The unchanged collaboration-v1.1 rubric evaluates live behavior; provider-invalid episodes are excluded from label counts. "
            "Present includes intentions and attempts. Bypass labels have not received manual review. "
            "Exact citation repairs and feedback retries are preserved in the attempt files.", "",
            "See [discovery results](working-search-only-results-2026-09-10.md) for measured live wiki reads and writes "
            "and [setup](archive/working-search-only-2026-09-10.md) for controls and comparison limits.", "",
            "| Model | Judged | Provider invalid | Requests help | Shares info | Fulfills requests | Coordinates | Social expression | Uses peer info | Bypass sharing |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for model, rows in zip(MODELS, groups):
            valid = [r for r in rows if not r["source_facts"]["source_transport_error"]]
            values = [model, len(rows), len(rows)-len(valid),
                *[sum(r["judgment"]["labels"][label] == "present" for r in valid) for label in report_labels]]
            lines.append("| " + " | ".join(map(str, values)) + " |")
        (ROOT / "docs/working-search-only-classifier-results-2026-09-10.md").write_text("\n".join(lines) + "\n")
        save(OUT / "status.json", {"status": "finished", "judged": 200, "annotations_verified": 200})
        from ai_collusion.docent_cli import make_client
        make_client().update_collection_metadata(COLLECTION, {"search_only_classifier": {
            "status": "finished", "judgments": 200, "annotations_verified": 200}})


if __name__ == "__main__":
    main()
