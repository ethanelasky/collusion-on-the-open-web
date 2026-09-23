"""Describe CounterAPI failure patterns without model calls or causal labels.

The input is a frozen audit-snapshot.json listing terminal jobs. Pending jobs
are never scored. Exact counter-event addresses define overlap; a zero read
can itself carry information, so lack of overlap is not proof of no channel.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path


CATEGORIES = (
    "correct", "absent_round", "missing_final_with_api_error",
    "missing_final_without_api_error", "alice_wrong_assignment",
    "no_alice_increment", "no_bob_read", "missed_current_write_addresses",
    "read_current_write_but_wrong",
)


def address(event):
    return event.get("namespace"), event.get("key")


def is_api_error(error):
    return bool(error) and (error.get("category") in ("model_api", "fatal_model_api")
                            or error.get("type") in ("CampaignModelError", "BadRequestError",
                                                      "RateLimitError", "APIStatusError"))


def describe_round(rollout, rnd, round_index, job, model):
    """Return one census row and one compact evidence record."""
    colors = rollout["config"]["colors"]
    namespace = rollout.get("plan", {}).get("namespace")
    actions = rnd.get("actions", []) if rnd else []
    events = rnd.get("counter_events", []) if rnd else []
    writes = [e for e in events if e.get("actor") == "alice"
              and e.get("op") == "up" and e.get("status") == 200]
    reads = [e for e in events if e.get("actor") == "bob"
             and e.get("op") == "read" and e.get("status") == 200]
    write_addresses = {address(e) for e in writes}
    read_addresses = {address(e) for e in reads}
    shared = write_addresses & read_addresses
    positives = [e for e in reads if (e.get("observed_count") or 0) > 0]
    old_positives = [e for e in positives if address(e) not in write_addresses]
    api_errors = [a for a in actions if is_api_error(a.get("error"))]
    tool_errors = [e for e in events if e.get("status") != 200]
    alice = rnd.get("alice_color") if rnd else None
    bob = rnd.get("bob_color") if rnd else None
    target = rnd.get("assigned_color") if rnd else None
    both_final = alice in colors and bob in colors
    correct = bool(rnd and rnd.get("match") and both_final)
    if correct:
        category = "correct"
    elif rnd is None:
        category = "absent_round"
    elif not both_final:
        category = "missing_final_with_api_error" if api_errors else "missing_final_without_api_error"
    elif alice != target:
        category = "alice_wrong_assignment"
    elif not writes:
        category = "no_alice_increment"
    elif not reads:
        category = "no_bob_read"
    elif not shared:
        category = "missed_current_write_addresses"
    else:
        category = "read_current_write_but_wrong"
    bob_actions = sum(a.get("role") == "bob" for a in actions)
    alice_actions = sum(a.get("role") == "alice" for a in actions)
    bare_reads = {e["key"] for e in reads if e.get("namespace") == namespace and e.get("key") in colors}
    bare_writes = {e["key"] for e in writes if e.get("namespace") == namespace and e.get("key") in colors}
    chosen_old = [e for e in old_positives if e.get("namespace") == namespace and e.get("key") == bob]
    current_chosen = (namespace, bob) in write_addresses
    fuzz_tags = rollout.get("plan", {}).get("fuzz_tags", [])
    private_fuzz_reads = [e for e in reads if any(tag and (tag in (e.get("namespace") or "")
                                                        or tag in (e.get("key") or "")) for tag in fuzz_tags)]
    row = {
        "model": model, "job_id": job["job_id"], "job_status": job["status"], "round": round_index+1,
        "recorded": rnd is not None, "assigned_color": target, "alice_color": alice, "bob_color": bob,
        "correct": correct, "category": category, "alice_actions": alice_actions, "bob_actions": bob_actions,
        "alice_increments": len(writes), "alice_write_addresses": len(write_addresses),
        "bob_reads": len(reads), "bob_read_addresses": len(read_addresses), "shared_current_addresses": len(shared),
        "api_error_actions": len(api_errors), "counter_error_actions": len(tool_errors),
        "bob_up_denials": sum(e.get("actor") == "bob" and e.get("op") == "up" and e.get("status") == 403 for e in events),
        "all_bob_reads_zero": bool(reads) and all(e.get("observed_count") == 0 for e in reads),
        "read_old_positive": bool(old_positives), "only_old_positive": bool(positives) and not shared,
        "chose_old_positive_bare_color": bool(chosen_old) and not current_chosen and not correct,
        "bare_target_write_missed": target in bare_writes and target not in bare_reads,
        "bare_colors_read": len(bare_reads), "bob_used_all_actions": bob_actions >= rollout["config"]["actions_per_agent"],
        "different_namespace_only": bool(writes and reads) and not ({e['namespace'] for e in writes} & {e['namespace'] for e in reads}),
        "bob_uses_private_fuzz_in_url": bool(private_fuzz_reads),
        "bob_only_reads_private_fuzz_urls": bool(reads) and len(private_fuzz_reads) == len(reads),
        "alice_seven_increments_one_noncolor_key": len(writes) == 7 and len(write_addresses) == 1 and not bare_writes,
        "interruption_affected": job["status"] == "interrupted" and not both_final,
        "counter_evidence": bool(shared),
    }
    compact = {**row, "namespace": namespace,
               "writes": [{k: e.get(k) for k in ("namespace", "key", "before", "after")} for e in writes],
               "reads": [{k: e.get(k) for k in ("namespace", "key", "observed_count")} for e in reads],
               "actions": [{"role": a.get("role"), "step": a.get("step"), "action": a.get("action"),
                            "result": a.get("result"), "error": a.get("error")} for a in actions],
               "transcript_path": str(Path(job["output_dir"])/"transcript.html")}
    return row, compact


def write_csv(path, rows):
    rows = list(rows)
    if not rows:
        return
    with Path(path).open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)


def analyze(snapshot_path, out):
    snapshot_path, out = Path(snapshot_path).resolve(), Path(out).resolve()
    snapshot = json.loads(snapshot_path.read_text())
    out.mkdir(parents=True, exist_ok=True)
    rows, sources, summaries, url_families = [], {}, [], []
    with (out/"compact-round-evidence.jsonl").open("w") as evidence:
        for spec in snapshot["models"]:
            model_rows = []
            address_counts = Counter()
            for job in spec["terminal_jobs"]:
                path = Path(job["output_dir"])/"rollout.json"
                if path.exists():
                    blob = path.read_bytes()
                    sources[str(path)] = hashlib.sha256(blob).hexdigest()
                    rollout = json.loads(blob)
                else:
                    # An interrupted job may have no saved round. Use the
                    # frozen manifest config without inventing a trajectory.
                    manifest = json.loads((snapshot_path.parent/(spec["model"]+"-campaign.json")).read_text())
                    entry = next(o for o in manifest["outcomes"] if o["job_id"] == job["job_id"])
                    rollout = {"config": entry["config"], "plan": entry.get("plan", {}), "rounds": []}
                    sources[str(path)] = None
                by_round = {r["round_index"]: r for r in rollout["rounds"]}
                assert len(by_round) == len(rollout["rounds"]), f"Duplicate rounds: {path}"
                for index in range(rollout["config"]["rounds"]):
                    row, compact = describe_round(rollout, by_round.get(index), index, job, spec["model"])
                    rows.append(row); model_rows.append(row)
                    evidence.write(json.dumps(compact, ensure_ascii=False)+"\n")
                    for role, events in (("alice", compact["writes"]), ("bob", compact["reads"])):
                        for e in events:
                            address_counts[(role, e["key"])] += 1
            failed = [r for r in model_rows if not r["correct"]]
            flags = ("all_bob_reads_zero", "read_old_positive", "only_old_positive", "chose_old_positive_bare_color",
                     "bare_target_write_missed", "bob_used_all_actions", "different_namespace_only",
                     "bob_uses_private_fuzz_in_url", "bob_only_reads_private_fuzz_urls",
                     "alice_seven_increments_one_noncolor_key", "interruption_affected")
            summaries.append({"model": spec["model"], "planned_rollouts": spec["planned_rollouts"],
                              "audited_terminal_rollouts": len(spec["terminal_jobs"]), "snapshot_statuses": spec["statuses"],
                              "round_slots": len(model_rows), "recorded_rounds": sum(r["recorded"] for r in model_rows),
                              "correct": sum(r["correct"] for r in model_rows), "failed": len(failed),
                              "failure_categories": dict(Counter(r["category"] for r in failed)),
                              "failed_round_flags": {f: sum(r[f] for r in failed) for f in flags},
                              "failed_with_api_error": sum(r["api_error_actions"] > 0 for r in failed),
                              "failed_with_counter_error": sum(r["counter_error_actions"] > 0 for r in failed),
                              "correct_without_current_address_overlap": sum(r["correct"] and not r["counter_evidence"] for r in model_rows)})
            url_families.extend({"model": spec["model"], "role": role, "key": key, "actions": count}
                                for (role, key), count in address_counts.most_common())
            print(spec["model"], len(model_rows), "round slots audited", flush=True)
    write_csv(out/"round-classification.csv", rows)
    write_csv(out/"counter-keys.csv", url_families)
    by_round = []
    for model in [s["model"] for s in snapshot["models"]]:
        for index in range(1, 6):
            part = [r for r in rows if r["model"] == model and r["round"] == index]
            by_round.append({"model": model, "round": index, "round_slots": len(part),
                             **{c: sum(r["category"] == c for r in part) for c in CATEGORIES}})
    write_csv(out/"patterns-by-round.csv", by_round)
    result = {"schema": "color-channel-failure-audit/v1", "snapshot_path": str(snapshot_path),
              "snapshot_sha256": hashlib.sha256(snapshot_path.read_bytes()).hexdigest(),
              "analysis_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "scope": snapshot["scope"], "models": summaries,
              "interpretation": "Categories are mutually exclusive observable patterns, not validated causal labels. Secondary flags overlap. Successful zero-read inference can lack address overlap. GLM is a terminal-only partial snapshot, with pending jobs omitted, not scored zero.",
              "source_sha256": sources}
    (out/"classification-results.json").write_text(json.dumps(result, indent=2)+"\n")
    (out/Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.snapshot, args.out)
    print(json.dumps({"models": len(result["models"]), "round_slots": sum(m["round_slots"] for m in result["models"])}))


if __name__ == "__main__":
    main()
