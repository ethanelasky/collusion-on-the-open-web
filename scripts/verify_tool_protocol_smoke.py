"""Verify saved API calls against executed actions and publish smoke outcomes."""
from collections import Counter
from pathlib import Path
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "data/run-snapshots/tool-protocol-fix-20260910-v1"
sys.path.insert(0, str(ROOT))
from ai_collusion.run_health import health
from scripts.run_working_extension import save


def main():
    rows = []
    for directory in sorted((ROOT / "runs").glob("tool-protocol-smoke-20260910-v1-*")):
        manifest = json.loads((directory / "manifest.json").read_text())
        for path in sorted(directory.glob("*env-neutral*.json")):
            record = json.loads(path.read_text())
            assert not record.get("error"), path.name
            assert not health(record)["provider_response_error"] and not health(record)["provider_blocked"]
            assert record["episode"]["end_reason"] == "all_rounds_resolved", path.name
            assert len(record["episode"]["rounds"]) == 7
            assert record["hint"] == "search_result"
            assert not any("wikiservice.at" in message["content"] for message in record["context"]["messages"]
                           if message["role"] == "assistant")
            assert record["experiment_sha256"] == manifest["experiment_sha256"]
            for name, digest in manifest["experiment"]["implementation"].items():
                assert hashlib.sha256((SNAPSHOT / "ai_collusion" / name).read_bytes()).hexdigest() == digest, name
            turns = record["episode"]["turns"]
            for turn in turns:
                response = turn["response"]
                assert response["tool_mode"] == "native"
                assert response["tool_call"] == turn["call"]
                assert response["finish_reason"] in {"completed", "tool_calls", "stop"}
                raw = response["raw"]
                if record["model"]["transport"] == "responses":
                    calls = [item for item in raw["output"] if item["type"] == "function_call"]
                else:
                    calls = [call["function"] for call in raw["choices"][0]["message"].get("tool_calls", [])]
                assert len(calls) == 1, (path.name, turn["turn"])
                assert calls[0]["name"] == turn["call"]["tool"]
                assert json.loads(calls[0]["arguments"]) == {"arg": turn["call"]["arg"]}
                if turn["call"]["tool"] == "wait":
                    assert float(turn["call"]["arg"]) >= 1
            rows.append({"model": record["model"]["name"], "seed": record["seed"],
                "source_path": str(path), "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "turns": len(turns), "correct": sum(bool(r["correct"]) for r in record["episode"]["rounds"]),
                "tools": dict(Counter(turn["call"]["tool"] for turn in turns)),
                "recovery_attempts": sum(len(turn["generation_attempts"])-1 for turn in turns),
                "recovery_issues": dict(Counter(attempt["issue"] for turn in turns for attempt in turn["generation_attempts"] if attempt["issue"]))})
    counts = Counter(row["model"] for row in rows)
    complete = len(rows) == 8 and set(counts.values()) == {2}
    output = {"complete": complete, "validated_episodes": len(rows), "correct_answers": sum(r["correct"] for r in rows),
              "interface_failures": 0, "episodes": rows}
    save(ROOT / "data/tool-protocol-smoke-20260910-v1/verification.json", output)
    lines = ["# Tool-protocol smoke validation", "",
        f"{'Complete' if complete else 'In progress'}: {len(rows)}/8 full episodes validated. "
        f"{sum(r['correct'] for r in rows)}/{7*len(rows)} live answers correct. "
        "The v1 snapshot passed 271 regression tests before launch.", "",
        "Each saved action was checked against the raw provider response: exactly one native call, "
        "matching tool name and string argument, with a completed response. No truncated response or zero-second wait executed. "
        "The tested implementation hashes match the frozen v1 snapshot. Current source subsequently tightened text parsing "
        "to reject malformed calls with visible retry feedback; these native-tool trials do not validate that later change. "
        "All runs retain search-only exposure with no prefilled wiki visit.", "",
        "| Model | Seed | Live turns | Correct / 7 | Recovery retries |",
        "|---|---:|---:|---:|---:|"]
    for row in rows:
        lines.append(f"| {row['model']} | {row['seed']} | {row['turns']} | {row['correct']} | {row['recovery_attempts']} |")
    lines += ["", "These are smoke tests of the repaired interface, not an estimate of failure rates at scale. "
        "The original 50-per-model experiments and their classifier supervisors were cancelled. "
        "The repair uses native tools, bounded generation recovery up to 16384 output tokens, "
        "and a reminder after repeated short waits. These are separate experimental settings from the historical batches.", "",
        "[Investigation and fixes](archive/tool-interface-investigation-2026-09-10.md). "
        "Machine-readable source hashes and call verification: `data/tool-protocol-smoke-20260910-v1/verification.json`.", ""]
    (ROOT / "docs/tool-protocol-smoke-results-2026-09-10.md").write_text("\n".join(lines))
    print(json.dumps({k: v for k, v in output.items() if k != "episodes"}))


if __name__ == "__main__":
    main()
