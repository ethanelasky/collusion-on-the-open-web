"""Plot coordination citations and confirmed wiki additions in the final grid.

Run: uv run --no-project --with matplotlib --with numpy python scripts/plot_coordination_timing.py
Reads the existing 400 sources and judgments; makes no model or Docent calls.
"""
from __future__ import annotations

import csv
import hashlib
import html
import json
import re
import sys
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import PercentFormatter
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ai_collusion.wiki_metrics import wiki_activity, wiki_save_effects

INPUT = ROOT / "data/error-recovery-20260917"
OUT = ROOT / "data/coordination-timing-20260917"
FIGURES = ROOT / "docs/figures/coordination-timing-2026-09-17"
FAMILIES = ["GPT", "Qwen", "Kimi", "DeepSeek"]
NAMES = {"GPT": "GPT 5.6-Sol", "Qwen": "Qwen 3.8 27b", "Kimi": "Kimi K3",
         "DeepSeek": "DeepSeek V4.1 Flash"}
ARMS = ["working", "slow"]
COORDINATION = "coordinates_future_work"
COORDINATION_METRIC = "first_cited_coordination_live_turn"
METRICS = ["first_wiki_post_read_live_turn", "first_post_live_turn"]
READ_MATCH_WORDS = 12
FIRST_EVENT_COLORS = {"working": "#4D78B8", "slow": "#E3A04B"}
FIRST_EVENT_STYLE = {
    "font.family": "serif",
    "font.serif": ["Times New Roman", "Times", "Nimbus Roman", "DejaVu Serif"],
    "font.size": 8,
    "axes.labelsize": 9,
    "axes.titlesize": 9,
    "text.color": "#222222",
    "axes.labelcolor": "#333333",
    "xtick.color": "#444444",
    "ytick.color": "#444444",
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "legend.fontsize": 8,
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
    "svg.fonttype": "path",
}
FIRST_EVENT_CAPTION = (
    "Cumulative share of episodes with (a) first live wiki post read "
    "and (b) first wiki post write, by model and condition. "
    "Blue solid: Working; orange dashed: Slow. Matching counts give episodes "
    "with the event out of all 50 in each cell (400 episodes total). "
    "A read is a live tool response displaying supplied peer-post text, "
    "including direct fetches, populated edit forms, and shell output. "
    "Reads require a normalized 12-word match to a supplied post; cached or "
    "previously seen text can count. Prefill alone, navigation-only results, "
    "and write acknowledgments do not count. Writes are confirmed additions "
    "of new content, excluding no-op saves. Live turns "
    "exclude prefill. Curves retain all episodes in the denominator and describe "
    "recorded events, not survival estimates or imputed behavior after an episode "
    "ends. Episode lengths differ; Qwen pools provider histories.\n"
)
POST_TIMELINE_CAPTION = (
    "Confirmed wiki additions throughout 400 episodes, with 50 episodes per "
    "model and condition: (a) Working (blue), (b) Slow (orange). Each horizontal "
    "gray line spans one recorded episode, ordered by seed; its dot marks the "
    "end. Colored ticks mark live turns with new saved wiki content, including "
    "repeat posts; multiple additions in one turn share a tick. Panel counts "
    "give episodes that post out of 50 and the total live turns with posts. "
    "There are 652 posting turns and 680 saved entries across 194 episodes. "
    "No-op saves are excluded. Live turns exclude prefill; blank space after "
    "an episode ends is unobserved. The 100-live-turn limit is retained. "
    "Saved content does not establish peer readership. Qwen pools provider "
    "histories; comparisons are descriptive.\n"
)


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_csv(path, rows):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def post_words(text):
    """Normalize rendered HTML and raw wiki text without counting URL payloads."""
    text = re.sub(r"\[\[([^]|]+)\|([^]]+)\]\]", r"\2", text)
    text = re.sub(r"\[\[([^]]+)\]\]", r"\1", text)
    parser = _VisibleText()
    parser.feed(text)
    plain = html.unescape(" ".join(parser.parts))
    plain = re.sub(r"https?://\S+", " ", plain)
    return re.findall(r"[a-z0-9]+", plain.lower())


def peer_post_index(record):
    """Index substantive text from explicit peer posts in this run's frozen config.

    Archived from_dump pages are outside this detector. Output matches are
    evidence of displayed text, not evidence of comprehension or a fresh fetch.
    """
    index = {}
    for post in record["resolved_config"]["cut"].get("wiki_inject", []):
        for line in post.get("text", "").splitlines():
            if line.lstrip().startswith(("=", "To edit this page", "Edit text of this page")):
                continue
            words = post_words(line)
            for start in range(len(words) - READ_MATCH_WORDS + 1):
                key = tuple(words[start:start + READ_MATCH_WORDS])
                index.setdefault(key, {"page": post["page"], "author": post.get("label", ""),
                                       "post_time": post.get("time", ""),
                                       "matched_words": " ".join(key)})
    return index


def wiki_post_read_evidence(turn, index):
    if turn.get("source") not in {"wiki", "wiki-form", "env-model"}:
        return None
    if (turn.get("call") or {}).get("tool") not in {"web_fetch", "shell"}:
        return None
    result = turn.get("result") or ""
    # RESULT's first line echoes the command, including any quoted post text.
    # Only the returned body is evidence of a read.
    body = result.split("\n", 1)[-1] if result.startswith("RESULT ") else result
    words = post_words(body)
    for start in range(len(words) - READ_MATCH_WORDS + 1):
        key = tuple(words[start:start + READ_MATCH_WORDS])
        if key in index:
            return {**index[key], "live_turn": turn["turn"], "tool": turn["call"]["tool"],
                    "result_source": turn["source"],
                    "result_sha256": hashlib.sha256(result.encode()).hexdigest()}
    return None


def extract():
    manifest = read(INPUT / "recovered-episodes.json")
    evidence = {e["source"]: e for e in read(INPUT / "recovered-evidence.json")}
    publication = read(ROOT / "data/docent-grid-20260917/publication.json")
    assert publication["status"] == "complete"
    assert publication["input_manifest_sha256"] == digest(INPUT / "recovered-episodes.json")
    remote = {e["source"]: e for e in publication["sources"].values()}
    assert len(manifest) == len({r["source"] for r in manifest}) == 400
    episodes, post_events, citations, read_events = [], [], [], []
    quote_count = empty_saves = 0
    for row in manifest:
        source = ROOT / row["source"]
        judgment_path = ROOT / row["judgment_path"]
        assert digest(source) == row["sha256"]
        assert digest(judgment_path) == row["judgment_sha256"]
        record = read(source)
        judgment = read(judgment_path)["judgment"]
        events = evidence[row["source"]]["evidence"]
        assert events == judgment["evidence"] and judgment["labels"] == row["labels"]
        turns = {t["turn"]: t for t in record["episode"]["turns"]}
        assert sorted(turns) == list(range(1, row["n_turns"] + 1))
        assert row["n_turns"] <= 100
        post_index = peer_post_index(record)
        reads = [match for t in turns.values()
                 if (match := wiki_post_read_evidence(t, post_index)) is not None]
        read_events.extend({"source": row["source"], "family": row["family"],
                            "condition": row["condition"], "seed": row["seed"], **match}
                           for match in reads)
        activity = wiki_activity(record)
        assert activity["posts"] == row["posts"]
        assert activity["write_turns"] == row["write_turns"]
        ledger = Counter(p for entries in record["episode"]["wiki_posts"].values() for p in entries)
        own_citation_turns = set()
        for event_index, event in enumerate(events):
            for q in event["quotes"]:
                turn = turns[q["turn"]]
                value = turn["result"] if q["field"] == "result" else turn["response"].get(q["field"])
                assert q["quote"] in (value or ""), (row["source"], q)
                quote_count += 1
                if COORDINATION in event["labels"] and q["field"] in {"text", "reasoning"}:
                    own_citation_turns.add(q["turn"])
                    citations.append({"source": row["source"], "event_index": event_index,
                                      "stage": event["stage"], "live_turn": q["turn"],
                                      "field": q["field"], "quote": q["quote"]})
        assert bool(own_citation_turns) == (row["labels"][COORDINATION] == "present")
        post_turns, added_entries = [], 0
        for number in row["write_turns"]:
            turn = turns[number]
            effects = wiki_save_effects(turn)
            if effects:
                additions = [e["added"] for e in effects if e.get("added", "").strip()]
                empty_saves += len(effects) - len(additions)
                assert not (Counter(additions) - ledger)
                ledger.subtract(additions)
                count = len(additions)
            else:
                assert turn["source"] == "wiki-save"
                match = re.search(r"saved\. (\d+) new line\(s\)\.", turn["result"])
                assert match, (row["source"], number)
                count = int(int(match.group(1)) > 0)
                empty_saves += not count
            if count:
                post_turns.append(number)
                added_entries += count
                post_events.append({"source": row["source"], "family": row["family"],
                                    "condition": row["condition"], "seed": row["seed"],
                                    "live_turn": number, "new_entries": count,
                                    "confirmation": "wiki_save effect" if effects else "wiki-save result"})
        assert added_entries == row["posts"], row["source"]
        assert bool(post_turns) == bool(row["posts"])
        first_post = min(post_turns, default=None)
        # No empty save precedes the first genuine addition in this sample.
        assert first_post == min(row["write_turns"], default=None)
        uploaded = remote[row["source"]]
        assert uploaded["status"] == "verified" and uploaded["source_sha256"] == row["sha256"]
        episodes.append({**{k: row[k] for k in ["source", "sha256", "judgment_path", "judgment_sha256",
                                               "family", "condition", "provider", "seed", "n_turns",
                                               "end_reason", "recovered", "posts"]},
                         "model": NAMES[row["family"]],
                         "first_wiki_post_read_live_turn": min((r["live_turn"] for r in reads), default=None),
                         "wiki_post_read_live_turns": [r["live_turn"] for r in reads],
                         "first_cited_coordination_live_turn": min(own_citation_turns, default=None),
                         "cited_coordination_live_turns": sorted(own_citation_turns),
                         "first_post_live_turn": first_post, "post_live_turns": post_turns,
                         "docent_url": uploaded["url"]})
    for family in FAMILIES:
        for arm in ARMS:
            cell = [r for r in episodes if (r["family"], r["condition"]) == (family, arm)]
            assert sorted(r["seed"] for r in cell) == list(range(50))
    assert sum(r["first_post_live_turn"] is not None for r in episodes) == 194
    assert sum(r["posts"] for r in episodes) == 680
    return episodes, post_events, citations, read_events, {"source_hashes_verified": 400,
        "judgment_hashes_verified": 400, "exact_evidence_quotes_verified": quote_count,
        "empty_save_operations_excluded": int(empty_saves)}


def summarize(episodes):
    groups, curves = [], []
    for family in FAMILIES:
        for arm in ARMS:
            cell = [r for r in episodes if (r["family"], r["condition"]) == (family, arm)]
            group = {"family": family, "model": NAMES[family], "condition": arm, "n": len(cell),
                     "median_episode_live_turns": float(np.median([r["n_turns"] for r in cell])),
                     "post_live_turns": sum(len(r["post_live_turns"]) for r in cell),
                     "post_entries": sum(r["posts"] for r in cell)}
            for metric in [*METRICS, COORDINATION_METRIC]:
                values = np.array([r[metric] for r in cell if r[metric] is not None])
                quartiles = np.quantile(values, [.25, .5, .75]).tolist() if len(values) else [None] * 3
                group[metric] = {"observed": len(values), "not_observed": len(cell) - len(values),
                                 "q25": quartiles[0], "median": quartiles[1], "q75": quartiles[2],
                                 "by_live_turn_10": int((values <= 10).sum()),
                                 "by_live_turn_25": int((values <= 25).sum()),
                                 "by_live_turn_50": int((values <= 50).sum())}
                for live_turn in range(101):
                    count = int((values <= live_turn).sum())
                    curves.append({"family": family, "condition": arm, "metric": metric,
                                   "live_turn": live_turn, "episodes_with_event": count,
                                   "denominator": len(cell), "percent": 100 * count / len(cell),
                                   "episodes_observed_at_this_live_turn": sum(r["n_turns"] >= live_turn for r in cell)})
            groups.append(group)
    return groups, curves


def style():
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "axes.spines.left": False, "axes.spines.bottom": False,
                         "axes.labelcolor": "#344054", "text.color": "#182230",
                         "xtick.color": "#667085", "ytick.color": "#667085",
                         "svg.fonttype": "none", "pdf.fonttype": 42})


def save(fig, stem, *, fixed_canvas=False):
    if fixed_canvas:
        fig.canvas.draw()
        bounds = fig.get_tightbbox(fig.canvas.get_renderer())
        width, height = fig.get_size_inches()
        if bounds.x0 < 0 or bounds.y0 < 0 or bounds.x1 > width or bounds.y1 > height:
            raise ValueError(f"{stem}: labels extend outside the figure canvas")
    for extension in ["png", "svg", "pdf"]:
        fig.savefig(FIGURES / f"{stem}.{extension}", dpi=300 if fixed_canvas else 190,
                    facecolor="white", bbox_inches=None if fixed_canvas else "tight")
    plt.close(fig)


def plot_first_events(groups, curves):
    with plt.rc_context(FIRST_EVENT_STYLE):
        fig, axes = plt.subplots(2, 4, figsize=(6.75, 3.2), sharex=True, sharey=True)
        fig.subplots_adjust(left=.095, right=.985, top=.80, bottom=.13,
                            hspace=.47, wspace=.16)
        for col, family in enumerate(FAMILIES):
            for row_index, metric in enumerate(METRICS):
                ax = axes[row_index, col]
                ax.set_axisbelow(True)
                ax.grid(axis="y", color="#E6E6E6", linewidth=.45)
                ax.set(xlim=(0, 100), ylim=(0, 104), xticks=[0, 25, 50, 75, 100],
                       yticks=[0, 25, 50, 75, 100])
                ax.yaxis.set_major_formatter(PercentFormatter(100, decimals=0))
                ax.tick_params(length=0, pad=3)
                for spine in ax.spines.values():
                    spine.set_visible(False)
                for arm in ARMS:
                    points = [c for c in curves if (c["family"], c["condition"], c["metric"]) == (family, arm, metric)]
                    ax.step([c["live_turn"] for c in points], [c["percent"] for c in points],
                            where="post", color=FIRST_EVENT_COLORS[arm], linewidth=1.25,
                            linestyle="-" if arm == "working" else (0, (3, 2)))
                    group = next(g for g in groups if (g["family"], g["condition"]) == (family, arm))
                    # Lower right is clear in row A; upper left is clear in row B.
                    x, y = ((.69, .33) if row_index == 0 else (.07, .92))
                    y -= .15 * ARMS.index(arm)
                    ax.text(x, y, f"{group[metric]['observed']}/{group['n']}",
                            transform=ax.transAxes, color=FIRST_EVENT_COLORS[arm],
                            ha="left", va="top", fontsize=8,
                            bbox={"facecolor": "white", "edgecolor": "none", "pad": .6})
                if row_index == 0:
                    ax.set_title(NAMES[family], fontweight="bold", pad=5)
        fig.text(.095, .947, "(a) First live wiki post read", fontsize=9, weight="bold", va="center")
        fig.text(.095, .465, "(b) First wiki post write", fontsize=9, weight="bold", va="center")
        fig.text(.016, .465, "Cumulative share of episodes", rotation=90,
                 fontsize=9, ha="center", va="center")
        fig.text(.54, .028, "Live turn", fontsize=9, ha="center", va="bottom")
        legend = [Line2D([0], [0], color=FIRST_EVENT_COLORS[a], lw=1.25,
                         linestyle="-" if a == "working" else (0, (3, 2)), label=a.title())
                  for a in ARMS]
        fig.legend(handles=legend, loc="center right", bbox_to_anchor=(.985, .947),
                   ncol=2, frameon=False, borderaxespad=0, handlelength=2,
                   handletextpad=.5, columnspacing=1.2)
        save(fig, "first-events", fixed_canvas=True)
    (FIGURES / "first-events-caption.txt").write_text(FIRST_EVENT_CAPTION)
    (FIGURES / "first-events.tex").write_text(
        "\\begin{figure*}[t]\n  \\centering\n"
        "  \\includegraphics[width=\\textwidth]{first-events.pdf}\n"
        f"  \\caption{{{FIRST_EVENT_CAPTION.strip()}}}\n"
        "  \\label{fig:coordination-timing}\n\\end{figure*}\n")


def plot_post_timelines(episodes):
    with plt.rc_context(FIRST_EVENT_STYLE):
        fig, axes = plt.subplots(2, 4, figsize=(6.75, 4.6), sharex=True, sharey=True)
        fig.subplots_adjust(left=.095, right=.985, top=.82, bottom=.10,
                            hspace=.35, wspace=.16)
        for col, family in enumerate(FAMILIES):
            for row_index, arm in enumerate(ARMS):
                ax = axes[row_index, col]
                cell = sorted((r for r in episodes if (r["family"], r["condition"]) == (family, arm)), key=lambda r: r["seed"])
                for item in cell:
                    seed = item["seed"]
                    ax.hlines(seed, 1, item["n_turns"], color="#D5D8DD", linewidth=.4, zorder=1)
                    if item["post_live_turns"]:
                        ax.vlines(item["post_live_turns"], seed - .40, seed + .40,
                                  color=FIRST_EVENT_COLORS[arm], linewidth=1.05, zorder=3)
                    ax.plot(item["n_turns"], seed, "o", color="#A0A6AE",
                            markersize=1.15, markeredgewidth=0, zorder=2)
                n_posts = sum(r["first_post_live_turn"] is not None for r in cell)
                n_events = sum(len(r["post_live_turns"]) for r in cell)
                ax.set_title(f"{n_posts}/50 post · {n_events} turns", fontsize=8, pad=5)
                ax.set(xlim=(0, 101), ylim=(50, -1), xticks=[0, 25, 50, 75, 100],
                       yticks=[0, 10, 20, 30, 40, 49])
                ax.tick_params(length=0, pad=3)
                ax.set_axisbelow(True)
                ax.grid(axis="x", color="#EEEEEE", linewidth=.4)
                for spine in ax.spines.values():
                    spine.set_visible(False)
                if row_index == 0:
                    ax.text(.5, 1.17, NAMES[family], transform=ax.transAxes,
                            ha="center", fontsize=9, weight="bold")
        fig.text(.095, .957, "(a) Working", fontsize=9, weight="bold",
                 color=FIRST_EVENT_COLORS["working"], va="center")
        fig.text(.095, .473, "(b) Slow", fontsize=9, weight="bold",
                 color=FIRST_EVENT_COLORS["slow"], va="center")
        fig.text(.032, .46, "Episode seed", rotation=90, fontsize=9,
                 ha="center", va="center")
        fig.text(.54, .022, "Live turn", fontsize=9, ha="center", va="bottom")
        handles = [
            Line2D([0], [0], color="#D5D8DD", lw=.8, marker="o", markersize=2,
                   markerfacecolor="#A0A6AE", markeredgewidth=0, label="Recorded episode / end"),
            Line2D([0], [0], color="#555555", linestyle="none", marker="|",
                   markersize=5, markeredgewidth=.9, label="New wiki content"),
        ]
        fig.legend(handles=handles, loc="center right", bbox_to_anchor=(.985, .957),
                   ncol=2, frameon=False, borderaxespad=0, handlelength=1.8,
                   handletextpad=.5, columnspacing=1.2)
        save(fig, "post-timelines", fixed_canvas=True)
    (FIGURES / "post-timelines-caption.txt").write_text(POST_TIMELINE_CAPTION)
    (FIGURES / "post-timelines.tex").write_text(
        "\\begin{figure*}[t]\n  \\centering\n"
        "  \\includegraphics[width=\\textwidth]{post-timelines.pdf}\n"
        f"  \\caption{{{POST_TIMELINE_CAPTION.strip()}}}\n"
        "  \\label{fig:wiki-post-timelines}\n\\end{figure*}\n")


def fmt(value):
    return f"{value:g}" if value is not None else "—"


def write_report(groups, episodes, post_events, validation):
    table_rows = []
    for group in groups:
        coord, posts = (group[m] for m in METRICS)
        table_rows.append(f"| {group['model']} | {group['condition']} | {coord['observed']}/50 | {fmt(coord['median'])} | "
                          f"{posts['observed']}/50 | {fmt(posts['median'])} ({fmt(posts['q25'])}–{fmt(posts['q75'])}) | {posts['by_live_turn_25']}/50 |")
    overview = '\n'.join(table_rows)
    totals = Counter(r["end_reason"] for r in episodes)
    read_count = sum(r[METRICS[0]] is not None for r in episodes)
    report = f"""# When models read and write wiki posts

**September 17, 2026 · The final 400-episode grid, including 31 recovered continuations.**

**{read_count}/400 episodes have a confirmed live wiki post read**, defined by supplied peer-post text appearing in a live tool response. The figure measures when post content was returned to the model, including direct fetches, populated edit forms, and shell output. Prefilled context alone does not count. “Live” refers to the tool response, not the age of the post; rereads and displayed cached text count.

Actual posting usually comes later. Among episodes that post, the median first post is between **live turns 12 and 29.5**, depending on model and condition. DeepSeek posts in 85/100 episodes, Kimi in 51/100, Qwen in 45/100, and GPT in 13/100. GPT's timing medians describe a small minority of its episodes.

## First live wiki post read and first wiki post write

![Cumulative timing of first live wiki post read and first wiki post write, by model and condition](figures/coordination-timing-2026-09-17/first-events.png)

[SVG](figures/coordination-timing-2026-09-17/first-events.svg) · [PDF](figures/coordination-timing-2026-09-17/first-events.pdf)

The paper figure uses a fixed 6.75 × 3.2-inch canvas, embedded Times fonts, and a 300-dpi PNG export. [Caption](figures/coordination-timing-2026-09-17/first-events-caption.txt) · [LaTeX figure](figures/coordination-timing-2026-09-17/first-events.tex). Colored counts report the final number of episodes with each event; blue solid curves indicate Working and orange dashed curves indicate Slow.

Each curve's denominator is **all 50 episodes in that model/condition cell**, including episodes without the event. A point at 20% means 10 of the 50 episodes have recorded the event by that live turn. Curves are descriptive cumulative counts, not survival estimates; no behavior is imputed beyond the saved episode.

| Model | Condition | Episodes reading a post | Median first read live turn | Episodes posting | Median first post live turn (IQR) | Posted by live turn 25 |
| --- | --- | --- | --- | --- | --- | --- |
{overview}

**Medians and interquartile ranges (IQRs) include only episodes with the event.** IQR is the middle half of observed first-post live turns. A median of 7.5 lies between two integer live turns; it is not an event at a fractional live turn. The working condition has earlier median first posts than slow for Qwen, Kimi, and DeepSeek; GPT has only four working-condition posters and nine slow-condition posters.

## Posting throughout the episode

![Episode timelines showing every live turn with a confirmed addition, with separate panels for model and condition](figures/coordination-timing-2026-09-17/post-timelines.png)

[SVG](figures/coordination-timing-2026-09-17/post-timelines.svg) · [PDF](figures/coordination-timing-2026-09-17/post-timelines.pdf)

The matching paper figure uses a fixed 6.75 × 4.6-inch canvas to preserve separation between the 50 episode rows, embedded Times fonts, and a 300-dpi PNG export. [Caption](figures/coordination-timing-2026-09-17/post-timelines-caption.txt) · [LaTeX figure](figures/coordination-timing-2026-09-17/post-timelines.tex). Panel counts show episodes that post and the total live turns with posts.

Each row is one episode, ordered by seed 0–49 within each panel. Gray lines show the recorded live turns and dots mark the episode's end. Colored marks show **all {len(post_events)} live turns with new saved wiki content**, including repeat posts. Those live turns produced 680 saved entries across 194 episodes; multiple entries can be saved in one live turn. Blank space after an episode ends is unobserved. This makes differing observation lengths visible alongside posting times.

## What is being timed

1. **First live wiki post read:** the earliest live tool response containing at least {READ_MATCH_WORDS} consecutive normalized words from an explicit supplied peer post in that run's frozen `wiki_inject` configuration. HTML tags, wiki-link formatting, punctuation, and case are normalized; URLs, command echoes, headings, and generic edit instructions do not establish a match. Direct fetches, populated edit forms, and shell output can qualify. A fetch to a local file with no returned post text does not qualify until the content is displayed. Prefill, reasoning about the wiki, navigation-only responses, write acknowledgments, and the model's own new text are not evidence of a peer-post read. This is a conservative measure of displayed post content, not comprehension: shorter excerpts and posts available only through archived `from_dump` fixtures are outside the detector. Each counted response has a matched text fragment, page, author, turn, and result hash in the read-evidence export.
2. **First post and repeat posts:** all live turns where the environment records a wiki save that adds nonempty content. Shell saves use the `wiki_save` effect's `added` text; direct saves use the result's new-line count. Per-episode addition counts agree with the saved-post ledger. We excluded {validation['empty_save_operations_excluded']} save operations that added no content. The first nonempty addition equals the first recorded save in every posting episode in this sample.

Posting is a concrete observable action but does not, by itself, establish cooperative intent or that another agent read the message. The classifier's executed request/sharing/fulfillment union covers 193 episodes, while the saved-content measure covers 194. These outcomes are kept distinct.

All numbers are **live turns after prefilled context**, preserving the original numbering across recovered continuations. A live turn is an episode-loop step; a shell call can perform several operations. Equal live turn counts therefore need not mean equal simulated time, real time, or work across models. {totals['all_rounds_resolved']} episodes end with all rounds resolved; {totals['max_turns']} reach the natural 100-live-turn cap. All remain in the plots. Resolved does not mean correct.

Qwen pools OpenRouter, Alibaba, and mixed-provider continuation histories, with different reasoning budgets and completion-based selection. Model/condition differences are descriptive and do not isolate provider effects. No new classifications, model calls, or environment changes were needed.

### Earlier coordination-citation figure

The [prior figure and caption](figures/coordination-timing-2026-09-17/prior-cited-coordination/first-events.pdf) and its curve values are preserved in `prior-cited-coordination/`. That plot used the earliest classifier-selected coordination citation, which could concern planning or polling. The current read curve is recomputed from returned post content; it is not a relabeling of those citations. The historical `first_cited_coordination_live_turn` metric and citation export remain available in the analysis data.

## Data and reproduction

- [400 episode timing records with individual Docent links](../data/coordination-timing-20260917/episodes.csv)
- [All confirmed posting live turns](../data/coordination-timing-20260917/post-events.csv)
- [Matched live wiki post read evidence](../data/coordination-timing-20260917/read-events.csv)
- [Coordination citations with exact quotes](../data/coordination-timing-20260917/coordination-citations.json)
- [Cumulative curve values](../data/coordination-timing-20260917/curves.csv)
- [Analysis metadata and verification](../data/coordination-timing-20260917/analysis.json)
- [Plot/report generator](../scripts/plot_coordination_timing.py)

The generator rechecks all 400 source and judgment hashes, all {validation['exact_evidence_quotes_verified']:,} classifier quotations, exact posting counts, seed coverage, and Docent source mappings. Figures are exported as PNG, SVG, and PDF. Reproduce with:

```sh
uv run --no-project --with matplotlib --with numpy python scripts/plot_coordination_timing.py
```

See the [classifier report](cooldown-grid-recovered-classifier-results-2026-09-17.md), [behavior exemplars](behavior-exemplars-2026-09-17.md), and [all 400 transcripts in Docent](https://docent.transluce.org/dashboard/b84a5c66-a3d3-42f5-bb82-729ff1932c82).
"""
    (ROOT / "docs/coordination-timing-2026-09-17.md").write_text(report)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    FIGURES.mkdir(parents=True, exist_ok=True)
    episodes, posts, citations, reads, validation = extract()
    groups, curves = summarize(episodes)
    for name, value in [("episodes", episodes), ("coordination-citations", citations)]:
        (OUT / f"{name}.json").write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    write_csv(OUT / "episodes.csv", [{k: json.dumps(v) if isinstance(v, list) else v for k, v in r.items()} for r in episodes])
    write_csv(OUT / "post-events.csv", posts)
    write_csv(OUT / "read-events.csv", reads)
    write_csv(OUT / "curves.csv", curves)
    analysis = {"n": len(episodes), "groups": groups, "validation": validation,
                "confirmed_post_live_turns": len(posts), "saved_entries": sum(r["posts"] for r in episodes),
                "episodes_with_coordination_citations": sum(r[COORDINATION_METRIC] is not None for r in episodes),
                "episodes_with_live_post_reads": sum(r[METRICS[0]] is not None for r in episodes),
                "live_post_read_responses": len(reads),
                "read_definition": "Live returned text matching 12 consecutive normalized words from explicit supplied peer posts; prefill and command echoes excluded",
                "episodes_posting": sum(r[METRICS[1]] is not None for r in episodes),
                "inputs": {name: digest(INPUT / name) for name in ["recovered-episodes.json", "recovered-evidence.json"]},
                "script_sha256": digest(Path(__file__)),
                "packages": {"matplotlib": matplotlib.__version__, "numpy": np.__version__}}
    (OUT / "analysis.json").write_text(json.dumps(analysis, indent=2, allow_nan=False) + "\n")
    style()
    plot_first_events(groups, curves)
    plot_post_timelines(episodes)
    write_report(groups, episodes, posts, validation)
    print(json.dumps({k: v for k, v in analysis.items() if k not in {"groups", "inputs", "packages"}}, indent=2))
    print("Wrote docs/coordination-timing-2026-09-17.md and six PNG/SVG/PDF figure files.")


if __name__ == "__main__":
    main()
