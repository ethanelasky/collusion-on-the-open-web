"""Validate a completed API batch; invalid episodes never enter outcome totals."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from ai_collusion.run_health import source_validity


def validate(run: Path, model: str, samples: int = 5, rounds: int = 7, *,
             max_turns: int = 40,
             arm_ids: tuple[str, ...] = ("working", "empty-success"),
             wiki_write_instructions: bool | None = None) -> dict:
    if not arm_ids or len(set(arm_ids)) != len(arm_ids):
        raise ValueError("arm_ids must be nonempty and unique")
    if wiki_write_instructions is not None and type(wiki_write_instructions) is not bool:
        raise ValueError("wiki_write_instructions must be a boolean or None")
    errors: list[str] = []
    records: list[dict] = []
    if not (run / "manifest.json").is_file():
        errors.append("missing manifest.json")
    paths = sorted(p for p in run.glob("*.json") if p.name != "manifest.json")
    if len(paths) != len(arm_ids) * samples:
        errors.append(f"expected {len(arm_ids) * samples} episode files, found {len(paths)}")
    for path in paths:
        try:
            rec = json.loads(path.read_text())
            if not isinstance(rec, dict):
                raise ValueError("episode must be an object")
            records.append(rec)
            ep = rec.get("episode") or {}
            failures = []
            if rec.get("model", {}).get("name") != model:
                failures.append("unexpected model")
            if source_validity(rec)["source_transport_status"] != "valid":
                failures.append("invalid or unknown source transport status")
            if rec.get("num_live_problems") != rounds or len(ep.get("rounds", [])) != rounds:
                failures.append(f"expected {rounds} live rounds")
            if rec.get("max_tokens") != 4096 or rec.get("max_turns") != max_turns:
                failures.append("unexpected generation or turn cap")
            if wiki_write_instructions is not None:
                for location, config in (("record", rec), ("arm", rec.get("arm") or {}),
                                         ("resolved_config", rec.get("resolved_config") or {})):
                    if config.get("wiki_write_instructions") is not wiki_write_instructions:
                        failures.append(f"unexpected wiki_write_instructions in {location}")
            if not ep.get("turns"):
                failures.append("no live turns")
            if failures:
                errors.append(f"{path.name}: {', '.join(failures)}")
        except (ValueError, TypeError, AttributeError) as exc:
            errors.append(f"{path.name}: malformed episode ({exc})")
    counts = Counter(rec.get("arm_id") for rec in records)
    expected = {arm: samples for arm in arm_ids}
    if dict(counts) != expected:
        errors.append(f"expected arm counts {expected}, found {dict(counts)}")
    for arm in expected:
        indices = [rec.get("sample_index") for rec in records if rec.get("arm_id") == arm]
        if sorted(indices, key=str) != sorted(range(samples), key=str):
            errors.append(f"{arm}: sample indices must be exactly 0..{samples - 1}")
    report = {"valid": not errors, "model": model, "records": len(records), "errors": errors}
    if errors:
        return report
    report["arms"] = {}
    for arm in expected:
        episodes = [rec["episode"] for rec in records if rec["arm_id"] == arm]
        report["arms"][arm] = {
            "episodes": len(episodes),
            "end_reasons": dict(Counter(ep.get("end_reason") for ep in episodes)),
            "incomplete_episodes": sum(any(r.get("answered_at") is None and not r.get("missed")
                                            for r in ep["rounds"]) for ep in episodes),
            "rounds_answered": sum(r.get("answered_at") is not None for ep in episodes for r in ep["rounds"]),
            "rounds_missed": sum(bool(r.get("missed")) for ep in episodes for r in ep["rounds"]),
        }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--rounds", type=int, default=7)
    parser.add_argument("--max-turns", type=int, default=40)
    parser.add_argument("--arm-ids", nargs="+", default=["working", "empty-success"])
    parser.add_argument("--wiki-write-instructions", action=argparse.BooleanOptionalAction,
                        default=None)
    args = parser.parse_args()
    report = validate(args.run, args.model, args.samples, args.rounds,
                      max_turns=args.max_turns, arm_ids=tuple(args.arm_ids),
                      wiki_write_instructions=args.wiki_write_instructions)
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["valid"] else 1)


if __name__ == "__main__":
    main()
