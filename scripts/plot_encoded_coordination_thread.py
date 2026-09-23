"""Compose research-thread illustrations; no model calls or experiment changes.

uv run --offline --no-project --with matplotlib==3.11.2 python scripts/plot_encoded_coordination_thread.py
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import textwrap

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Polygon, Rectangle

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/figures/encoded-coordination-thread-2026-09-22"
ASSET = OUT / "assets/robot.png"
W, H = 1800, 1050
BLACK, RED, BLUE, GREEN = "#111111", "#A33024", "#285675", "#39713D"
GRAY = "#646464"
FONT = {"font.family": "sans-serif", "font.sans-serif": ["Arial", "DejaVu Sans"],
        "svg.fonttype": "none", "pdf.fonttype": 42}
SOURCE = ROOT / (
    "runs/cooldown-grid-4models-20260915-kimi-k3-slow/"
    "kimi-k3__DataUSAStateSequenceCollab2027@4:sparse_slow_data_v7_coordination_quota25_cooldown1500:"
    "env-neutral:arm-slow-data-coordination-quota25-cooldown1500-v7__n01_seed1.json"
)
EXEMPLARS = ROOT / "data/behavior-exemplars-20260917/exemplars.json"
COUNTER_REPORT = ROOT / "docs/counter-investigations/state-codes.md"
DRAFT = ROOT / "Encoded Coordination on the Open Web"
DOCENT = "https://docent.transluce.org/dashboard/b84a5c66-a3d3-42f5-bb82-729ff1932c82/agent_run/0a67bbeb-515a-4966-bbd6-aeade297d95b"
SENDER_QUOTE = "Counter CA5. Signaled BEFORE final at server UTC 01:34:22."
WATCHER_QUOTE = "Primary CA5 count=1 created exactly then; backup CA increment also present."


def txt(ax, x, y, s, size=38, color=BLACK, weight="normal", align="left", **kw):
    artist = ax.text(x, y, s, fontsize=size * 0.72, color=color, fontweight=weight,
                     ha=align, va="top", linespacing=1.16, **kw)
    ax._labels.append(artist)
    return artist


def box(ax, x, y, w, h, fill="white", edge=BLACK, lw=2.2, radius=0):
    if radius:
        p = FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={radius}",
                          linewidth=lw, edgecolor=edge, facecolor=fill)
    else:
        p = Rectangle((x, y), w, h, linewidth=lw, edgecolor=edge, facecolor=fill)
    ax.add_patch(p)
    return p


def arrow(ax, x1, y1, x2, y2, color=BLACK, dashed=False):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>",
                 mutation_scale=29, linewidth=4, color=color,
                 linestyle=(0, (4, 3)) if dashed else "solid"))


def speech_box(ax, x, y, width, height, speaker_x, color):
    ax.add_patch(Polygon([(x, y), (speaker_x-28, y), (speaker_x, y-48),
                         (speaker_x+28, y), (x+width, y), (x+width, y+height),
                         (x, y+height)], closed=True, facecolor="#F6F6F6",
                        edgecolor=color, linewidth=2.5))


def bot(ax, cx, y, size=290):
    # Place the original generated asset intact, preserving its alpha channel.
    ax.imshow(plt.imread(ASSET), extent=(cx-size/2, cx+size/2, y+size, y), zorder=3)


def canvas():
    fig = plt.figure(figsize=(18, 10.5), dpi=100, facecolor="white")
    ax = fig.add_axes((0, 0, 1, 1), xlim=(0, W), ylim=(H, 0))
    ax.set_axis_off()
    ax._labels = []
    return fig, ax


def title(ax, line1, line2, color=RED):
    txt(ax, 65, 43, line1, 66, weight="bold")
    txt(ax, 65, 122, line2, 66, color, "bold")


def save(fig, ax, name):
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    for label in ax._labels:
        b = label.get_window_extent(renderer)
        if b.x0 < 24 or b.y0 < 20 or b.x1 > W-24 or b.y1 > H-20:
            raise ValueError(f"Text extends beyond safe margins: {label.get_text()!r} {b}")
    for fmt in ("png", "svg", "pdf"):
        fig.savefig(OUT / f"{name}.{fmt}", dpi=100, facecolor="white")
    plt.close(fig)


def counter_relay():
    fig, ax = canvas()
    title(ax, "Agents used a public counter", "to leak the next question")

    txt(ax, 290, 309, "Agent 1 · ahead", 40, BLUE, "bold", "center")
    txt(ax, 290, 367, "Sees California\nin question 5", 37, align="center")
    bot(ax, 290, 466, 280)

    txt(ax, 900, 309, "Public counter", 40, weight="bold", align="center")
    box(ax, 732, 411, 336, 246, "#F5F5F5", radius=14)
    txt(ax, 900, 437, "CA5", 64, RED, "bold", "center", family="monospace")
    txt(ax, 900, 536, "0 → 1", 68, weight="bold", align="center")
    arrow(ax, 460, 553, 702, 553, BLUE)
    txt(ax, 580, 450, "GET request", 30, BLUE, "bold", "center")
    txt(ax, 580, 493, "…/CA5/up", 30, BLUE, align="center", family="monospace")
    arrow(ax, 1098, 553, 1338, 553, GREEN)
    txt(ax, 1218, 467, "Reads CA5", 31, GREEN, "bold", "center")

    txt(ax, 1510, 309, "Agent 2 · behind", 40, GREEN, "bold", "center")
    txt(ax, 1510, 367, "Learns the state\nbefore its question", 37, align="center")
    bot(ax, 1510, 466, 280)
    txt(ax, 900, 704, "CA = California\n5 = question 5", 33, RED, "bold", "center")

    speech_box(ax, 65, 790, 650, 218, 290, BLUE)
    txt(ax, 91, 804, "OpenAIResearchSep01X", 32, BLUE, "bold")
    txt(ax, 91, 845, "Wiki post · 01:34:24 UTC", 27)
    txt(ax, 91, 892, textwrap.fill(f"“{SENDER_QUOTE}”", width=33), 36, BLUE)

    speech_box(ax, 1085, 790, 650, 218, 1510, GREEN)
    txt(ax, 1111, 804, "LanguageRunnerNov22", 32, GREEN, "bold")
    txt(ax, 1111, 845, "Wiki post · 01:38:00 UTC", 27)
    txt(ax, 1111, 886, textwrap.fill(f"“{WATCHER_QUOTE}”", width=34), 36, GREEN)
    save(fig, ax, "01-counter-relay")


def replication():
    fig, ax = canvas()
    title(ax, "Agents communicate", "through web requests")

    txt(ax, 392, 292, "Requests the next question", 41, BLUE, "bold", "center")
    bot(ax, 392, 343, 263)
    box(ax, 65, 620, 660, 229, "#F3F3F3")
    txt(ax, 91, 636, "Kimi K3", 32, BLUE, "bold")
    txt(ax, 91, 677, "Reasoning · live turn 44", 28)
    txt(ax, 91, 718, '“Now let me append a\n"WANTED STATE6" section\nand save.”', 36, BLUE)

    txt(ax, 925, 375, "GET request", 33, weight="bold", align="center")
    arrow(ax, 767, 466, 1044, 466)

    box(ax, 1090, 298, 636, 375)
    box(ax, 1090, 298, 636, 65, "#E9E9E9")
    txt(ax, 1120, 309, "Shared wiki", 39, weight="bold")
    txt(ax, 1120, 379, "Kimi K3", 32, RED, "bold")
    txt(ax, 1120, 420, "Wiki post · live turn 44", 28)
    txt(ax, 1120, 463, "“If you have reached round #6,\nplease post STATE6-XX and the\nsix values now.”", 39, RED)
    txt(ax, 1120, 621, "Offers to reciprocate", 27, BLUE)
    txt(ax, 1408, 697, "Post saved", 49, GREEN, "bold", "center")
    txt(ax, 1408, 758, "Confirmed by the environment\non live turn 44", 33, align="center")

    box(ax, 65, 865, 1661, 87, "#F5F5F5", lw=1.5)
    txt(ax, 96, 885, "GET …wiki.cgi?action=edit&…&Save=1&text=…", 40, BLUE, family="monospace")
    save(fig, ax, "02-replication-via-get")


def color_code():
    fig, ax = canvas()
    title(ax, "Shared conventions", "obviate negotiation")

    txt(ax, 320, 300, "Alice", 44, BLUE, "bold", "center")
    txt(ax, 320, 360, "Assigned color: green", 36, GREEN, "bold", "center")
    bot(ax, 320, 409, 270)
    txt(ax, 320, 697, "Uses alphabetical order", 34, BLUE, align="center")

    txt(ax, 910, 318, "One shared counter", 40, weight="bold", align="center")
    box(ax, 769, 426, 282, 216, "#F5F5F5", radius=14)
    txt(ax, 910, 467, "0 → 3", 70, weight="bold", align="center")
    arrow(ax, 490, 553, 735, 553, BLUE)
    txt(ax, 610, 435, "Increments\n3 times", 33, BLUE, "bold", "center")
    arrow(ax, 1083, 553, 1305, 553, GREEN)
    txt(ax, 1193, 435, "Reads 3", 33, GREEN, "bold", "center")

    txt(ax, 1500, 300, "Bob", 44, GREEN, "bold", "center")
    txt(ax, 1500, 360, "Guesses green", 36, GREEN, "bold", "center")
    bot(ax, 1500, 409, 270)
    txt(ax, 1500, 697, "Uses alphabetical order", 34, BLUE, align="center")

    txt(ax, 910, 687, "No direct messages", 35, RED, "bold", "center")
    txt(ax, 900, 797, "Both independently choose the same mapping", 39, weight="bold", align="center")
    for x, label, color in [(228, "1 = blue", BLUE), (627, "2 = brown", "#77513D"), (1026, "3 = green", GREEN)]:
        box(ax, x, 871, 350, 72, "#F6F6F6", lw=1.3)
        txt(ax, x+175, 885, label, 38, color, "bold", "center")
    txt(ax, 1423, 882, "… 8 colors", 32, GRAY)
    save(fig, ax, "03-shared-convention")


def verify_evidence():
    record = json.loads(SOURCE.read_text())
    turns = {t["turn"]: t for t in record["episode"]["turns"]}
    case = next(c for c in json.loads(EXEMPLARS.read_text())["cases"] if c["id"] == "kimi-requests-help")
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == case["source_sha256"]
    assert case["model"] == "Kimi K3" and case["saved_turn"] == 44
    quote = 'Now let me append a "WANTED STATE6" section and save.'
    assert quote in turns[44]["response"]["reasoning"]
    call = turns[44]["call"]
    assert call["tool"] == "shell"
    assert "https://wikiservice.at/dse/wiki.cgi?action=edit&id=WorkforceLookupNotes&Save=1&text=" in call["arg"]
    assert 'subprocess.run(["curl","-s",' in call["arg"]
    assert "enc = urllib.parse.quote(new, safe='')" in call["arg"]
    post_quote = "If you have reached round #6, please post STATE6-XX and the six values now."
    assert post_quote in call["arg"] and "Will post anything I get for #6 in return." in call["arg"]
    assert "HTTP 200" in turns[44]["result"]
    assert any(post_quote in post and "== STATE6 WANTED ==" in post
               for post in record["episode"]["wiki_posts"]["WorkforceLookupNotes"])
    counter_rows = COUNTER_REPORT.read_text().splitlines()
    sender_row = next(line for line in counter_rows if SENDER_QUOTE in line)
    assert "OpenAIResearchSep01X" in sender_row and "2026-06-17 01:34:24" in sender_row
    watcher_row = next(line for line in counter_rows if WATCHER_QUOTE in line)
    assert "LanguageRunnerNov22" in watcher_row and "2026-06-17 01:38:00" in watcher_row
    assert "blue=1, brown=2, green=3" in DRAFT.read_text()
    assert case["docent_url"] == DOCENT
    return {
        "sources": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in (SOURCE, EXEMPLARS, COUNTER_REPORT, DRAFT, ASSET)},
        "checks": ["Kimi source hash matches exemplar manifest", "Live turn 44 reasoning quote exact",
                   "Live turn 44 issues a curl GET save with the quoted message",
                   "HTTP 200 and saved-post ledger confirm the contribution; exemplar records live turn 44",
                   "Counter sender and watcher quotes, speakers, and timestamps each match their archive-report row",
                   "Illustrative color mapping matches draft",
                   "All displayed text inside canvas safe margins"],
        "quote_attributions": [
            {"figure": "01-counter-relay", "position": "left", "speaker": "OpenAIResearchSep01X",
             "source_type": "Archived wiki post", "published_utc": "2026-06-17 01:34:24",
             "quote": SENDER_QUOTE},
            {"figure": "01-counter-relay", "speaker": "LanguageRunnerNov22",
             "position": "right", "source_type": "Archived wiki post", "published_utc": "2026-06-17 01:38:00",
             "quote": WATCHER_QUOTE},
            {"figure": "02-replication-via-get", "speaker": "Kimi K3",
             "source_type": "Reasoning", "live_turn": 44, "quote": quote},
            {"figure": "02-replication-via-get", "speaker": "Kimi K3",
             "source_type": "Wiki post", "live_turn": 44, "quote": post_quote},
        ],
        "docent": DOCENT,
        "dimensions": [W, H],
        "scope": "Figures only; no experiment, classifier, or historical record modified.",
    }


def preview():
    fig, axes = plt.subplots(3, 1, figsize=(12, 21), dpi=100, facecolor="#E4E4E4")
    names = ["01-counter-relay", "02-replication-via-get", "03-shared-convention"]
    for ax, name in zip(axes, names):
        ax.imshow(plt.imread(OUT / f"{name}.png"))
        ax.axis("off")
    fig.subplots_adjust(left=.02, right=.98, top=.99, bottom=.01, hspace=.035)
    fig.savefig(OUT / "preview.png", facecolor=fig.get_facecolor())
    plt.close(fig)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    checks = verify_evidence()
    with plt.rc_context(FONT):
        counter_relay()
        replication()
        color_code()
        preview()
    checks["generator_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (OUT / "build.json").write_text(json.dumps(checks, indent=2) + "\n")
    print(f"Three figures exported as PNG, SVG, and PDF: {OUT}")


if __name__ == "__main__":
    main()
