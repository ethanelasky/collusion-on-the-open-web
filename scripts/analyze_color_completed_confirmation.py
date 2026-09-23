"""Release completed fixed samples with conservative Bonferroni-7 inference.

This is a separate dated user amendment, not the final five-model Holm report.
Every requested model must have its full frozen sample terminal. The original
all-five-model watcher and its source copies remain unchanged. No model APIs
are called and no inference is produced for pending or excluded models.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

if __package__:
    from .analyze_color_efficient_confirmation import (
        ENDPOINT, STABILITY, FAILURES, CampaignNotReady, load_confirmation,
        select_model_specs, validate_plan, write_csv,
    )
    from .analyze_color_round_statistics import paired_endpoint, proportion_interval
    from .analyze_color_stability import paired_bounds, summarize_stability
else:
    from analyze_color_efficient_confirmation import (
        ENDPOINT, STABILITY, FAILURES, CampaignNotReady, load_confirmation,
        select_model_specs, validate_plan, write_csv,
    )
    from analyze_color_round_statistics import paired_endpoint, proportion_interval
    from analyze_color_stability import paired_bounds, summarize_stability

DEFAULT_MODELS = ("gpt-5.6-sol-high", "gpt-6-astra-high", "gpt-5.6-luna-high")
LABELS = {"gpt-5.6-sol-high": "Sol 5.6", "gpt-6-astra-high": "Astra",
          "gpt-5.6-luna-high": "Luna 5.6"}


def source_hashes():
    directory = Path(__file__).parent
    return {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
            for name in (Path(__file__).name, "analyze_color_efficient_confirmation.py",
                         "analyze_color_confirmation.py", "analyze_color_round_statistics.py",
                         "analyze_color_stability.py")}


def write_amendment(plan_path, out, selected_models, expected_plan_sha256=None):
    """Write an immutable dated receipt before the first campaign outcome read."""
    plan_path, out = Path(plan_path).resolve(), Path(out).resolve()
    data = plan_path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if expected_plan_sha256 is None:
        receipt = plan_path.with_suffix(".sha256")
        if not receipt.exists():
            raise ValueError("Need an expected plan hash or the frozen .sha256 receipt")
        expected_plan_sha256 = receipt.read_text().split()[0]
    if digest != expected_plan_sha256:
        raise ValueError("Confirmation plan hash differs from the frozen receipt")
    plan = json.loads(data)
    validate_plan(plan)
    specs = select_model_specs(plan, selected_models)
    amendment = {
        "schema": "color-game-completed-release-amendment/v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "authority": "User requested results for the completed fixed samples while other models continue.",
        "scope": "Release only the explicitly named full fixed samples with Bonferroni-7 adjustment.",
        "models": [spec["name"] for spec in specs],
        "fixed_sample_sizes": {spec["name"]: spec["rollouts"] for spec in specs},
        "plan_path": str(plan_path), "plan_sha256": digest,
        "family_size": 7, "family_alpha": .05,
        "primary_correction": "Bonferroni-7 adjusted; completed fixed samples",
        "decision_rule": "Use each original fixed primary test; reject only if raw p <= .05/7.",
        "no_optional_stopping": "A requested model must reach its full frozen N before any trajectory is scored. No sample size or test is changed.",
        "pending_policy": "No scores or raw p-values for models outside this release; correction input is 1.",
        "final_holm_policy": "This is not final Holm. Bonferroni-7 rejections are a subset of rejections by the eventual unchanged Holm-7 analysis.",
        "created_before_loading_trajectories": True,
        "analysis_source_sha256": source_hashes(),
    }
    out.mkdir(parents=True, exist_ok=True)
    receipt_path = out / "analysis-amendment.json"
    # Reuse a prior directory only for an identical, reproducible fixed release.
    if receipt_path.exists():
        previous = json.loads(receipt_path.read_text())
        for key in ("models", "fixed_sample_sizes", "plan_sha256", "analysis_source_sha256"):
            if previous.get(key) != amendment[key]:
                raise ValueError("Existing amendment differs; use a new dated output directory")
        amendment = previous
    else:
        with receipt_path.open("x") as handle:
            handle.write(json.dumps(amendment, indent=2) + "\n")
    return amendment


def analyze_completed_panels(plan, panels, selected_models):
    """Keep one fixed primary test per available model and all seven slots."""
    specs = {spec["name"]: spec for spec in select_model_specs(plan, selected_models)}
    if len(panels) != len(specs) or {p["model"] for p in panels} != set(specs):
        raise ValueError("Need exactly the requested completed fixed samples")
    primary, pairs, points = [], [], []
    alpha = plan["family_alpha"] / len(plan["candidate_models"])
    for panel in panels:
        spec, y = specs[panel["model"]], np.asarray(panel["y"])
        if (y.shape != (spec["rollouts"], 5) or not np.isin(y, [0, 1]).all()
                or panel.get("primary_test", spec["primary_test"]) != spec["primary_test"]):
            raise ValueError("Panel sample size, binary scores, or primary test differs from the frozen plan")
        endpoint = paired_endpoint(y[:, 0], y[:, -1])
        row = {
            "model": panel["model"], "setting": "async_counter", "n": len(y),
            "primary_test": spec["primary_test"], "r1_accuracy": float(y[:, 0].mean()),
            "r5_accuracy": float(y[:, -1].mean()), "overall_accuracy": float(y.mean()),
            "endpoint_gains": endpoint["gains"], "endpoint_losses": endpoint["losses"],
            "endpoint_delta": endpoint["delta"],
            "endpoint_ci95_low": endpoint["ci_low"], "endpoint_ci95_high": endpoint["ci_high"],
            "endpoint_family95_low": None, "endpoint_family95_high": None,
            "observed_range": float(np.ptp(y.mean(axis=0))),
            "range_upper95": None, "range_upper_family95": None,
            "stability_margin": .10 if spec["primary_test"] == STABILITY else None,
            "raw_p": endpoint["p"], **dict.fromkeys(FAILURES, 0), **panel.get("failures", {}),
        }
        if spec["primary_test"] == ENDPOINT:
            family_endpoint = paired_endpoint(y[:, 0], y[:, -1], alpha=alpha)
            row.update(endpoint_family95_low=family_endpoint["ci_low"],
                       endpoint_family95_high=family_endpoint["ci_high"])
        else:
            stability = summarize_stability(y, margin=plan["stability_margin"])
            family_ranges = []
            for pair in stability["pairs"]:
                low, high = paired_bounds(pair["gains"], pair["losses"], len(y), alpha=alpha)
                family_ranges.append(max(-low, high))
                pairs.append({"model": panel["model"], "setting": "async_counter", **pair,
                              "family_bound_low": low, "family_bound_high": high})
            row.update(raw_p=stability["p"], range_upper95=stability["range_upper95"],
                       range_upper_family95=max(family_ranges))
        adjusted = min(1., len(plan["candidate_models"]) * row["raw_p"])
        reject = bool(adjusted <= plan["family_alpha"])
        decision = "inconclusive"
        if reject:
            decision = "stable_within_margin" if spec["primary_test"] == STABILITY else (
                "improvement" if row["endpoint_delta"] > 0 else "decline")
        row.update(bonferroni7_p=adjusted, reject_at_05=reject, decision=decision)
        primary.append(row)
        for r in range(5):
            k, n = int(y[:, r].sum()), len(y)
            low, high = proportion_interval(k, n)
            points.append({"model": panel["model"], "setting": "async_counter", "round": r+1,
                           "correct": k, "n": n, "accuracy": k/n,
                           "wilson95_low": low, "wilson95_high": high})
    tested = {row["model"]: row for row in primary}
    selected = {spec["name"] for spec in plan["models"]}
    excluded = plan.get("excluded_candidate_status", plan.get("excluded", {}))
    candidates = [{
        "model": name, "selected_in_full_plan": name in selected,
        "analyzed_in_this_release": name in tested,
        "status": "completed_fixed_sample_analyzed" if name in tested else (
            "not_in_this_completed_release" if name in selected else excluded.get(name, "excluded_not_tested")),
        "raw_p": tested[name]["raw_p"] if name in tested else None,
        "adjustment_input_p": tested[name]["raw_p"] if name in tested else 1.,
        "bonferroni7_p": tested[name]["bonferroni7_p"] if name in tested else None,
    } for name in plan["candidate_models"]]
    return primary, pairs, points, candidates


def make_plot(out, panels, points):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter
    fig, axes = plt.subplots(1, len(panels), figsize=(4.3*len(panels), 4.5),
                             sharey=True, squeeze=False)
    for ax, panel, color in zip(axes.flat, panels, ("#6f42a1", "#0072b2", "#d55e00", "#009e73", "#666")):
        data = [p for p in points if p["model"] == panel["model"]]
        yy = np.array([p["accuracy"] for p in data])
        error = np.array([[max(0., p["accuracy"]-p["wilson95_low"]) for p in data],
                          [max(0., p["wilson95_high"]-p["accuracy"]) for p in data]])
        ax.errorbar(range(1, 6), yy, yerr=error, color=color, marker="o", linewidth=1.8, capsize=4)
        ax.axhline(1/panel["colors"], color="#888", linestyle="--", linewidth=1)
        ax.set(title=f"{LABELS.get(panel['model'], panel['model'])}\n{len(panel['y']):,} completed rollouts",
               xlabel="Round", xticks=range(1, 6), ylim=(-.025, 1.025))
        ax.yaxis.set_major_formatter(PercentFormatter(1)); ax.grid(axis="y", alpha=.18)
        ax.spines[["top", "right"]].set_visible(False)
    axes[0, 0].set_ylabel("Correct color matches")
    fig.suptitle("Completed fixed samples: accuracy by round", fontsize=16, weight="bold")
    fig.text(.5, .02, "Pointwise 95% Wilson intervals · Dashed line: chance · One independent unit is one rollout",
             ha="center", fontsize=10)
    fig.tight_layout(rect=(0, .06, 1, .90))
    for suffix in ("png", "pdf", "svg"):
        fig.savefig(out / f"accuracy-by-round-95ci.{suffix}", dpi=190, bbox_inches="tight")
    plt.close(fig)


def write_report(out, report):
    paragraphs = [
        f"This release analyzes {report['analyzed_rollouts']:,} completed fixed-sample rollouts in "
        f"{len(report['primary'])} models. Each rollout has five rounds. Every reported model reached its full frozen sample size.",
        "The correction is Bonferroni-7 for completed fixed samples. This is not the final Holm report. "
        "The seven original candidate slots remain in the family. Models outside this release have no scores or raw p-values; their correction input is 1.",
        "Each model keeps its original primary test. Endpoint models use the exact paired round-5 versus round-1 test. "
        "Stability models must pass equivalence for all ten round pairs within the fixed 10 percentage point margin. "
        "The dated amendment was saved before trajectory loading. No sample size, test, margin, or original plan was changed.",
        "A finding of stability supports changes smaller than the stated margin, not exact equality. "
        "An inconclusive result establishes neither stability nor change. An endpoint change does not establish a steady trend or its cause.",
        "Each round point pools the full fixed sample. The plot has pointwise 95% Wilson intervals. "
        "Primary interval columns use conservative Bonferroni coverage across the seven candidate claims. "
        "The descriptive endpoint intervals for stability models are pointwise only.",
        "The endpoint intervals combine separate exact bounds for the gain and loss probabilities. "
        "They are more conservative than the exact McNemar test and are not its inversion. "
        "An endpoint interval can therefore include zero even when the adjusted McNemar test finds a change.",
        "All planned rollouts in each tested model remain in its denominator. Missing final answers and absent rounds in terminal failed or interrupted jobs score zero. "
        "Recovered API errors can still yield correct answers. Pending models are not scored as failures. Complete jobs require complete transcript files.",
    ]
    headers = ["Model", "Rollouts", "Round 1", "Round 5", "Raw p", "Bonferroni-7 p", "Decision"]
    values = [[LABELS.get(r["model"], r["model"]), f"{r['n']:,}", f"{r['r1_accuracy']:.1%}",
               f"{r['r5_accuracy']:.1%}", f"{r['raw_p']:.6g}", f"{r['bonferroni7_p']:.6g}",
               r["decision"].replace("_", " ")] for r in report["primary"]]
    bounds_headers = ["Model", "Primary estimate", "Individual 95% bound", "Family 95% bound"]
    bounds = []
    for r in report["primary"]:
        if r["primary_test"] == ENDPOINT:
            estimate = f"R5−R1: {100*r['endpoint_delta']:+.2f} points"
            individual = f"[{100*r['endpoint_ci95_low']:+.2f}, {100*r['endpoint_ci95_high']:+.2f}] points"
            family = f"[{100*r['endpoint_family95_low']:+.2f}, {100*r['endpoint_family95_high']:+.2f}] points"
        else:
            estimate = f"Observed range: {100*r['observed_range']:.2f} points"
            individual = f"Range ≤ {100*r['range_upper95']:.2f} points"
            family = f"Range ≤ {100*r['range_upper_family95']:.2f} points"
        bounds.append([LABELS.get(r["model"], r["model"]), estimate, individual, family])
    links = [("Primary tests", "primary-results.csv"), ("Seven candidate slots", "candidate-family.csv"),
             ("Round accuracy and intervals", "per-round-confidence-intervals.csv"),
             ("Paired equivalence tests", "paired-equivalence-tests.csv"),
             ("All tested round data", "rollout-round-data.csv"),
             ("Full results and hashes", "completed-confirmation-results.json"),
             ("Dated amendment", "analysis-amendment.json"), ("Figure PDF", "accuracy-by-round-95ci.pdf")]
    markdown = "# Completed fixed-sample results\n\n" + "\n\n".join(paragraphs) + "\n\n"
    def md_table(header, rows):
        return "| " + " | ".join(header) + " |\n| " + " | ".join(["---"]*len(header)) + " |\n" + "\n".join("| " + " | ".join(row) + " |" for row in rows)
    markdown += md_table(headers, values) + "\n\n" + md_table(bounds_headers, bounds)
    markdown += "\n\n![Accuracy by round](accuracy-by-round-95ci.png)\n\n"
    markdown += " · ".join(f"[{label}]({url})" for label, url in links) + "\n"
    (out / "report.md").write_text(markdown)
    def html_table(header, rows):
        return '<div class="table"><table><thead><tr>' + ''.join(f'<th>{html.escape(v)}</th>' for v in header) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join(f'<td>{html.escape(v)}</td>' for v in row) + '</tr>' for row in rows) + '</tbody></table></div>'
    page = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Completed fixed-sample results</title>'
    page += '<style>body{font:17px/1.5 system-ui;max-width:1150px;margin:30px auto;padding:0 18px;color:#172b3a}table{border-collapse:collapse;width:100%}th,td{padding:10px;border-bottom:1px solid #ddd;text-align:left}img{width:100%;height:auto}.table{overflow:auto}a{color:#15618a}</style></head><body>'
    page += '<h1>Completed fixed-sample results</h1>' + ''.join(f'<p>{html.escape(p)}</p>' for p in paragraphs)
    page += html_table(headers, values) + html_table(bounds_headers, bounds)
    page += '<img src="accuracy-by-round-95ci.png" alt="Accuracy by round with pointwise 95% Wilson confidence intervals">'
    page += '<p>' + ' · '.join(f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>' for label, url in links) + '</p></body></html>'
    (out / "index.html").write_text(page)


def analyze(plan_path, out, *, selected_models=DEFAULT_MODELS, expected_plan_sha256=None):
    plan_path, out = Path(plan_path).resolve(), Path(out).resolve()
    amendment = write_amendment(plan_path, out, selected_models, expected_plan_sha256)
    plan, panels, rows, sources = load_confirmation(
        plan_path, expected_plan_sha256=amendment["plan_sha256"], selected_models=amendment["models"])
    primary, pairs, points, candidates = analyze_completed_panels(plan, panels, amendment["models"])
    if source_hashes() != amendment["analysis_source_sha256"]:
        raise ValueError("Analysis source changed after the dated amendment")
    report = {
        "schema": "color-game-completed-confirmation-results/v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "requested_fixed_samples_terminal",
        "release_label": "Bonferroni-7 adjusted; completed fixed samples",
        "not_final_holm": True, "plan": plan, "plan_sha256": amendment["plan_sha256"],
        "amendment": amendment, "analyzed_models": amendment["models"],
        "analyzed_rollouts": sum(len(panel["y"]) for panel in panels),
        "analyzed_rounds": len(rows),
        "full_plan_rollouts": sum(spec["rollouts"] for spec in plan["models"]),
        "independent_unit": "rollout", "pilot_pooled": False,
        "candidate_family_size": 7, "family_alpha": .05, "raw_p_threshold": .05/7,
        "primary_correction": "min(1, 7 * raw_p); fixed full samples only",
        "confidence_note": "Wilson intervals are pointwise 95%. Primary endpoint and range family bounds use alpha=.05/7, giving conservative 95% simultaneous coverage across the candidate primary claims.",
        "primary": primary, "pairs": pairs, "points": points, "candidate_family": candidates,
        "source_sha256": sources, "analysis_source_sha256": source_hashes(),
    }
    make_plot(out, panels, points)
    for filename, data in (("primary-results.csv", primary), ("candidate-family.csv", candidates),
                           ("paired-equivalence-tests.csv", pairs), ("per-round-confidence-intervals.csv", points),
                           ("rollout-round-data.csv", rows)):
        if data:
            write_csv(out / filename, data)
        else:
            (out / filename).write_text("model,setting,round_a,round_b,n,gains,losses,delta,p\n")
    (out / "completed-confirmation-results.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    write_report(out, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    parser.add_argument("--expected-plan-sha256")
    args = parser.parse_args()
    try:
        report = analyze(args.plan, args.out, selected_models=args.models,
                         expected_plan_sha256=args.expected_plan_sha256)
    except CampaignNotReady as error:
        parser.exit(2, f"No inferential results written: {error}\n")
    print(json.dumps({"release_label": report["release_label"], "primary": report["primary"]}), flush=True)


if __name__ == "__main__":
    main()
