"""Print the per-model x arm collaboration table from one or more judge output directories.

Counts are rollouts with positive evidence (intentions, attempts or executions, incomplete
rollouts included, prefilled actions excluded) out of the judged rollouts in that cell, the same
convention as docs/archive/working-condition-judgments-2026-09-09.md. Interface-limited and turn-capped
counts are shown so attrition can be read alongside the labels.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

LABELS = [("requests_assistance", "req"), ("shares_information", "share"), ("fulfills_request", "fulfil"),
          ("coordinates_future_work", "coord"), ("expresses_social_sentiment", "social"),
          ("uses_peer_information", "peer"), ("shares_bypass_techniques", "bypass")]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dirs", nargs="+", type=Path, help="judge --out directories (later ones override earlier cells)")
    ap.add_argument("--markdown", action="store_true")
    args = ap.parse_args()
    cells: dict[tuple[str, str], dict] = {}
    for d in args.dirs:
        for g in json.loads((d / "summary.json").read_text())["groups"]:
            cells[(g["model"], g["arm_id"])] = g
    arms = sorted({a for _, a in cells}, key=lambda a: (not a.startswith("working"), a.endswith("on")))
    models = sorted({m for m, _ in cells})
    head = ["model", "arm", "n", "judged", "fail", "iface", "cap"] + [s for _, s in LABELS]
    rows = []
    for m in models:
        for a in arms:
            g = cells.get((m, a))
            if not g:
                continue
            rows.append([m, a, g["source_count"], g["judged_success"], g["judge_failures"], g["interface_limited"],
                         g["max_turn_censored"]] + [g["labels"][l]["present"] for l, _ in LABELS])
    if args.markdown:
        print("| " + " | ".join(head) + " |")
        print("|" + "---|" * len(head))
        for r in rows:
            print("| " + " | ".join(str(x) for x in r) + " |")
    else:
        w = [max(len(str(x)) for x in col) for col in zip(head, *rows)]
        for r in [head] + rows:
            print("  ".join(str(x).ljust(n) for x, n in zip(r, w)))


if __name__ == "__main__":
    main()
