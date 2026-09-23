"""A full-width 1x4 bar chart of the verified September 17 classifier counts.

Run: uv run --no-project --with matplotlib==3.11.2 python scripts/plot_classifier_behaviors_bars.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.plot_classifier_behaviors import DATA, LABELS, MODELS, STYLE, load_counts

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

OUT = ROOT / "docs/figures/classifier-behaviors-bars-2026-09-18"
DISPLAY_LABELS = ["Refreshed wiki" if label == "Coordinate" else label for label in LABELS]
TITLES = ["GPT 5.6-Sol", "Qwen 3.8 27b", "Kimi K3", "DeepSeek V4.1\nFlash"]
ARMS = {"working": ("#4D78B8", -.18), "slow": ("#E3A04B", .18)}
BAR_STYLE = {**STYLE, "axes.titlesize": 10, "ytick.labelsize": 10.5,
             "xtick.labelsize": 9, "legend.fontsize": 10}
CAPTION = (
    "Supported positive classifier judgments by model and condition, out of 50 episodes "
    "per cell (400 total). Blue: Working; orange: Slow. Labels overlap and can reflect "
    "expression, attempts, or execution; uncertain judgments count as zero. "
    "Refreshed wiki is the display label for the original Coordinate classifier category, "
    "which includes wiki polling and coordination plans; its counts are unchanged. "
    "Peer use includes proposed use of peer information. Bypass can include intended "
    "wiki-editing functionality. Qwen pools provider histories; comparisons are descriptive.\n"
)


def make_figure(counts):
    fig, axes = plt.subplots(1, 4, figsize=(6.75, 3.45), sharex=True, sharey=True)
    fig.subplots_adjust(left=.205, right=.985, bottom=.16, top=.78, wspace=.20)
    for ax, model, title in zip(axes, MODELS, TITLES):
        for arm, (color, offset) in ARMS.items():
            ax.barh([i + offset for i in range(len(LABELS))],
                    [counts[model, arm][label] for label in LABELS],
                    height=.31, color=color, edgecolor="none", zorder=3)
        ax.set(xlim=(0, 52), ylim=(6.6, -.6), xticks=[0, 25, 50],
               yticks=range(len(LABELS)), yticklabels=DISPLAY_LABELS)
        # Bottom alignment gives all model names a common baseline.
        ax.set_title(title, fontweight="bold", pad=8, va="bottom")
        ax.set_axisbelow(True)
        ax.grid(axis="x", color="#E2E2E2", linewidth=.5)
        ax.tick_params(length=0, pad=4)
        for spine in ax.spines.values():
            spine.set_visible(False)
    handles = [Patch(facecolor=color, label=arm.title()) for arm, (color, _) in ARMS.items()]
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(.57, .99),
               ncol=2, frameon=False, handlelength=1.2, handleheight=.8,
               handletextpad=.5, columnspacing=1.8)
    fig.text(.59, .04, "Episodes exhibiting behavior (out of 50)", fontsize=10,
             ha="center", va="center")
    return fig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    counts, validation = load_counts()
    with plt.rc_context(BAR_STYLE):
        fig = make_figure(counts)
        fig.canvas.draw()
        bounds = fig.get_tightbbox(fig.canvas.get_renderer())
        width, height = fig.get_size_inches()
        assert 0 <= bounds.x0 and 0 <= bounds.y0 and bounds.x1 <= width and bounds.y1 <= height
        for extension in ("pdf", "svg", "png"):
            fig.savefig(args.out / f"classifier-behaviors-bars.{extension}", dpi=300, facecolor="white")
        plt.close(fig)
    (args.out / "data.csv").write_text(DATA)
    (args.out / "caption.txt").write_text(CAPTION)
    (args.out / "figure.tex").write_text(
        "\\begin{figure*}[t]\n  \\centering\n"
        "  \\includegraphics[width=\\textwidth]{classifier-behaviors-bars.pdf}\n"
        f"  \\caption{{{CAPTION.strip()}}}\n"
        "  \\label{fig:classifier-behaviors-bars}\n\\end{figure*}\n")
    (args.out / "build.json").write_text(json.dumps({
        **validation, "dimensions_inches": [6.75, 3.45], "dpi": 300,
        "matplotlib": matplotlib.__version__,
        "data_sha256": hashlib.sha256(DATA.encode()).hexdigest(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source_script_sha256": hashlib.sha256((ROOT / "scripts/plot_classifier_behaviors.py").read_bytes()).hexdigest(),
        "display_labels": dict(zip(LABELS, DISPLAY_LABELS)),
    }, indent=2) + "\n")
    print(json.dumps(validation, indent=2))
    print(f"Saved 1x4 classifier bar chart to {args.out}")


if __name__ == "__main__":
    main()
