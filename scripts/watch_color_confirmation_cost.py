"""Stop named color campaigns at a fixed, saved-usage spending threshold.

This is a spending guard, not an invoice cap: in-flight requests, delayed usage,
and charges without saved responses can increase the final bill. It never reads
correctness and never marks queued work complete for statistical analysis.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("Cost and usage values must be finite nonnegative numbers")
    return value


def estimate(manifest, input_rate, output_rate):
    """Use each outcome once, charging the greater of token estimate and bill."""
    total = 0.0
    unmeasured = 0
    for outcome in manifest.get("outcomes", []):
        summary = outcome.get("summary") or {}
        usage = summary.get("usage") or {}
        reported = summary.get("cost")
        if not usage and reported is None:
            unmeasured += int(outcome.get("status", "").startswith("complete"))
            continue
        token_cost = (number(usage.get("prompt_tokens", 0)) * number(input_rate)
                      + number(usage.get("completion_tokens", 0)) * number(output_rate)) / 1_000_000
        total += max(token_cost, number(reported) if reported is not None else 0)
    return total, unmeasured


def check(config, *, stop=False):
    threshold = number(config["threshold_usd"])
    if threshold <= 0 or not config.get("campaigns"):
        raise ValueError("Need a positive threshold and named campaigns")
    rows, directories = [], []
    for spec in config["campaigns"]:
        directory = Path(spec["directory"]).resolve()
        if directory in directories:
            raise ValueError("Duplicate campaign directory")
        directories.append(directory)
        launch_path = directory / "launch.json"
        if hashlib.sha256(launch_path.read_bytes()).hexdigest() != spec["launch_sha256"]:
            raise ValueError("Campaign launch differs from the budget plan")
        manifest_path = directory / "runs/campaign.json"
        if not manifest_path.exists():
            rows.append({"directory": str(directory), "status": "preparing", "cost_estimate_usd": 0,
                         "unmeasured_complete_rollouts": 0})
            continue
        manifest = json.loads(manifest_path.read_text())
        cost, missing = estimate(manifest, spec["input_per_million"], spec["output_per_million"])
        rows.append({"directory": str(directory), "status": manifest["status"],
                     "cost_estimate_usd": cost, "unmeasured_complete_rollouts": missing})
    total = sum(row["cost_estimate_usd"] for row in rows)
    reason = ("unmeasured_complete_rollouts" if any(r["unmeasured_complete_rollouts"] for r in rows)
              else "spending_threshold" if total >= threshold else None)
    now = datetime.now(timezone.utc).isoformat()
    receipt = {"created_utc": now, "pid": os.getpid(), "threshold_usd": threshold,
               "cost_estimate_usd": total, "stop_reason": reason, "campaigns": rows,
               "note": "Saved usage estimate; delayed and in-flight charges can increase the bill. Queued work remains incomplete."}
    if stop and reason:
        for directory in directories:
            target = directory / "stop-request.json"
            try:
                with target.open("x") as handle:
                    json.dump({"reason": "cost_guard_" + reason, "at_utc": now,
                               "cost_estimate_usd": total, "threshold_usd": threshold}, handle)
            except FileExistsError:
                pass  # Preserve any earlier user stop.
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=30)
    parser.add_argument("--max-hours", type=float, default=168)
    args = parser.parse_args()
    if not 1 <= args.interval <= 60 or not 0 < args.max_hours <= 720:
        parser.error("Use interval 1–60 seconds and duration at most 720 hours")
    original = args.plan.read_bytes()
    config = json.loads(original)
    out = args.plan.parent
    with (out / ".cost-guard.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock.seek(0); lock.truncate(); lock.write(str(os.getpid()) + "\n"); lock.flush()
        deadline = time.monotonic() + args.max_hours * 3600
        while True:
            if args.plan.read_bytes() != original:
                raise ValueError("Frozen budget plan changed")
            receipt = check(config, stop=True)
            receipt["plan_sha256"] = hashlib.sha256(original).hexdigest()
            (out / "cost-guard-status.json").write_text(json.dumps(receipt, indent=2) + "\n")
            print(json.dumps({k: receipt[k] for k in ("created_utc", "cost_estimate_usd", "stop_reason")}), flush=True)
            if receipt["stop_reason"] or all(r["status"].startswith("complete") for r in receipt["campaigns"]):
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(args.interval, remaining))


if __name__ == "__main__":
    main()
