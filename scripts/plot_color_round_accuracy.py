"""Plot pooled round accuracy from completed color-game campaigns.

Requires matplotlib. Reads saved files only; no model or Docent calls.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


SETTINGS = ("guessing_only", "async_counter", "sync_counter")
TITLES = ("Guessing only", "Async counter", "Sync counter")
STYLES = {
    "gpt-6-astra-high": ("Astra", "#0072B2", "o"),
    "gpt-5.6-sol-high": ("Sol 5.6", "#E69F00", "s"),
    "gemini-3.8-flash-high": ("Gemini 3.8 Flash", "#CC79A7", "^"),
    "claude-haiku-4.5-thinking-26214": ("Haiku 4.5", "#7B61A8", "v"),
    "glm-5.3-high": ("GLM 5.3", "#D55E00", "P"),
    "deepseek-v4-flash-high": ("DeepSeek V4 Flash", "#009E73", "D"),
    "gpt-5.6-luna-high": ("Luna 5.6 (earlier)", "#707780", "X"),
}


def read(path):
    return json.loads(Path(path).read_text())


def aggregate(paths):
    rows, cohorts, sources = [], [], {}
    for directory in paths:
        manifest_path = directory / "campaign.json"
        manifest = read(manifest_path)
        summary = manifest["summary"]
        if not (manifest["status"].startswith("complete") and
                summary["completed_rollouts"] == summary["planned_rollouts"]):
            raise ValueError(f"Campaign is not complete: {directory}")
        model = manifest["model"]["name"]
        config = manifest["base_config"]
        expected = manifest["rollouts_per_setting"]
        n_rounds = config["rounds"]
        counters = defaultdict(lambda: defaultdict(int))
        indices = defaultdict(set)
        sources[str(manifest_path)] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        for outcome in manifest["outcomes"]:
            if not outcome["status"].startswith("complete"):
                raise ValueError("A planned rollout is not complete")
            path = Path(outcome["artifact_paths"]["json"])
            raw = path.read_bytes()
            rollout = json.loads(raw)
            sources[str(path)] = hashlib.sha256(raw).hexdigest()
            setting = rollout["config"]["setting"]
            index = rollout["config"]["rollout_index"]
            if index in indices[setting]:
                raise ValueError("Duplicate rollout index")
            indices[setting].add(index)
            if [r["round_index"] for r in rollout["rounds"]] != list(range(n_rounds)):
                raise ValueError("Missing or duplicate rounds")
            if rollout["config"]["colors"] != config["colors"]:
                raise ValueError("Inconsistent color set")
            for rnd in rollout["rounds"]:
                alice, bob = rnd.get("alice_color"), rnd.get("bob_color")
                correct = alice in config["colors"] and bob in config["colors"] and alice == bob
                if correct != rnd["match"]:
                    raise ValueError("Stored correctness does not match final choices")
                c = counters[(setting, rnd["round_index"] + 1)]
                c["correct"] += correct
                c["rollouts"] += 1
                c["missing_final_rounds"] += alice is None or bob is None
                c["invalid_for_analysis_rounds"] += not rnd["valid_for_analysis"]
        if set(indices) != set(SETTINGS) or any(len(v) != expected for v in indices.values()):
            raise ValueError("Incomplete setting cohort")
        if any(v != indices[SETTINGS[0]] for v in indices.values()):
            raise ValueError("Settings have different rollout indices")
        for setting in SETTINGS:
            if sum(counters[(setting, r)]["correct"] for r in range(1, n_rounds + 1)) != manifest["settings"][setting]["matched"]:
                raise ValueError("Recomputed totals differ from the campaign summary")
            for r in range(1, n_rounds + 1):
                counts = dict(counters[(setting, r)])
                if counts["rollouts"] != expected:
                    raise ValueError("Incorrect round denominator")
                rows.append({"model": model, "setting": setting, "round": r, **counts,
                             "accuracy": counts["correct"] / counts["rollouts"]})
        cohorts.append({"model": model, "campaign_id": manifest["campaign_id"],
                        "source": str(directory), "rounds": n_rounds, "colors": config["colors"],
                        "rollouts_per_setting": expected, "indices": sorted(indices[SETTINGS[0]])})
    if len({c["model"] for c in cohorts}) != len(cohorts):
        raise ValueError("Duplicate model campaigns need separate labels")
    for key in ("rounds", "colors", "rollouts_per_setting", "indices"):
        if any(c[key] != cohorts[0][key] for c in cohorts):
            raise ValueError(f"Campaigns have different {key}")
    return rows, cohorts, sources


def plot(rows, cohorts, out, *, selected=None, stem="correctness-by-round"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter

    available = {r["model"] for r in rows}
    order = [model for model in STYLES if model in available and (selected is None or model in selected)]
    if set(order) != (available if selected is None else available.intersection(selected)):
        raise ValueError("Add a plot style for the unknown model")
    n = cohorts[0]["rollouts_per_setting"]
    rounds = cohorts[0]["rounds"]
    chance = 1 / len(cohorts[0]["colors"])
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 12,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "axes.spines.left": False, "axes.spines.bottom": False,
                         "pdf.fonttype": 42, "svg.fonttype": "none"})
    fig, axes = plt.subplots(1, 3, figsize=(17, 6.4), sharey=True)
    for ax, setting, title in zip(axes, SETTINGS, TITLES):
        ax.set_axisbelow(True)
        ax.grid(axis="y", color="#e5e7eb", linewidth=0.9)
        ax.axhline(chance, color="#9ca3af", linestyle=(0, (4, 4)), linewidth=1.2, zorder=1)
        for model in order:
            values = sorted((r for r in rows if r["model"] == model and r["setting"] == setting), key=lambda r: r["round"])
            label, color, marker = STYLES[model]
            ax.plot([r["round"] for r in values], [r["accuracy"] for r in values],
                    label=label, color=color, marker=marker, linewidth=2.2,
                    markersize=6.5, markeredgewidth=0.8, markeredgecolor="white", zorder=3)
        ax.set(title=title, xlabel="Round", xlim=(0.8, rounds + 0.2), ylim=(-0.025, 1.05),
               xticks=range(1, rounds + 1), yticks=[0, 0.25, 0.5, 0.75, 1])
        ax.yaxis.set_major_formatter(PercentFormatter(1, decimals=0))
        ax.tick_params(length=0, pad=8)
        ax.set_title(title, fontsize=15, weight="bold", pad=14)
    axes[0].set_ylabel("Correct color matches", labelpad=12)
    fig.suptitle("Correctness by round", x=0.5, y=0.965, fontsize=23, weight="bold")
    fig.text(0.5, 0.896, f"Pooled across {n} rollouts per model and setting · each point is one round", ha="center", color="#4b5563", fontsize=13)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", bbox_to_anchor=(0.5, 0.095),
               ncols=len(order), frameon=False, fontsize=11, handlelength=2.2, columnspacing=1.4)
    earlier_note = "Luna used the earlier action format. " if "gpt-5.6-luna-high" in order else ""
    foot = (f"Dashed line: random guessing ({chance:.1%}). All planned rollouts are included; missing final answers count as incorrect.\n"
            + earlier_note + "All plotted model batches are complete.")
    fig.text(0.055, 0.025, foot, ha="left", va="bottom", color="#596273", fontsize=10.5, linespacing=1.55)
    fig.subplots_adjust(left=0.065, right=0.98, top=0.79, bottom=0.25, wspace=0.14)
    for suffix in ("png", "pdf", "svg"):
        fig.savefig(out / f"{stem}.{suffix}", dpi=200, facecolor="white", bbox_inches="tight")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparison", required=True, type=Path)
    parser.add_argument("--campaign", action="append", default=[], type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    spec = read(args.comparison)
    paths = [args.comparison.resolve().parent / m["directory"] / "runs" for m in spec["models"]]
    paths += [p.resolve() for p in args.campaign]
    rows, cohorts, sources = aggregate(paths)
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "correctness-by-round.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    metadata = {"created_utc": datetime.now(timezone.utc).isoformat(), "cohorts": cohorts,
                "metric": "Final color match / all planned rollouts at that round index",
                "missing_final_policy": "Count as incorrect; do not drop the round",
                "comparison": spec, "source_sha256": sources,
                "plot_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (args.out / "correctness-by-round-source.json").write_text(json.dumps(metadata, indent=2) + "\n")
    plot(rows, cohorts, args.out)
    plot(rows, cohorts, args.out, selected={"gpt-6-astra-high", "gpt-5.6-sol-high", "gpt-5.6-luna-high"},
         stem="correctness-by-round-astra-sol-luna")
    print(f"Wrote pooled accuracy for {len(cohorts)} models / {len(rows)} points to {args.out}")


if __name__ == "__main__":
    main()
