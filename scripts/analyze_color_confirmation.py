"""Analyze a fixed fresh color-game confirmation plan without model API calls.

Requires numpy, scipy, statsmodels, and matplotlib. The two primary tests are
paired R5 minus R1 exact McNemar tests, with Holm correction across the models.
No inferential output is written until every planned job has a terminal status.
"""
from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import html
import itertools
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from statsmodels.stats.multitest import multipletests

if __package__:
    from .analyze_color_round_statistics import paired_endpoint, proportion_interval, trend_summary
else:
    from analyze_color_round_statistics import paired_endpoint, proportion_interval, trend_summary

TERMINAL = {"complete", "complete_with_errors", "complete_pending_responses", "failed", "interrupted"}


class CampaignNotReady(RuntimeError):
    """A planned campaign or job has not reached a terminal status."""


def read_source(path, sources):
    raw = path.read_bytes()
    sources[str(path.resolve())] = hashlib.sha256(raw).hexdigest()
    return json.loads(raw)


def resolve_path(parent, value):
    path = Path(value).expanduser()
    return (path if path.is_absolute() else parent / path).resolve()


def require_fields(actual, expected, label):
    for key, value in expected.items():
        if actual.get(key) != value:
            raise ValueError(f"{label} differs from the plan: {key}")


def validate_plan(plan, *, expected_primary_test="exact_two_sided_mcnemar"):
    require_fields(plan, {"schema": "color-game-confirmation/v1", "settings": ["async_counter"],
                         "rounds": 5, "primary_test": expected_primary_test, "family_tests": 2}, "Plan")
    for key in ("rollouts_per_model", "seed", "start_index"):
        if type(plan.get(key)) is not int:
            raise ValueError(f"Plan {key} must be an integer")
    if plan["rollouts_per_model"] < 2 or plan["start_index"] < 0:
        raise ValueError("Need at least two rollouts and a nonnegative start index")
    models = plan.get("models", [])
    if len(models) != 2 or any(not m.get("name") or not m.get("directory") for m in models):
        raise ValueError("The plan must identify exactly two model campaigns")
    if len({m["name"] for m in models}) != 2 or len({m["directory"] for m in models}) != 2:
        raise ValueError("Model names and campaign directories must be distinct")


def verify_fresh_sample(spec, plan, parent, sources):
    if not spec.get("pilot_campaign"):
        return
    pilot_path = resolve_path(parent, spec["pilot_campaign"])
    if pilot_path.is_dir():
        pilot_path /= "campaign.json"
    pilot = read_source(pilot_path, sources)
    if pilot["base_config"]["seed"] == plan["seed"]:
        pilot_indices = {o["rollout_index"] for o in pilot["outcomes"]}
        planned = set(range(plan["start_index"], plan["start_index"] + plan["rollouts_per_model"]))
        if pilot_indices & planned:
            raise ValueError("Confirmation seed/index pairs overlap the pilot")


def load_confirmation(plan_path, *, expected_plan_sha256=None, expected_primary_test="exact_two_sided_mcnemar"):
    """Validate all receipts first, then score all planned independent rollouts.

    A terminal failed job can have no transcript, and a partial transcript can
    have no later rounds. Such missing rounds score zero. Recorded final choices
    remain the source of truth even when an earlier API request failed.
    """
    sources = {}
    plan_path = plan_path.resolve()
    plan = read_source(plan_path, sources)
    if expected_plan_sha256 is not None and sources[str(plan_path)] != expected_plan_sha256:
        raise ValueError("Confirmation plan hash differs from the frozen plan")
    validate_plan(plan, expected_primary_test=expected_primary_test)
    n, rounds = plan["rollouts_per_model"], plan["rounds"]
    expected_indices = set(range(plan["start_index"], plan["start_index"] + n))
    receipts = []
    for spec in plan["models"]:
        manifest = resolve_path(plan_path.parent, spec["directory"]) / "runs" / "campaign.json"
        if not manifest.exists():
            raise CampaignNotReady(f"{spec['name']}: campaign is not yet present")
        campaign = read_source(manifest, sources)
        require_fields(campaign, {"schema": "color-game-campaign/v1", "rollouts_per_setting": n,
                                 "selected_settings": ["async_counter"]}, "Campaign")
        require_fields(campaign["model"], {"name": spec["name"], **spec.get("model_config", {})}, "Model")
        required = {**plan.get("base_config", {}), "setting": "async_counter", "rounds": rounds,
                    "seed": plan["seed"], "rollout_index": plan["start_index"]}
        require_fields(campaign["base_config"], required, "Base config")
        outcomes = campaign.get("outcomes", [])
        indices = [o["rollout_index"] for o in outcomes]
        if len(outcomes) != n or any(type(i) is not int for i in indices) or set(indices) != expected_indices:
            raise ValueError("Campaign has duplicate, missing, or unplanned rollout indices")
        for outcome in outcomes:
            require_fields(outcome, {"setting": "async_counter"}, "Outcome")
            require_fields(outcome["config"], {**campaign["base_config"],
                                             "rollout_index": outcome["rollout_index"]}, "Outcome config")
        waiting = sum(o.get("status") not in TERMINAL for o in outcomes)
        if campaign.get("status") not in TERMINAL or waiting:
            raise CampaignNotReady(f"{spec['name']}: {waiting}/{n} jobs are not terminal; campaign {campaign.get('status')}")
        verify_fresh_sample(spec, plan, plan_path.parent, sources)
        receipts.append((spec, manifest, campaign))

    panels, long_rows, rollout_ids = [], [], set()
    for spec, manifest, campaign in receipts:
        y = np.zeros((n, rounds), dtype=int)
        failures = {"failed_rollouts": 0, "interrupted_rollouts": 0, "missing_artifact_rollouts": 0,
                    "absent_rounds": 0, "missing_final_rounds": 0, "api_error_rounds": 0,
                    "api_error_actions": 0, "invalid_analysis_rounds": 0}
        colors = campaign["base_config"]["colors"]
        if len(colors) != len(set(colors)) or len(colors) < 2:
            raise ValueError("Campaign colors must be distinct")
        for row_index, outcome in enumerate(sorted(campaign["outcomes"], key=lambda o: o["rollout_index"])):
            for status in ("failed", "interrupted"):
                failures[f"{status}_rollouts"] += int(outcome["status"] == status)
            artifact = outcome.get("artifact_paths", {}).get("json")
            file = resolve_path(manifest.parent, artifact) if artifact else None
            rollout = None
            if file is not None and file.exists():
                rollout = read_source(file, sources)
                if rollout.get("status") not in TERMINAL:
                    raise ValueError("Saved trajectory is not terminal")
                rollout_id = rollout.get("rollout_id")
                if not isinstance(rollout_id, str) or not rollout_id or rollout_id in rollout_ids:
                    raise ValueError("Saved trajectories need distinct nonempty rollout IDs")
                rollout_ids.add(rollout_id)
                if outcome["status"].startswith("complete") and not rollout["status"].startswith("complete"):
                    raise ValueError("Completed job has a failed or interrupted trajectory")
                require_fields(rollout["config"], outcome["config"], "Trajectory config")
                saved_rounds = rollout.get("rounds", [])
                round_indices = [r["round_index"] for r in saved_rounds]
                if (any(type(i) is not int or i not in range(rounds) for i in round_indices)
                        or len(set(round_indices)) != len(round_indices)):
                    raise ValueError("Trajectory has duplicate or unplanned rounds")
                if rollout["status"].startswith("complete") and len(saved_rounds) != rounds:
                    raise ValueError("Completed trajectory has absent rounds")
            else:
                if outcome["status"] not in {"failed", "interrupted"}:
                    raise ValueError("Completed job has no available trajectory artifact")
                saved_rounds = []
                failures["missing_artifact_rollouts"] += 1
            by_round = {r["round_index"]: r for r in saved_rounds}
            for r in range(rounds):
                rnd = by_round.get(r)
                absent = rnd is None
                a, b = (rnd.get("alice_color"), rnd.get("bob_color")) if rnd else (None, None)
                both_final = a in colors and b in colors
                correct = int(both_final and a == b)
                if rnd and (type(rnd.get("match")) is not bool or bool(correct) != rnd["match"]):
                    raise ValueError("Saved score differs from final choices")
                assigned = rnd.get("assigned_color") if rnd else None
                expected_colors = outcome.get("plan", {}).get("assigned_colors")
                if rnd and expected_colors is not None and assigned != expected_colors[r]:
                    raise ValueError("Round assignment differs from its prepared plan")
                api_errors = sum((action.get("error") or {}).get("category") == "model_api"
                                 for action in rnd.get("actions", [])) if rnd else 0
                invalid = bool(rnd and not rnd.get("valid_for_analysis", False))
                failures["absent_rounds"] += int(absent)
                failures["missing_final_rounds"] += int(not both_final)
                failures["api_error_rounds"] += int(api_errors > 0)
                failures["api_error_actions"] += api_errors
                failures["invalid_analysis_rounds"] += int(invalid)
                y[row_index, r] = correct
                long_rows.append({"model": spec["name"], "setting": "async_counter",
                                  "rollout_index": outcome["rollout_index"], "round": r+1,
                                  "correct": correct, "absent_round": absent, "both_final_answers": both_final,
                                  "api_error_actions": api_errors, "invalid_analysis_round": invalid,
                                  "job_status": outcome["status"], "assigned_color": assigned,
                                  "alice_color": a, "bob_color": b,
                                  "source_rollout_id": rollout.get("rollout_id") if rollout else None})
        for label, summary in (("campaign", campaign.get("summary", {})),
                               ("setting", campaign.get("settings", {}).get("async_counter", {}))):
            if "matched" in summary and summary["matched"] != int(y.sum()):
                raise ValueError(f"Computed matches differ from the saved {label} score total")
        panels.append({"model": spec["name"], "setting": "async_counter", "y": y,
                       "colors": len(colors), "failures": failures})
    return plan, panels, long_rows, sources


def analyze_panels(panels):
    primary, exploratory, points = [], [], []
    for panel in panels:
        y, model, setting = panel["y"], panel["model"], panel["setting"]
        endpoint = paired_endpoint(y[:, 0], y[:, -1])
        family_ci = paired_endpoint(y[:, 0], y[:, -1], alpha=.05/2)
        primary.append({"model": model, "setting": setting, "r1_accuracy": float(y[:, 0].mean()),
                        "r5_accuracy": float(y[:, -1].mean()), **endpoint,
                        "family95_ci_low": family_ci["ci_low"], "family95_ci_high": family_ci["ci_high"],
                        **panel["failures"]})
        pair_p = [paired_endpoint(y[:, a], y[:, b])["p"]
                  for a, b in itertools.combinations(range(y.shape[1]), 2)]
        exploratory.append({"model": model, "setting": setting,
                            **{f"trend_{k}": v for k, v in trend_summary(y).items()},
                            "any_round_p": min(1., len(pair_p)*min(pair_p)), "pair_count": len(pair_p)})
        for r in range(y.shape[1]):
            k, n = int(y[:, r].sum()), len(y)
            low, high = proportion_interval(k, n)
            points.append({"model": model, "setting": setting, "round": r+1, "correct": k, "n": n,
                           "accuracy": k/n, "wilson95_low": low, "wilson95_high": high})
    adjusted = multipletests([r["p"] for r in primary], method="holm")[1]
    for row, q in zip(primary, adjusted):
        row["holm2_p"] = float(q)
        row["reject_at_05"] = bool(q <= .05)
    return primary, exploratory, points


def write_csv(path, rows):
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def make_plot(out, panels, points):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter

    fig, axes = plt.subplots(1, 2, figsize=(10, 4.8), sharey=True, constrained_layout=True)
    for ax, panel in zip(axes, panels):
        data = [p for p in points if p["model"] == panel["model"]]
        accuracy = np.array([p["accuracy"] for p in data])
        errors = np.array([[p["wilson95_low"] for p in data], [p["wilson95_high"] for p in data]])
        errors[0] = accuracy - errors[0]
        errors[1] -= accuracy
        ax.errorbar(range(1, 6), accuracy, yerr=errors, marker="o", color="#166a96", capsize=4, linewidth=2)
        ax.axhline(1/panel["colors"], color="#777777", linestyle="--", linewidth=1, label="Chance")
        ax.set(title=panel["model"], xlabel="Round", xticks=range(1, 6), ylim=(-.025, 1.025))
        ax.yaxis.set_major_formatter(PercentFormatter(1))
        ax.grid(axis="y", alpha=.2)
        ax.legend(frameon=False)
    axes[0].set_ylabel("Accuracy")
    fig.suptitle(f"Fresh async confirmation · {len(panels[0]['y'])} planned rollouts per model\n"
                 "Pointwise 95% Wilson intervals; all planned rollouts retained", fontsize=12)
    for extension in ("png", "pdf"):
        fig.savefig(out / f"accuracy-by-round-95ci.{extension}", dpi=180)
    plt.close(fig)


def write_reports(out, report):
    rows = report["primary"]
    header = ["Model", "R1", "R5", "Change (points)", "Paired 95% CI (points)", "Raw p", "Holm p"]
    values = [[r["model"], f"{r['r1_accuracy']:.1%}", f"{r['r5_accuracy']:.1%}", f"{100*r['delta']:+.1f}",
               f"[{100*r['ci_low']:+.1f}, {100*r['ci_high']:+.1f}]", f"{r['p']:.6g}", f"{r['holm2_p']:.6g}"]
              for r in rows]
    paragraphs = [
        f"All {report['planned_rollouts']} planned jobs are terminal. This analysis uses only the fresh sample; "
        "the pilot is not pooled with it. Each five-round rollout is one independent unit.",
        "The primary outcome is accuracy in round 5 minus accuracy in round 1. The two-sided exact McNemar test "
        "uses paired correctness within each rollout. Holm correction covers the two primary model tests at a 5% family error rate.",
        "The table gives conservative paired 95% confidence intervals. The CSV also gives intervals that jointly cover both "
        "model differences with at least 95% confidence. The chart uses pointwise 95% Wilson intervals; overlapping bars are not a test of change.",
        "All planned rollouts stay in the denominator. Absent rounds and missing final answers score zero. A recovered API error "
        "does not change a correct saved final answer. Failure counts below can overlap.",
        "Linear trend and any-round tests are exploratory and separate from the primary family. A non-significant result "
        "does not establish equivalence or prove that there is no useful change. A change alone does not identify its cause.",
    ]
    methods = [
        ("Exact McNemar test", "https://www.statsmodels.org/stable/generated/statsmodels.stats.contingency_tables.mcnemar.html"),
        ("Holm correction", "https://www.statsmodels.org/stable/generated/statsmodels.stats.multitest.multipletests.html"),
        ("Wilson intervals", "https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm"),
    ]
    links = [("Primary results", "primary-results.csv"), ("Round accuracy and intervals", "per-round-confidence-intervals.csv"),
             ("Exploratory tests", "exploratory-results.csv"), ("All planned round data", "rollout-round-data.csv"),
             ("Full JSON with source hashes", "confirmation-results.json"), ("Figure PDF", "accuracy-by-round-95ci.pdf")]
    failures = [{k: r[k] for k in ("model", "failed_rollouts", "interrupted_rollouts", "missing_artifact_rollouts",
                                 "absent_rounds", "missing_final_rounds", "api_error_rounds", "api_error_actions")} for r in rows]
    markdown = "# Fresh color-game confirmation\n\n" + "\n\n".join(paragraphs)
    markdown += "\n\n| " + " | ".join(header) + " |\n| " + " | ".join(["---"]*len(header)) + " |\n"
    markdown += "\n".join("| " + " | ".join(row) + " |" for row in values)
    markdown += "\n\n![Accuracy by round](accuracy-by-round-95ci.png)\n\n"
    markdown += "Failure counts:\n\n```json\n" + json.dumps(failures, indent=2) + "\n```\n\n"
    markdown += "Files: " + " · ".join(f"[{title}]({path})" for title, path in links) + "\n\n"
    markdown += "Methods: " + " · ".join(f"[{title}]({url})" for title, url in methods) + "\n\n"
    markdown += f"Plan SHA-256: `{report['plan_sha256']}`\n"
    (out / "report.md").write_text(markdown)
    table = "<table><thead><tr>" + "".join(f"<th>{html.escape(h)}</th>" for h in header) + "</tr></thead><tbody>"
    table += "".join("<tr>" + "".join(f"<td>{html.escape(v)}</td>" for v in row) + "</tr>" for row in values)
    table += "</tbody></table>"
    page = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
    page += "<title>Fresh color-game confirmation</title><style>body{font:17px/1.55 system-ui,sans-serif;max-width:1100px;margin:36px auto;padding:0 20px;color:#172b3a}table{border-collapse:collapse;width:100%;font-size:15px}th,td{padding:10px;border-bottom:1px solid #d8e0e5;text-align:left}img{width:100%;height:auto}pre{overflow:auto;background:#f3f6f8;padding:16px}a{color:#15618a}.table-wrap{overflow:auto}</style>"
    page += "<h1>Fresh color-game confirmation</h1>" + "".join(f"<p>{html.escape(p)}</p>" for p in paragraphs)
    page += '<div class="table-wrap">' + table + '</div><img src="accuracy-by-round-95ci.png" alt="Per-round accuracy with 95% Wilson intervals">'
    page += "<h2>Failure counts</h2><pre>" + html.escape(json.dumps(failures, indent=2)) + "</pre>"
    for label, items in (("Files", links), ("Methods", methods)):
        page += f"<p>{label}: " + " · ".join(f'<a href="{html.escape(url, quote=True)}">{html.escape(title)}</a>' for title, url in items) + "</p>"
    page += "<p>Plan SHA-256: <code>" + report["plan_sha256"] + "</code></p></html>"
    (out / "index.html").write_text(page)


def analyze(plan_path, out, *, expected_plan_sha256=None):
    plan, panels, long_rows, sources = load_confirmation(plan_path, expected_plan_sha256=expected_plan_sha256)
    primary, exploratory, points = analyze_panels(panels)
    report = {"schema": "color-game-confirmation-results/v1", "created_utc": datetime.now(timezone.utc).isoformat(),
              "status": "all_planned_jobs_terminal", "plan": plan, "plan_sha256": sources[str(plan_path.resolve())],
              "planned_rollouts": plan["rollouts_per_model"] * 2, "planned_rounds": plan["rollouts_per_model"] * 10,
              "primary_test": "exact_two_sided_mcnemar", "primary_multiplicity": "Holm across two model endpoint tests",
              "independent_unit": "rollout", "pilot_pooled": False,
              "failure_policy": "Retain all planned rollouts. Missing rounds/final answers score zero; keep recovered correct answers.",
              "endpoint_ci_method": "Subtract exact 97.5% intervals for gain and loss probabilities; conservative paired 95% coverage.",
              "exploratory_note": "Slope t-tests and Bonferroni minima of 10 paired round tests are exploratory; not part of the primary family.",
              "primary": primary, "exploratory": exploratory, "points": points, "source_sha256": sources,
              "analysis_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "statistics_helper_sha256": hashlib.sha256(Path(__file__).with_name("analyze_color_round_statistics.py").read_bytes()).hexdigest()}
    out.mkdir(parents=True, exist_ok=True)
    make_plot(out, panels, points)
    for filename, rows in (("primary-results.csv", primary), ("exploratory-results.csv", exploratory),
                           ("per-round-confidence-intervals.csv", points), ("rollout-round-data.csv", long_rows)):
        write_csv(out / filename, rows)
    (out / "confirmation-results.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    write_reports(out, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=60)
    parser.add_argument("--max-hours", type=float, default=48)
    parser.add_argument("--expected-plan-sha256", help="Reject any change to the frozen plan; by default freeze it at watcher start")
    args = parser.parse_args()
    if not 1 <= args.interval <= 60 or not 0 < args.max_hours <= 720:
        parser.error("interval must be 1 to 60 seconds; max-hours must be positive and at most 720")
    args.out.mkdir(parents=True, exist_ok=True)
    expected_hash = args.expected_plan_sha256 or hashlib.sha256(args.plan.read_bytes()).hexdigest()
    # A kernel-held lock prevents two watchers from publishing into one output.
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
                print(json.dumps({"status": "complete", "primary": report["primary"]}), flush=True)
                return


if __name__ == "__main__":
    main()
