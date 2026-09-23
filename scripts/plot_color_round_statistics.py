"""Export confidence-interval figures from analyze_color_round_statistics.py."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, PercentFormatter

MODELS = {
    "gpt-6-astra-high": "Astra",
    "gemini-3.8-flash-high": "Gemini 3.8 Flash",
    "gpt-5.6-sol-high": "Sol 5.6",
    "gpt-5.6-luna-high": "Luna 5.6 (earlier)",
    "deepseek-v4-flash-high": "DeepSeek V4 Flash",
    "claude-haiku-4.5-thinking-26214": "Haiku 4.5",
    "glm-5.3-high": "GLM 5.3",
}
CONDITIONS = {
    "guessing_only": ("Guessing", "#7b8491", "o"),
    "async_counter": ("Async counter", "#0072b2", "s"),
    "sync_counter": ("Sync counter", "#d55e00", "^"),
}


def save(fig, out, stem):
    for suffix in ("png", "pdf", "svg"):
        fig.savefig(out / f"{stem}.{suffix}", dpi=190, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    data = json.loads(args.results.read_text())
    args.out.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "axes.spines.left": False, "axes.spines.bottom": False,
                         "pdf.fonttype": 42, "svg.fonttype": "none"})
    fig, axes = plt.subplots(2, 4, figsize=(17, 9.3), sharex=True, sharey=True)
    for ax, (model, label) in zip(axes.flat, MODELS.items()):
        ax.set_axisbelow(True)
        ax.grid(axis="y", color="#e5e7eb")
        ax.axhline(.125, color="#a1a7b0", linestyle=(0, (4, 4)), linewidth=1)
        for setting, (name, color, marker) in CONDITIONS.items():
            points = sorted((r for r in data["points"] if r["model"] == model and r["setting"] == setting), key=lambda r: r["round"])
            means = [r["accuracy"] for r in points]
            error = [[max(0, r["accuracy"]-r["wilson95_low"]) for r in points],
                     [max(0, r["wilson95_high"]-r["accuracy"]) for r in points]]
            ax.errorbar([r["round"] for r in points], means, yerr=error, label=name,
                        color=color, marker=marker, markersize=4.5, linewidth=1.7,
                        elinewidth=.8, capsize=2, alpha=.94)
        ax.set(title=label, xlim=(.75, 5.25), ylim=(-.02, 1.05), xticks=range(1, 6),
               yticks=[0, .25, .5, .75, 1])
        ax.set_title(label, fontsize=13, weight="bold", pad=10)
        ax.yaxis.set_major_formatter(PercentFormatter(1, decimals=0))
        ax.tick_params(length=0, pad=6, labelbottom=True)
    axes[1, 3].axis("off")
    handles, labels = axes[0, 0].get_legend_handles_labels()
    axes[1, 3].legend(handles, labels, loc="upper left", frameon=False, fontsize=12)
    axes[1, 3].text(.03, .59,
        "50 rollouts per point\n95% pointwise Wilson intervals\n\nDashed line: 12.5% chance\nMissing answers count as wrong\n\nLuna: earlier action format",
        transform=axes[1, 3].transAxes, va="top", fontsize=11.5, linespacing=1.6, color="#4b5563")
    for ax in axes[:, 0]:
        ax.set_ylabel("Correct color matches")
    for ax in axes[1, :3]:
        ax.set_xlabel("Round")
    fig.suptitle("Accuracy by round with 95% confidence intervals", fontsize=21, weight="bold", y=.985)
    fig.text(.5, .936, "Each rollout contributes one observation to each of five rounds", ha="center", fontsize=12.5, color="#4b5563")
    fig.text(.045, .02, "Intervals are pointwise, not a simultaneous band. Paired tests, not interval overlap, assess changes across rounds.", color="#4b5563", fontsize=11)
    fig.subplots_adjust(left=.065, right=.985, bottom=.09, top=.85, hspace=.36, wspace=.18)
    save(fig, args.out, "accuracy-by-round-95ci")

    fig, ax = plt.subplots(figsize=(12.8, 7.4))
    ax.set_axisbelow(True)
    ax.grid(axis="x", color="#e5e7eb")
    ax.axvline(0, color="#5b6574", linestyle="--", linewidth=1.1)
    for index, (model, label) in enumerate(MODELS.items()):
        for setting, offset in (("async_counter", -.14), ("sync_counter", .14)):
            row = next(r for r in data["results"] if r["model"] == model and r["setting"] == setting)
            name, color, marker = CONDITIONS[setting]
            delta = row["endpoint_delta"]
            ax.errorbar(delta, index+offset,
                        xerr=[[delta-row["endpoint_ci_low"]], [row["endpoint_ci_high"]-delta]],
                        color=color, marker=marker, markersize=7, capsize=3,
                        linewidth=1.6, label=name if index == 0 else None)
    ax.set(yticks=range(len(MODELS)), yticklabels=list(MODELS.values()), xlim=(-.5, .5),
           xlabel="Round 5 minus round 1 (percentage points)")
    ax.invert_yaxis()
    ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value*100:+.0f}" if value else "0"))
    ax.tick_params(length=0, pad=8)
    ax.legend(loc="lower right", frameon=False)
    fig.suptitle("Change from round 1 to round 5", fontsize=20, weight="bold", y=.97)
    fig.text(.56, .91, "Paired outcomes from 50 rollouts per model and setting", ha="center", color="#4b5563", fontsize=12)
    fig.text(.05, .025, "Bars: conservative 95% paired-difference intervals. They are pointwise; use the adjusted tests for claims across settings.", fontsize=10.5, color="#4b5563")
    fig.subplots_adjust(left=.23, right=.98, top=.86, bottom=.14)
    save(fig, args.out, "round5-minus-round1-95ci")
    print("Exported confidence-interval figures to", args.out)


if __name__ == "__main__":
    main()
