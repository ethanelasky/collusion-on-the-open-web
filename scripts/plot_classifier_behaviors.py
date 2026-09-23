"""Render the frozen September 17 classifier table as a compact paper figure.

uv run --no-project --with matplotlib==3.11.2 python scripts/plot_classifier_behaviors.py
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "docs/figures/classifier-behaviors-2026-09-17"
MANIFEST = ROOT / "data/error-recovery-20260917/recovered-episodes.json"
DATA = """Model,Arm,Ask,Share,Fulfill,Coordinate,Bypass,Sentiment,Peer use
GPT 5.6-Sol,slow,8,18,9,50,2,1,47
GPT 5.6-Sol,working,0,10,4,47,0,0,38
Qwen 3.8 27b,slow,15,34,18,50,4,5,50
Qwen 3.8 27b,working,11,44,29,50,11,16,50
Kimi K3,slow,28,44,27,50,15,18,50
Kimi K3,working,7,33,23,50,5,8,50
DeepSeek V4.1 Flash,slow,44,50,41,50,15,7,50
DeepSeek V4.1 Flash,working,32,50,43,50,16,19,50
"""
MODELS = ["GPT 5.6-Sol", "Qwen 3.8 27b", "Kimi K3", "DeepSeek V4.1 Flash"]
FAMILIES = dict(zip(MODELS, ["GPT", "Qwen", "Kimi", "DeepSeek"]))
LABELS = {
    "Ask": "requests_assistance",
    "Share": "shares_information",
    "Fulfill": "fulfills_request",
    "Coordinate": "coordinates_future_work",
    "Bypass": "shares_bypass_techniques",
    "Sentiment": "expresses_social_sentiment",
    "Peer use": "uses_peer_information",
}
ARMS = {"working": ("#4D78B8", "o", -.13), "slow": ("#E3A04B", "s", .13)}
STYLE = {
    "font.family": "serif",
    "font.serif": ["Times New Roman", "Times", "Nimbus Roman", "DejaVu Serif"],
    "font.size": 8, "axes.titlesize": 9, "axes.labelsize": 9,
    "xtick.labelsize": 8, "ytick.labelsize": 9, "legend.fontsize": 8,
    "text.color": "#222222", "xtick.color": "#444444", "ytick.color": "#333333",
    "pdf.fonttype": 42, "ps.fonttype": 42, "svg.fonttype": "path",
}
CAPTION = (
    "Supported positive classifier judgments by model and condition, out of 50 episodes "
    "per cell (400 total). Blue circles: Working; orange squares: Slow. Gray connectors "
    "link the two arms for each label; vertical offsets keep equal counts visible. "
    "Labels overlap and may reflect expression, attempts, or execution; uncertain "
    "judgments count as zero. Coordinate includes wiki polling; Peer use includes "
    "proposed use of peer information. Bypass can include intended wiki-editing "
    "functionality and does not necessarily indicate an exploit. Sentiment denotes "
    "expressed social sentiment. Qwen pools provider histories; comparisons are "
    "descriptive. The sample includes 31 recovered continuations.\n"
)


def load_counts():
    rows = list(csv.DictReader(io.StringIO(DATA)))
    counts = {(r["Model"], r["Arm"]): {label: int(r[label]) for label in LABELS} for r in rows}
    assert len(counts) == 8
    assert all(0 <= n <= 50 for cell in counts.values() for n in cell.values())
    validation = {"snapshot_cells": 56, "manifest_verified": False}
    if MANIFEST.exists():
        episodes = json.loads(MANIFEST.read_text())
        assert len(episodes) == len({e["source"] for e in episodes}) == 400
        for (model, arm), cell in counts.items():
            group = [e for e in episodes if (e["family"], e["condition"]) == (FAMILIES[model], arm)]
            assert sorted(e["seed"] for e in group) == list(range(50))
            for label, key in LABELS.items():
                assert cell[label] == sum(e["labels"][key] == "present" for e in group), (model, arm, label)
        validation.update(manifest_verified=True, episodes=400,
                          manifest_sha256=hashlib.sha256(MANIFEST.read_bytes()).hexdigest())
    return counts, validation


def make_figure(counts):
    fig, axes = plt.subplots(1, 4, figsize=(6.75, 3.2), sharex=True, sharey=True)
    fig.subplots_adjust(left=.14, right=.985, bottom=.16, top=.82, wspace=.16)
    for ax, model in zip(axes, MODELS):
        for row, label in enumerate(LABELS):
            if row % 2 == 0:
                ax.axhspan(row - .45, row + .45, color="#F5F5F5", linewidth=0, zorder=0)
            x = [counts[model, arm][label] for arm in ARMS]
            y = [row + spec[2] for spec in ARMS.values()]
            ax.plot(x, y, color="#B9BDC3", linewidth=.7, zorder=2)
            for arm, (color, marker, offset) in ARMS.items():
                ax.plot(counts[model, arm][label], row + offset, marker=marker,
                        markersize=4, markerfacecolor=color, markeredgecolor="white",
                        markeredgewidth=.35, linestyle="none", zorder=3)
        ax.set(xlim=(-3, 53), ylim=(6.55, -.55), xticks=[0, 25, 50],
               yticks=range(7), yticklabels=list(LABELS))
        ax.set_title(model, fontweight="bold", pad=8)
        ax.set_axisbelow(True)
        ax.grid(axis="x", color="#E3E3E3", linewidth=.45)
        ax.tick_params(length=0, pad=4)
        for spine in ax.spines.values():
            spine.set_visible(False)
    fig.text(.14, .95, "50 episodes per arm", fontsize=8, color="#555555", va="center")
    handles = [Line2D([], [], marker=marker, markersize=4, color=color, linestyle="none", label=arm.title())
               for arm, (color, marker, _) in ARMS.items()]
    fig.legend(handles=handles, loc="center right", bbox_to_anchor=(.985, .95), ncol=2,
               frameon=False, borderaxespad=0, handlelength=1, handletextpad=.4, columnspacing=1.2)
    fig.text(.56, .037, "Episodes with positive judgment (out of 50)", ha="center", fontsize=9)
    return fig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    counts, validation = load_counts()
    with plt.rc_context(STYLE):
        fig = make_figure(counts)
        fig.canvas.draw()
        bounds = fig.get_tightbbox(fig.canvas.get_renderer())
        width, height = fig.get_size_inches()
        assert bounds.x0 >= 0 and bounds.y0 >= 0 and bounds.x1 <= width and bounds.y1 <= height
        for extension in ("pdf", "svg", "png"):
            fig.savefig(args.out / f"classifier-behaviors.{extension}", dpi=300, facecolor="white")
        plt.close(fig)
    (args.out / "data.csv").write_text(DATA)
    (args.out / "caption.txt").write_text(CAPTION)
    (args.out / "figure.tex").write_text(
        "\\begin{figure*}[t]\n  \\centering\n"
        "  \\includegraphics[width=\\textwidth]{classifier-behaviors.pdf}\n"
        f"  \\caption{{{CAPTION.strip()}}}\n"
        "  \\label{fig:classifier-behaviors}\n\\end{figure*}\n")
    (args.out / "build.json").write_text(json.dumps({
        **validation, "dimensions_inches": [6.75, 3.2], "dpi": 300,
        "matplotlib": matplotlib.__version__, "data_sha256": hashlib.sha256(DATA.encode()).hexdigest(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }, indent=2) + "\n")
    (args.out / "README.md").write_text(
        "# Classifier behavior figure\n\n"
        "Frozen September 17 table: seven labels, four models, two arms, 50 episodes per cell. "
        "Counts are checked against all 400 selected episodes when the local recovered manifest is available. "
        "All values are supported positives; uncertain judgments remain in the denominator.\n\n"
        "PDF and SVG are vector exports; PNG is 300 dpi. The canvas is 6.75 × 3.2 inches. "
        "Use `figure.tex` for a full-width paper figure. `data.csv` preserves the exact counts.\n\n"
        "From the repository root:\n\n```sh\n"
        "uv run --no-project --with matplotlib==3.11.2 python scripts/plot_classifier_behaviors.py\n```\n")
    print(json.dumps(validation, indent=2))
    print(f"Saved classifier figure, data, caption, and LaTeX to {args.out}")


if __name__ == "__main__":
    main()
