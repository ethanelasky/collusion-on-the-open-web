"""Render two self-contained social graphics from the verified classifier counts.

uv run --no-project --with matplotlib==3.11.2 python scripts/plot_classifier_behaviors_social.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle

from scripts.plot_classifier_behaviors import DATA, LABELS, MODELS, load_counts

OUT = ROOT / "docs/figures/classifier-behaviors-social-2026-09-21"
BG = "#F5F3EE"
INK = "#17282C"
MUTED = "#526467"
BLUE = "#2868C7"
ORANGE = "#D96531"
TRACK = "#EAEDEB"
STYLE = {
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "DejaVu Sans"],
    "pdf.fonttype": 42,
    "svg.fonttype": "path",
    "text.color": INK,
}
DISPLAY = {
    "Ask": "Ask for help",
    "Share": "Share information",
    "Fulfill": "Answer peer\nrequests",
    "Coordinate": "Read / coordinate",
    "Bypass": "Share workarounds",
    "Sentiment": "Social expression",
    "Peer use": "Use peer info",
}


def canvas(height):
    fig = plt.figure(figsize=(16, height / 100), dpi=100, facecolor=BG)
    ax = fig.add_axes([0, 0, 1, 1], xlim=(0, 1600), ylim=(height, 0))
    ax.set_axis_off()
    ax._social_text = []
    return fig, ax


def text(ax, x, y, value, size=30, weight="normal", color=INK, va="top", **kwargs):
    artist = ax.text(x, y, value, fontsize=size * .72, fontweight=weight,
                     color=color, va=va, linespacing=1.2, **kwargs)
    ax._social_text.append(artist)
    return artist


def box(ax, x, y, width, height, color="white", radius=22):
    ax.add_patch(FancyBboxPatch((x, y), width, height,
                 boxstyle=f"round,pad=0,rounding_size={radius}",
                 linewidth=0, facecolor=color, zorder=0))


def conditions(ax, y):
    box(ax, 80, y, 1440, 146, "#E8ECE8")
    ax.add_patch(Rectangle((110, y + 27), 12, 42, facecolor=BLUE, linewidth=0))
    text(ax, 140, y + 20, "FASTER LOOKUPS", 25, "bold", BLUE)
    text(ax, 140, y + 53, "Working condition · about 5 seconds", 29)
    ax.add_patch(Rectangle((812, y + 27), 12, 42, facecolor=ORANGE, linewidth=0))
    text(ax, 842, y + 20, "SLOWER LOOKUPS", 25, "bold", ORANGE)
    text(ax, 842, y + 53, "Slow condition · 14 seconds", 29)
    text(ax, 110, y + 103,
         "13-second answer deadline. An API cooldown applied in both conditions.", 27, color=MUTED)


def bar(ax, x, y, value, width, height, color, label_size=28, denominator=False):
    ax.add_patch(Rectangle((x, y), width, height, facecolor=TRACK, linewidth=0))
    if value:
        ax.add_patch(Rectangle((x, y), width * value / 50, height,
                              facecolor=color, linewidth=0))
    # Keep the number in a fixed column: the value remains legible even at zero.
    text(ax, x + width + 18, y + height / 2,
         f"{value}/50" if denominator else str(value), label_size,
         "bold", color=color, va="center")


def lead(counts):
    fig, ax = canvas(1600)
    text(ax, 80, 57, "AI COOPERATION  /  400 SIMULATED RUNS", 26, "bold", MUTED)
    text(ax, 76, 117, "Slower lookups.\nMore help-seeking.", 92, "bold")
    text(ax, 80, 351,
         "Four AI models answered timed data questions, with access to\n"
         "a wiki containing simulated peer messages.", 34)
    conditions(ax, 467)
    text(ax, 80, 657, "Runs with help-seeking", 40, "bold")
    text(ax, 80, 711, "Includes stated plans, attempts and sent requests · 50 runs per bar", 29, color=MUTED)
    row_y = [800, 957, 1114, 1271]
    for model, y in zip(MODELS, row_y):
        name = model.replace(" V4.1 Flash", " V4.1\nFlash")
        text(ax, 80, y + 35, name, 37, "bold", va="center")
        bar(ax, 515, y, counts[model, "working"]["Ask"], 852, 29, BLUE, 32, True)
        bar(ax, 515, y + 44, counts[model, "slow"]["Ask"], 852, 29, ORANGE, 32, True)
    ax.plot([80, 1520], [1410, 1410], color="#CDD4CE", linewidth=1)
    text(ax, 80, 1440, "All four models had more help-seeking labels in the slow condition.", 30, "bold")
    text(ax, 80, 1487, "Descriptive comparison; Qwen combines provider histories. Ambiguous labels excluded.", 25, color=MUTED)
    text(ax, 80, 1540, "50 runs × 4 models × 2 conditions", 25, "bold", MUTED)
    text(ax, 1520, 1540, "AI COLLUSION  /  SEPT 2026", 25, "bold", MUTED, ha="right")
    return fig, ax


def breakdown(counts):
    fig, ax = canvas(2000)
    text(ax, 80, 57, "AI COOPERATION  /  THE FULL BREAKDOWN", 26, "bold", MUTED)
    text(ax, 76, 117, "Same wiki.\nDifferent model behavior.", 82, "bold")
    text(ax, 80, 326,
         "Timed data questions. A wiki with simulated peers.\n"
         "400 runs across four models and two lookup speeds.", 34)
    conditions(ax, 437)
    text(ax, 80, 618, "Runs with each classifier label, out of 50", 34, "bold")
    text(ax, 80, 665, "Counts include stated plans, attempts and actions; categories overlap.", 29, color=MUTED)
    positions = [(80, 735), (820, 735), (80, 1286), (820, 1286)]
    for model, (x, y) in zip(MODELS, positions):
        box(ax, x, y, 700, 521)
        text(ax, x + 27, y + 26, model, 36, "bold")
        for i, label in enumerate(LABELS):
            row = y + 99 + i * 58
            text(ax, x + 27, row + 16, DISPLAY[label], 25, va="center")
            bar(ax, x + 266, row, counts[model, "working"][label], 344, 14, BLUE, 24)
            bar(ax, x + 266, row + 21, counts[model, "slow"][label], 344, 14, ORANGE, 24)
    text(ax, 80, 1844,
         "Read / coordinate includes polling. Workarounds can include allowed wiki edits.\n"
         "Peer use can be proposed; social expression does not establish an internal emotion.",
         25, color=MUTED)
    text(ax, 80, 1928, "Ambiguous labels excluded · Qwen combines provider histories", 25, color=MUTED)
    text(ax, 1520, 1928, "AI COLLUSION  /  SEPT 2026", 25, "bold", MUTED, ha="right")
    return fig, ax


def save(fig, ax, out, name):
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    frame = fig.bbox
    for artist in ax._social_text:
        extent = artist.get_window_extent(renderer)
        assert frame.contains(extent.x0, extent.y0) and frame.contains(extent.x1, extent.y1), artist.get_text()
    dimensions = [round(v * fig.dpi) for v in fig.get_size_inches()]
    for extension in ("png", "svg", "pdf"):
        fig.savefig(out / f"{name}.{extension}", dpi=100, facecolor=BG)
    plt.close(fig)
    return dimensions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    counts, validation = load_counts()
    assert validation["manifest_verified"], "Verify against the final 400-episode source manifest."
    assert all(counts[m, "slow"]["Ask"] > counts[m, "working"]["Ask"] for m in MODELS)
    with plt.rc_context(STYLE):
        outputs = {
            "help-seeking-social": save(*lead(counts), args.out, "help-seeking-social"),
            "behavior-breakdown-social": save(*breakdown(counts), args.out, "behavior-breakdown-social"),
        }
    (args.out / "data.csv").write_text(DATA)
    (args.out / "caption.txt").write_text(
        "Slower lookups. More help-seeking.\n\n"
        "We ran four AI models on timed data questions in a synthetic environment with a wiki "
        "containing simulated peer messages. Each model had 50 runs with faster lookups and 50 "
        "with slower lookups: 400 runs total. The answer deadline was 13 task seconds; lookups "
        "took about 5 task seconds in Working and 14 in Slow. Both conditions had the same API "
        "cooldown. Each episode had its own world; these were not 400 live agents sharing a wiki.\n\n"
        "All four models had more positive help-seeking labels in the slow condition. These "
        "counts include stated plans and attempts as well as sent requests. They do not measure "
        "only executed cooperation, peer readership, or successful help. The second image preserves "
        "all seven categories and all 56 counts from the original classifier chart.\n\n"
        "Labels overlap. Ambiguous candidates were rejected as positives, with all episodes kept "
        "in the denominator. Read / coordinate includes wiki polling and coordination plans. "
        "Workarounds can include intended wiki-editing functionality; the label does not establish "
        "an exploit. Peer use includes proposed use. Social expression describes expressed language, "
        "not an internal emotional state. Qwen combines provider and budget histories; comparisons "
        "are descriptive. The sample includes 31 recovered continuations.\n\n"
        "Report: https://github.com/CanKucukkurt/ai-collusion/blob/data-daily-quota/docs/"
        "cooldown-grid-recovered-classifier-results-2026-09-17.md\n"
        "Transcripts: https://docent.transluce.org/dashboard/b84a5c66-a3d3-42f5-bb82-729ff1932c82\n"
    )
    (args.out / "post-draft.txt").write_text(
        "Slower lookups. More help-seeking.\n\n"
        "We gave 4 AI models timed data questions and a wiki with simulated peers. "
        "All 4 showed more help-seeking in the slow condition.\n\n"
        "400 runs. Counts include plans, attempts and sent requests. Full breakdown in image 2.\n"
    )
    (args.out / "README.md").write_text(
        "# Classifier results for social sharing\n\n"
        "Use the square lead image first and the full breakdown second. Both give the task context "
        "and define the measurement on the image. The original paper figures remain unchanged.\n\n"
        "- [Lead image: help-seeking (1600 × 1600)](help-seeking-social.png) · "
        "[PDF](help-seeking-social.pdf) · [SVG](help-seeking-social.svg)\n"
        "- [All seven categories (1600 × 2000)](behavior-breakdown-social.png) · "
        "[PDF](behavior-breakdown-social.pdf) · [SVG](behavior-breakdown-social.svg)\n"
        "- [Short post draft](post-draft.txt) · [Full caption and limitations](caption.txt) · "
        "[Unchanged counts](data.csv) · [Build verification](build.json)\n\n"
        "Reproduce from the repository root:\n\n```sh\n"
        "uv run --no-project --with matplotlib==3.11.2 python scripts/plot_classifier_behaviors_social.py\n```\n\n"
        "The builder checks all 56 counts against the final 400-episode manifest and verifies "
        "that every text element fits within its canvas. Values use the existing all-stage "
        "classifier table, not the separate execution-only table.\n"
    )
    (args.out / "build.json").write_text(json.dumps({
        **validation, "dimensions_pixels": outputs, "matplotlib": matplotlib.__version__,
        "data_sha256": hashlib.sha256(DATA.encode()).hexdigest(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source_script_sha256": hashlib.sha256((ROOT / "scripts/plot_classifier_behaviors.py").read_bytes()).hexdigest(),
        "display_labels": DISPLAY,
        "headline_verified": "Slow Ask > Working Ask in all four model cells",
        "text_within_canvas": True,
    }, indent=2) + "\n")
    print(json.dumps(validation, indent=2))
    print(f"Saved two social graphics to {args.out}")


if __name__ == "__main__":
    main()
