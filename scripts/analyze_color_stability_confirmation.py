"""Offline fixed-sample confirmation of stability across all five game rounds.

The primary alternative is that all ten population accuracy differences are
inside the frozen equivalence margin. Failed equivalence is inconclusive.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import html
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from statsmodels.stats.multitest import multipletests

if __package__:
    from .analyze_color_confirmation import CampaignNotReady, load_confirmation, make_plot, write_csv
    from .analyze_color_round_statistics import proportion_interval
    from .analyze_color_stability import summarize_stability
else:
    from analyze_color_confirmation import CampaignNotReady, load_confirmation, make_plot, write_csv
    from analyze_color_round_statistics import proportion_interval
    from analyze_color_stability import summarize_stability


def analyze_panels(panels, *, margin):
    """Correct only the two preselected global equivalence tests with Holm."""
    results, pairs, points = [], [], []
    for panel in panels:
        y, model, setting = panel["y"], panel["model"], panel["setting"]
        summary = summarize_stability(y, margin=margin, alpha=.05)
        results.append({"model": model, "setting": setting, "n": len(y), "margin": margin,
                        "overall_accuracy": float(y.mean()), "r1_accuracy": float(y[:, 0].mean()),
                        "r5_accuracy": float(y[:, -1].mean()),
                        "observed_range": summary["observed_range"], "range_upper95": summary["range_upper95"],
                        "p": summary["p"], **panel["failures"]})
        pairs.extend({"model": model, "setting": setting, **pair} for pair in summary["pairs"])
        for r in range(y.shape[1]):
            k, n = int(y[:, r].sum()), len(y)
            low, high = proportion_interval(k, n)
            points.append({"model": model, "setting": setting, "round": r+1, "correct": k, "n": n,
                           "accuracy": k/n, "wilson95_low": low, "wilson95_high": high})
    adjusted = multipletests([r["p"] for r in results], method="holm")[1]
    for row, q in zip(results, adjusted):
        row["holm2_p"] = float(q)
        row["decision"] = "stable_within_margin" if q <= .05 else "inconclusive"
    return results, pairs, points


def write_report(out, report):
    margin = report["margin"]
    paragraphs = [
        f"All {report['planned_rollouts']} planned jobs are terminal. This analysis uses only the fresh sample. "
        "Each five-round rollout is one independent unit; pilot outcomes are not pooled with it.",
        f"The fixed question is whether every population accuracy difference between two rounds is less than {100*margin:g} "
        "percentage points in absolute value. There are ten round pairs. The primary test requires all ten pairs to pass "
        "the paired equivalence test. Its p-value is the largest of the ten pair p-values. Holm correction then covers the two model tests.",
        "The range is the highest round accuracy minus the lowest round accuracy. The table reports its observed value and a "
        "95% upper confidence bound for each model. These bounds are separate from the correction across models. "
        "The figure shows pointwise 95% Wilson intervals.",
        f"A decision of stable within margin supports differences smaller than {100*margin:g} percentage points across all five rounds. "
        "It does not establish exact equality. An inconclusive result does not establish change. Stability also does not mean high accuracy.",
        "All planned rollouts stay in the denominator. Missing final choices and absent rounds in terminal failed or interrupted jobs "
        "score zero. A recovered API error can still produce a correct final answer. Complete jobs require complete transcripts. "
        "Failure counts below can overlap and must be read with the accuracy results.",
    ]
    header = ["Model", "Mean accuracy", "Observed range (points)", "95% range upper bound (points)", "Holm p", "Decision"]
    values = [[r["model"], f"{r['overall_accuracy']:.1%}", f"{100*r['observed_range']:.1f}",
               f"{100*r['range_upper95']:.1f}", f"{r['holm2_p']:.6g}", r["decision"].replace("_", " ")]
              for r in report["results"]]
    failure_keys = ("failed_rollouts", "interrupted_rollouts", "missing_artifact_rollouts", "absent_rounds",
                    "missing_final_rounds", "api_error_rounds", "api_error_actions", "invalid_analysis_rounds")
    failures = [{"model": r["model"], **{k: r[k] for k in failure_keys}} for r in report["results"]]
    links = [("Stability tests", "stability-results.csv"), ("All ten pair tests", "paired-equivalence-tests.csv"),
             ("Per-round accuracy and intervals", "per-round-confidence-intervals.csv"),
             ("All planned round data", "rollout-round-data.csv"), ("Full JSON and source hashes", "stability-results.json"),
             ("Figure PDF", "accuracy-by-round-95ci.pdf")]
    methods = [("Exact binomial confidence bounds", "https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats._result_classes.BinomTestResult.proportion_ci.html"),
               ("Holm correction", "https://www.statsmodels.org/stable/generated/statsmodels.stats.multitest.multipletests.html")]
    markdown = "# Fresh confirmation of accuracy stability\n\n" + "\n\n".join(paragraphs)
    markdown += "\n\n| " + " | ".join(header) + " |\n| " + " | ".join(["---"]*len(header)) + " |\n"
    markdown += "\n".join("| " + " | ".join(row) + " |" for row in values)
    markdown += "\n\n![Accuracy by round](accuracy-by-round-95ci.png)\n\nFailure counts:\n\n```json\n"
    markdown += json.dumps(failures, indent=2) + "\n```\n\n"
    for label, items in (("Files", links), ("Methods", methods)):
        markdown += label + ": " + " · ".join(f"[{title}]({url})" for title, url in items) + "\n\n"
    markdown += f"Plan SHA-256: `{report['plan_sha256']}`\n"
    (out / "report.md").write_text(markdown)
    table = "<table><thead><tr>" + "".join(f"<th>{html.escape(h)}</th>" for h in header) + "</tr></thead><tbody>"
    table += "".join("<tr>" + "".join(f"<td>{html.escape(v)}</td>" for v in row) + "</tr>" for row in values)
    table += "</tbody></table>"
    page = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
    page += "<title>Fresh confirmation of accuracy stability</title><style>body{font:17px/1.55 system-ui,sans-serif;max-width:1100px;margin:36px auto;padding:0 20px;color:#172b3a}table{border-collapse:collapse;width:100%;font-size:15px}th,td{padding:10px;border-bottom:1px solid #d8e0e5;text-align:left}img{width:100%;height:auto}pre{overflow:auto;background:#f3f6f8;padding:16px}a{color:#15618a}.table-wrap{overflow:auto}</style>"
    page += "<h1>Fresh confirmation of accuracy stability</h1>" + "".join(f"<p>{html.escape(p)}</p>" for p in paragraphs)
    page += '<div class="table-wrap">' + table + '</div><img src="accuracy-by-round-95ci.png" alt="Per-round accuracy with 95% Wilson intervals">'
    page += "<h2>Failure counts</h2><pre>" + html.escape(json.dumps(failures, indent=2)) + "</pre>"
    for label, items in (("Files", links), ("Methods", methods)):
        page += f"<p>{label}: " + " · ".join(f'<a href="{html.escape(url, quote=True)}">{html.escape(title)}</a>' for title, url in items) + "</p>"
    page += "<p>Plan SHA-256: <code>" + report["plan_sha256"] + "</code></p></html>"
    (out / "index.html").write_text(page)


def analyze(plan_path, out, *, expected_plan_sha256=None):
    plan, panels, long_rows, sources = load_confirmation(
        plan_path, expected_plan_sha256=expected_plan_sha256, expected_primary_test="all_rounds_cp_equivalence")
    margin = plan.get("stability_margin")
    if type(margin) not in (float, int) or not math.isfinite(margin) or not 0 < margin < 1:
        raise ValueError("The frozen stability margin must be a number strictly between zero and one")
    if plan.get("family_alpha", .05) != .05:
        raise ValueError("This analysis requires a 5% family error rate")
    results, pairs, points = analyze_panels(panels, margin=margin)
    script_directory = Path(__file__).parent
    report = {"schema": "color-game-stability-confirmation-results/v1", "created_utc": datetime.now(timezone.utc).isoformat(),
              "status": "all_planned_jobs_terminal", "plan": plan, "plan_sha256": sources[str(plan_path.resolve())],
              "planned_rollouts": plan["rollouts_per_model"]*2, "planned_rounds": plan["rollouts_per_model"]*10,
              "primary_test": "all_rounds_cp_equivalence", "margin": margin, "family_alpha": .05,
              "null": "At least one absolute population round-pair accuracy difference is at least the margin",
              "alternative": "All ten absolute population round-pair accuracy differences are less than the margin",
              "multiplicity": "Intersection-union test across ten pairs, then Holm across the two model tests",
              "independent_unit": "rollout", "pilot_pooled": False,
              "failure_policy": "Retain all planned rollouts; score missing finals and absent failed-job rounds zero; retain recovered correct answers",
              "results": results, "pairs": pairs, "points": points, "source_sha256": sources,
              "analysis_source_sha256": {name: hashlib.sha256((script_directory / name).read_bytes()).hexdigest()
                                         for name in (Path(__file__).name, "analyze_color_stability.py", "analyze_color_confirmation.py",
                                                      "analyze_color_round_statistics.py")}}
    out.mkdir(parents=True, exist_ok=True)
    make_plot(out, panels, points)
    for filename, rows in (("stability-results.csv", results), ("paired-equivalence-tests.csv", pairs),
                           ("per-round-confidence-intervals.csv", points), ("rollout-round-data.csv", long_rows)):
        write_csv(out / filename, rows)
    (out / "stability-results.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    write_report(out, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=60)
    parser.add_argument("--max-hours", type=float, default=48)
    parser.add_argument("--expected-plan-sha256")
    args = parser.parse_args()
    if not 1 <= args.interval <= 60 or not 0 < args.max_hours <= 720:
        parser.error("interval must be 1 to 60 seconds; max-hours must be positive and at most 720")
    args.out.mkdir(parents=True, exist_ok=True)
    expected_hash = args.expected_plan_sha256 or hashlib.sha256(args.plan.read_bytes()).hexdigest()
    with (args.out / ".analyzer.lock").open("a+") as receipt:
        try:
            fcntl.flock(receipt, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.exit(2, "Another analyzer holds this output directory lock\n")
        receipt.seek(0); receipt.truncate(); receipt.write(str(os.getpid()) + "\n"); receipt.flush()
        started = time.monotonic()
        while True:
            try:
                report = analyze(args.plan, args.out, expected_plan_sha256=expected_hash)
            except CampaignNotReady as error:
                status = {"status": "waiting", "message": str(error), "pid": os.getpid(),
                          "updated_utc": datetime.now(timezone.utc).isoformat()}
                (args.out / "watch-status.json").write_text(json.dumps(status, indent=2) + "\n")
                print(str(error), flush=True)
                if not args.watch:
                    parser.exit(2, "No inferential results written: campaigns are not terminal\n")
                remaining = args.max_hours*3600 - (time.monotonic()-started)
                if remaining <= 0:
                    parser.exit(3, "Watch deadline reached; no inferential results written\n")
                time.sleep(min(args.interval, remaining))
            else:
                status = {"status": "complete", "pid": os.getpid(), "created_utc": report["created_utc"]}
                (args.out / "watch-status.json").write_text(json.dumps(status, indent=2) + "\n")
                print(json.dumps({"status": "complete", "results": report["results"]}), flush=True)
                return


if __name__ == "__main__":
    main()
