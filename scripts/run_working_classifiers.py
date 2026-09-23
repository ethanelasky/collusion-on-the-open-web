"""Judge non-Kimi families immediately; gate only Kimi on its verified repair."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_working_extension import save, COLLECTION
from ai_collusion.judge_cli import _attempts, _latest, summarize
from ai_collusion.judge import load_rubric
from ai_collusion.run_health import health

MODELS = ["gpt-5.6", "qwen3.8-27b", "glm-5.3", "kimi-k3"]
OUT = ROOT / "judgments/working-write-on-extension-20260910"


def run_path(model):
    if model == "kimi-k3":
        return ROOT / "data/kimi-budget-recovery-20260910/selected"
    name = ("working-write-on-native-tools-repair-20260910-kimi-k3" if model == "kimi-k3" else
            f"working-write-on-50-extension-20260910-{model}")
    return ROOT / "runs" / name


def classify(model, requests_per_minute=33, *, source_run=None, output_root=OUT, expected_count=45):
    out = output_root / model
    out.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "ai_collusion.judge_cli", "--run", str(source_run or run_path(model)),
        "--models", str(ROOT / "experiments/hinted-models.yaml"), "--judge", "gpt-5.6",
        "--rubric", str(ROOT / "judges/collaboration_v1.yaml"), "--out", str(out),
        "--workers", str(expected_count), "--requests-per-minute", str(requests_per_minute),
        "--env-file", ".env",
        "--collection-id", COLLECTION]
    for attempt in range(1, 4):
        with (out / "runner.log").open("a") as log:
            result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        latest = _latest(_attempts(out))
        ledger = out / "docent-ledger.jsonl"
        uploads = {r["source_path"]: r for r in [json.loads(s) for s in ledger.read_text().splitlines()]} if ledger.exists() else {}
        success = (len(latest) == expected_count and all(r.get("judgment") and not r.get("error") for r in latest)
                   and len(uploads) == expected_count and all(r["status"] in ["uploaded", "skipped"] for r in uploads.values()))
        save(out / "status.json", {"attempt": attempt, "exit_code": result.returncode,
                                   "judged": len(latest), "verified": success})
        if success:
            print(f"{model}: {expected_count} judgments and annotation uploads complete", flush=True)
            return latest
    from scripts.repair_judge_citations import repair_family
    repaired = repair_family(out)
    assert len(repaired) == expected_count, "Classifier did not produce the expected number of source judgments"
    save(out / "status.json", {"attempt": 3, "judged": len(repaired), "verified": True,
                               "citation_repair_applied": True})
    return repaired


def write_report(groups, models=MODELS):
    labels = ["requests_assistance", "shares_information", "fulfills_request", "coordinates_future_work",
              "expresses_social_sentiment", "uses_peer_information", "shares_bypass_techniques"]
    lines = ["# Expanded working-condition classifier results", "",
        f"This report covers {sum(len(g) for g in groups)} new working-condition episodes. "
        + ("Kimi uses the repaired native-tool interface; " if "kimi-k3" in models else "Kimi is pending repair validation and is excluded from this table. ")
        + "The 45 failed text-interface Kimi runs are retained separately and excluded here. "
        "The earlier 20 judgments remain in the [original report](archive/working-condition-judgments-2026-09-09.md).", "",
        "Counts indicate runs with evidence labeled present, including intentions and attempts. "
        "Incomplete episodes remain separate from resolved episodes in the source-quality columns. "
        "Provider-invalid episodes are excluded from the positive counts in the table. "
        "Bypass-sharing counts are classifier output and have not received manual review. "
        "Citation repairs are recorded separately: wrong turn indices are corrected only for unique exact "
        "source matches, and unsupported quotations require a new validated judge response. Original outputs are retained.", "",
        "| Model | Judged | Provider invalid | Resolved | Requests help | Shares info | Fulfills requests | Coordinates | Social expression | Uses peer info | Bypass sharing |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for model, items in zip(models, groups):
        valid = [i for i in items if not i["source_facts"]["source_transport_error"]]
        values = [model + (" (native tools)" if model == "kimi-k3" else ""), len(items), len(items)-len(valid),
                  sum(i["source_facts"]["all_rounds_resolved"] for i in valid),
                  *[sum(i["judgment"]["labels"][label] == "present" for i in valid) for label in labels]]
        lines.append("| " + " | ".join(map(str, values)) + " |")
    lines += ["", "The Kimi interface changed, so these 45 native-tool results should be analyzed as a distinct "
              "configuration from the original five text-tool Kimi runs. All task prompts retain the same wiki prefill.", "",
              f"[Docent collection](https://docent.transluce.org/dashboard/{COLLECTION}). "
              "Per-model JSON/CSV summaries and evidence are stored in "
              "`judgments/working-write-on-extension-20260910/`.", ""]
    (ROOT / "docs/working-condition-classifier-results-2026-09-10.md").write_text("\n".join(lines))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    import fcntl
    with (OUT / ".supervisor.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        old = json.loads((ROOT / "judgments/write-on-100-20260909/manifest.json").read_text())
        assert load_rubric(ROOT / "judges/collaboration_v1.yaml")["sha256"] == old["settings"]["rubric_sha256"]
        from dataclasses import asdict
        from ai_collusion.runner import load_models
        assert asdict(load_models(ROOT / "experiments/hinted-models.yaml", only=["gpt-5.6"])[0]) == old["settings"]["judge_config"]
        save(OUT / "status.json", {"status": "running_non_kimi", "classifier_calls_started": True,
                                  "families_in_parallel": 3, "workers_per_family": 45,
                                  "target_requests_per_minute": 99, "selected_episodes": 135})
        with ThreadPoolExecutor(max_workers=3) as pool:
            groups = list(pool.map(classify, MODELS[:3]))
        rubric = load_rubric(ROOT / "judges/collaboration_v1.yaml")
        save(OUT / "summary.json", summarize([i for group in groups for i in group], rubric["labels"]))
        write_report(groups, MODELS[:3])
        save(OUT / "status.json", {"status": "non_kimi_finished_waiting_for_kimi",
                                  "classifier_calls_started": True, "judged": 135, "annotations_verified": 135})
        repair = ROOT / "data/kimi-budget-recovery-20260910"
        while True:
            state = json.loads((repair / "status.json").read_text())
            if state["status"] == "finished":
                break
            if subprocess.run(["tmux", "has-session", "-t", "collusion-kimi-budget-recovery-20260910"],
                              capture_output=True).returncode:
                raise RuntimeError("Kimi repair stopped before verification; non-Kimi classifiers completed, Kimi classifier not launched")
            time.sleep(15)
        assert json.loads((ROOT / "data/kimi-native-repair-20260910/pilot.json").read_text())["verified"]
        rows = [json.loads(p.read_text()) for p in run_path("kimi-k3").glob("*env-neutral*.json")]
        assert len(rows) == 45
        assert all(not r.get("error") and not health(r)["provider_response_error"]
                   and not health(r)["provider_blocked"]
                   and not health(r)["unparsed_native_tool_tokens"]
                   and not health(r)["model_reports_tools_disabled"] for r in rows), "Repair has unresolved interface/provider failures"
        assert all(r["arm_id"] == "working-write-on" for r in rows)
        save(OUT / "status.json", {"status": "running_kimi", "classifier_calls_started": True,
                                  "non_kimi_judgments": 135, "target_requests_per_minute": 100})
        groups.append(classify("kimi-k3", requests_per_minute=100))
        save(OUT / "summary.json", summarize([i for group in groups for i in group], rubric["labels"]))
        write_report(groups)
        save(OUT / "status.json", {"status": "finished", "judged": 180, "annotations_verified": 180})
        from ai_collusion.docent_cli import make_client
        make_client().update_collection_metadata(COLLECTION, {
            "working_preview_run_count": 265 + state["uploaded_retries"], "working_preview_annotated_runs": 200,
            "kimi_repair": {"status": "finished", "interface": "native tools", "runs": 45},
            "working_extension_classifier": {"status": "finished", "new_judgments": 180,
                                              "excluded_original_kimi_interface_failures": 45},
        })
        print("All four classifier batches complete; Markdown report written.", flush=True)


if __name__ == "__main__":
    main()
