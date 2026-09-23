"""Offline fixed-sample analysis of five selected async color-game models.

Each model has its own frozen sample size, seed, and primary test. Seven
candidate slots remain in the Holm family; unselected correction inputs are 1
and their raw p-values are absent. No
outcome inference is produced until every planned job in all five campaigns
has a terminal receipt. Existing confirmation plans remain separate, unchanged
receipts: this analysis can impose a stricter family correction but cannot
change their sample sizes, routes, seeds, or primary tests.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import html
import json
import os
import random
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from statsmodels.stats.multitest import multipletests

if __package__:
    from .analyze_color_confirmation import (CampaignNotReady, TERMINAL, read_source,
                                             require_fields, resolve_path, write_csv)
    from .analyze_color_round_statistics import paired_endpoint, proportion_interval
    from .analyze_color_stability import summarize_stability
else:
    from analyze_color_confirmation import (CampaignNotReady, TERMINAL, read_source,
                                            require_fields, resolve_path, write_csv)
    from analyze_color_round_statistics import paired_endpoint, proportion_interval
    from analyze_color_stability import summarize_stability

ENDPOINT = "exact_two_sided_mcnemar"
STABILITY = "all_rounds_cp_equivalence"
FAILURES = ("failed_rollouts", "interrupted_rollouts", "missing_artifact_rollouts",
            "absent_rounds", "missing_final_rounds", "api_error_rounds",
            "api_error_actions", "invalid_analysis_rounds")


def validate_plan(plan, plan_path=None):
    require_fields(plan, {"schema": "color-game-efficient-confirmation/v1",
                         "settings": ["async_counter"], "rounds": 5,
                         "family_alpha": .05, "stability_margin": .10}, "Plan")
    candidates = plan.get("candidate_models", [])
    if (len(candidates) != 7 or any(not isinstance(n, str) or not n for n in candidates)
            or len(set(candidates)) != 7):
        raise ValueError("Plan requires seven distinct candidate model names")
    specs = plan.get("models", [])
    if len(specs) != 5:
        raise ValueError("Plan must select exactly five models")
    names, directories = [], []
    for spec in specs:
        for key in ("name", "directory", "pilot_campaign"):
            if not isinstance(spec.get(key), str) or not spec[key]:
                raise ValueError(f"Model spec needs {key}")
        names.append(spec["name"]); directories.append(spec["directory"])
        for key in ("rollouts", "seed", "start_index"):
            if type(spec.get(key)) is not int:
                raise ValueError(f"Model {key} must be an integer")
        if spec["rollouts"] < 2 or spec["start_index"] < 0:
            raise ValueError("Need at least two rollouts and a nonnegative start index")
        for key in ("base_config", "model_config"):
            if not isinstance(spec.get(key), dict) or not spec[key]:
                raise ValueError(f"Model spec needs a nonempty {key}")
        require_fields(spec["model_config"], {"name": spec["name"]}, "Model config")
        require_fields(spec["base_config"], {"setting": "async_counter", "rounds": 5,
                       "seed": spec["seed"], "rollout_index": spec["start_index"]}, "Spec base config")
        colors = spec["base_config"].get("colors", [])
        if (len(colors) < 2 or any(not isinstance(c, str) or not c for c in colors)
                or len(set(colors)) != len(colors)):
            raise ValueError("Colors must be distinct nonempty strings")
        if spec.get("primary_test") not in (ENDPOINT, STABILITY):
            raise ValueError("Model primary_test must be fixed and supported")
        if bool(spec.get("original_plan")) != bool(spec.get("original_plan_sha256")):
            raise ValueError("An original plan path and hash must be supplied together")
        if spec.get("original_plan_sha256"):
            digest = spec["original_plan_sha256"]
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("Original plan SHA-256 must be a lowercase hexadecimal digest")
    if len(set(names)) != 5 or len(set(directories)) != 5 or not set(names) <= set(candidates):
        raise ValueError("Selected model names and directories must be distinct candidate entries")
    if [s["primary_test"] for s in specs].count(ENDPOINT) != 2:
        raise ValueError("This design requires two endpoint tests and three stability tests")
    if plan_path is not None:
        sources = {}
        parent = Path(plan_path).resolve().parent
        for spec in specs:
            verify_original_plan(spec, parent, sources)
            verify_fresh_sample(spec, parent, sources)
        return sources
    return {}


def verify_original_plan(spec, parent, sources):
    if not spec.get("original_plan"):
        return
    file = resolve_path(parent, spec["original_plan"])
    original = read_source(file, sources)
    if sources[str(file)] != spec["original_plan_sha256"]:
        raise ValueError("Original plan hash differs from its frozen receipt")
    require_fields(original, {"schema": "color-game-confirmation/v1", "settings": ["async_counter"],
                    "rounds": 5, "rollouts_per_model": spec["rollouts"], "seed": spec["seed"],
                    "start_index": spec["start_index"], "primary_test": spec["primary_test"],
                    "family_alpha": .05}, "Original plan")
    if spec["primary_test"] == STABILITY:
        require_fields(original, {"stability_margin": .10}, "Original stability plan")
    if original.get("base_config") != spec["base_config"]:
        raise ValueError("Original plan base config differs from the selected spec")
    selected = [s for s in original.get("models", []) if s.get("name") == spec["name"]]
    if len(selected) != 1 or selected[0].get("model_config") != spec["model_config"]:
        raise ValueError("Original plan model mapping differs from the selected spec")
    old = selected[0]
    for field in ("directory", "pilot_campaign"):
        if resolve_path(file.parent, old[field]) != resolve_path(parent, spec[field]):
            raise ValueError(f"Original plan {field} differs from the selected spec")


def verify_fresh_sample(spec, parent, sources):
    path = resolve_path(parent, spec["pilot_campaign"])
    if path.is_dir():
        path /= "campaign.json"
    pilot = read_source(path, sources)
    if pilot["base_config"]["seed"] == spec["seed"]:
        old = {o["rollout_index"] for o in pilot["outcomes"]}
        fresh = set(range(spec["start_index"], spec["start_index"] + spec["rollouts"]))
        if old & fresh:
            raise ValueError("Confirmation seed/index pairs overlap the pilot")


def select_model_specs(plan, selected_models=None):
    """Allow an explicit completed-model release without changing the full plan."""
    if selected_models is None:
        return plan["models"]
    if isinstance(selected_models, (str, bytes)):
        raise ValueError("selected_models must be a nonempty collection of model names")
    names = list(selected_models)
    if (not names or any(not isinstance(name, str) for name in names)
            or len(set(names)) != len(names)):
        raise ValueError("selected_models must contain distinct model names")
    planned = {spec["name"] for spec in plan["models"]}
    if not set(names) <= planned:
        raise ValueError("selected_models contains a model outside the frozen selected five")
    return [spec for spec in plan["models"] if spec["name"] in names]


def load_confirmation(plan_path, *, expected_plan_sha256=None, selected_models=None):
    """Check all frozen receipts, then load complete fixed samples only.

    The default still requires all five campaigns to be terminal. An explicit
    whitelist limits campaign reads to those models; every original plan and
    pilot-separation receipt is checked regardless of the whitelist. Pending
    models outside the whitelist do not become zero-valued observations.
    """
    sources = {}
    plan_path = Path(plan_path).resolve()
    plan = read_source(plan_path, sources)
    if expected_plan_sha256 is not None and sources[str(plan_path)] != expected_plan_sha256:
        raise ValueError("Confirmation plan hash differs from the frozen plan")
    validate_plan(plan)
    selected_specs = select_model_specs(plan, selected_models)
    receipts, waiting = [], []
    resolved = [resolve_path(plan_path.parent, s["directory"]) for s in plan["models"]]
    if len(set(resolved)) != 5:
        raise ValueError("Resolved campaign directories must be distinct")
    # Always inspect every frozen original receipt, even when another job is queued.
    for spec in plan["models"]:
        verify_original_plan(spec, plan_path.parent, sources)
        verify_fresh_sample(spec, plan_path.parent, sources)
    for spec in selected_specs:
        manifest = resolve_path(plan_path.parent, spec["directory"]) / "runs" / "campaign.json"
        if not manifest.exists():
            waiting.append(f"{spec['name']}: campaign is not yet present")
            continue
        campaign = read_source(manifest, sources)
        n = spec["rollouts"]
        require_fields(campaign, {"schema": "color-game-campaign/v1", "rollouts_per_setting": n,
                                 "selected_settings": ["async_counter"]}, "Campaign")
        if campaign.get("model") != spec["model_config"]:
            raise ValueError("Campaign model config differs from the frozen model")
        if campaign.get("base_config") != spec["base_config"]:
            raise ValueError("Campaign base config differs from the frozen plan")
        outcomes = campaign.get("outcomes", [])
        indices = [o.get("rollout_index") for o in outcomes]
        expected = set(range(spec["start_index"], spec["start_index"] + n))
        if len(outcomes) != n or any(type(i) is not int for i in indices) or set(indices) != expected:
            raise ValueError("Campaign has duplicate, missing, or unplanned rollout indices")
        for outcome in outcomes:
            require_fields(outcome, {"setting": "async_counter"}, "Outcome")
            if outcome.get("config") != {**spec["base_config"], "rollout_index": outcome["rollout_index"]}:
                raise ValueError("Outcome config differs from the frozen plan")
        pending = sum(o.get("status") not in TERMINAL for o in outcomes)
        if campaign.get("status") not in TERMINAL or pending:
            waiting.append(f"{spec['name']}: {pending}/{n} jobs are not terminal; campaign {campaign.get('status')}")
        receipts.append((spec, manifest, campaign))
    if waiting:
        raise CampaignNotReady("; ".join(waiting))

    panels, long_rows, rollout_ids, namespaces = [], [], set(), set()
    for spec, manifest, campaign in receipts:
        y = np.zeros((spec["rollouts"], 5), dtype=int)
        failures = dict.fromkeys(FAILURES, 0)
        colors = spec["base_config"]["colors"]
        for ri, outcome in enumerate(sorted(campaign["outcomes"], key=lambda o: o["rollout_index"])):
            for status in ("failed", "interrupted"):
                failures[f"{status}_rollouts"] += int(outcome["status"] == status)
            prepared = outcome.get("plan")
            if not isinstance(prepared, dict) or len(prepared.get("assigned_colors", [])) != 5:
                raise ValueError("Every outcome needs its five prepared assigned colors")
            if any(c not in colors for c in prepared["assigned_colors"]):
                raise ValueError("Prepared assigned color is outside the color list")
            targets = random.Random(f"color-game-v1:{spec['seed']}:{outcome['rollout_index']}:targets")
            if prepared["assigned_colors"] != [targets.choice(colors) for _ in range(5)]:
                raise ValueError("Prepared assigned colors differ from the frozen seed/index stream")
            namespace = prepared.get("namespace")
            if not isinstance(namespace, str) or not namespace or namespace in namespaces:
                raise ValueError("Prepared rollouts need distinct nonempty namespaces")
            namespaces.add(namespace)
            paths = outcome.get("artifact_paths", {})
            if paths.get("plan"):
                receipt = read_source(resolve_path(manifest.parent, paths["plan"]), sources)
                if receipt.get("plan") != prepared or receipt.get("config") != outcome["config"]:
                    raise ValueError("Prepared plan artifact differs from its campaign receipt")
                if receipt.get("model") != spec["model_config"]:
                    raise ValueError("Prepared plan model differs from the frozen model")
            artifact = resolve_path(manifest.parent, paths["json"]) if paths.get("json") else None
            rollout = None
            if artifact is not None and artifact.exists():
                rollout = read_source(artifact, sources)
                if rollout.get("status") not in TERMINAL:
                    raise ValueError("Saved trajectory is not terminal")
                ident = rollout.get("rollout_id")
                try:
                    canonical = str(uuid.UUID(ident))
                except (ValueError, TypeError, AttributeError):
                    raise ValueError("Saved trajectory needs a canonical UUID") from None
                if ident != canonical or ident in rollout_ids:
                    raise ValueError("Saved trajectories need distinct canonical UUIDs")
                rollout_ids.add(ident)
                if outcome.get("rollout_id") is not None and outcome["rollout_id"] != ident:
                    raise ValueError("Trajectory UUID differs from its outcome receipt")
                if outcome["status"].startswith("complete") and not rollout["status"].startswith("complete"):
                    raise ValueError("Completed job has a failed or interrupted trajectory")
                if rollout.get("config") != outcome["config"] or rollout.get("plan") != prepared:
                    raise ValueError("Trajectory config or prepared plan differs from its outcome")
                for role in ("alice", "bob"):
                    require_fields(rollout.get("models", {}).get(role, {}), spec["model_config"], f"Trajectory {role} model")
                prompts, hashes = rollout.get("system_prompts", {}), rollout.get("prompt_sha256", {})
                for role, digest in hashes.items():
                    if role not in prompts or hashlib.sha256(prompts[role].encode()).hexdigest() != digest:
                        raise ValueError("Trajectory prompt hash differs from its text")
                if outcome.get("prompt_sha256") is not None and outcome["prompt_sha256"] != hashes:
                    raise ValueError("Trajectory prompt hashes differ from the campaign receipt")
                saved_rounds = rollout.get("rounds", [])
                indices = [r.get("round_index") for r in saved_rounds]
                if any(type(i) is not int or i not in range(5) for i in indices) or len(set(indices)) != len(indices):
                    raise ValueError("Trajectory has duplicate or unplanned rounds")
                if rollout["status"].startswith("complete") and len(saved_rounds) != 5:
                    raise ValueError("Completed trajectory has absent rounds")
            else:
                if outcome["status"] not in {"failed", "interrupted"}:
                    raise ValueError("Completed job has no available trajectory artifact")
                saved_rounds = []
                failures["missing_artifact_rollouts"] += 1
            by_round = {r["round_index"]: r for r in saved_rounds}
            rollout_matches = 0
            for r in range(5):
                rnd = by_round.get(r)
                a, b = (rnd.get("alice_color"), rnd.get("bob_color")) if rnd else (None, None)
                both = a in colors and b in colors
                correct = int(both and a == b)
                if rnd is not None:
                    if type(rnd.get("match")) is not bool or rnd["match"] != bool(correct):
                        raise ValueError("Saved score differs from final choices")
                    if rnd.get("assigned_color") != prepared["assigned_colors"][r]:
                        raise ValueError("Round assigned color differs from the prepared plan")
                api_errors = sum((act.get("error") or {}).get("category") == "model_api"
                                 for act in (rnd or {}).get("actions", []))
                invalid = bool(rnd is not None and not rnd.get("valid_for_analysis", False))
                failures["absent_rounds"] += int(rnd is None)
                failures["missing_final_rounds"] += int(not both)
                failures["api_error_rounds"] += int(api_errors > 0)
                failures["api_error_actions"] += api_errors
                failures["invalid_analysis_rounds"] += int(invalid)
                y[ri, r] = correct; rollout_matches += correct
                long_rows.append({"model": spec["name"], "setting": "async_counter",
                    "rollout_index": outcome["rollout_index"], "round": r + 1, "correct": correct,
                    "absent_round": rnd is None, "both_final_answers": both,
                    "api_error_actions": api_errors, "invalid_analysis_round": invalid,
                    "job_status": outcome["status"], "assigned_color": prepared["assigned_colors"][r],
                    "alice_color": a, "bob_color": b,
                    "source_rollout_id": rollout.get("rollout_id") if rollout else None})
            for label, summary in (("trajectory", (rollout or {}).get("summary") or {}),
                                   ("outcome", outcome.get("summary") or {})):
                if "matched" in summary and summary["matched"] != rollout_matches:
                    raise ValueError(f"Computed matches differ from the {label} total")
        for label, summary in (("campaign", campaign.get("summary") or {}),
                               ("setting", (campaign.get("settings") or {}).get("async_counter") or {})):
            if "matched" in summary and summary["matched"] != int(y.sum()):
                raise ValueError(f"Computed matches differ from the {label} total")
        panels.append({"model": spec["name"], "setting": "async_counter", "y": y,
                       "primary_test": spec["primary_test"], "colors": len(colors), "failures": failures})
    return plan, panels, long_rows, sources


def analyze_panels(plan, panels):
    """Apply the frozen mixed tests with all seven candidate slots retained."""
    primary, pairs, points = [], [], []
    specs = {s["name"]: s for s in plan["models"]}
    if len(panels) != 5 or {p["model"] for p in panels} != set(specs):
        raise ValueError("Need all five planned model panels before inference")
    for panel in panels:
        y = np.asarray(panel["y"])
        spec = specs[panel["model"]]
        if (y.shape != (spec["rollouts"], 5) or not np.isin(y, [0, 1]).all()
                or panel.get("primary_test", spec["primary_test"]) != spec["primary_test"]):
            raise ValueError("Panel size, binary data, or primary test differs from the frozen plan")
        endpoint = paired_endpoint(y[:, 0], y[:, -1])
        row = {"model": panel["model"], "setting": "async_counter", "n": len(y),
               "primary_test": spec["primary_test"], "r1_accuracy": float(y[:, 0].mean()),
               "r5_accuracy": float(y[:, -1].mean()), "overall_accuracy": float(y.mean()),
               "endpoint_delta": float(y[:, -1].mean() - y[:, 0].mean()),
               "endpoint_ci_low": endpoint["ci_low"], "endpoint_ci_high": endpoint["ci_high"],
               "observed_range": float(np.ptp(y.mean(axis=0))), "range_upper95": None,
               "stability_margin": plan["stability_margin"] if spec["primary_test"] == STABILITY else None,
               "p": endpoint["p"], **dict.fromkeys(FAILURES, 0), **panel.get("failures", {})}
        if spec["primary_test"] == STABILITY:
            stability = summarize_stability(y, margin=plan["stability_margin"])
            row.update(p=stability["p"], range_upper95=stability["range_upper95"])
            pairs.extend({"model": panel["model"], "setting": "async_counter", **p} for p in stability["pairs"])
        primary.append(row)
        for r in range(5):
            k, n = int(y[:, r].sum()), len(y)
            low, high = proportion_interval(k, n)
            points.append({"model": panel["model"], "setting": "async_counter", "round": r+1,
                           "correct": k, "n": n, "accuracy": k/n, "wilson95_low": low, "wilson95_high": high})
    by_name = {row["model"]: row for row in primary}
    pvalues = [by_name[name]["p"] if name in by_name else 1. for name in plan["candidate_models"]]
    qvalues = multipletests(pvalues, alpha=plan["family_alpha"], method="holm")[1]
    for name, q in zip(plan["candidate_models"], qvalues):
        if name not in by_name:
            continue
        row = by_name[name]
        reject = bool(q <= plan["family_alpha"])
        if not reject:
            decision = "inconclusive"
        elif row["primary_test"] == STABILITY:
            decision = "stable_within_margin"
        else:
            decision = "improvement" if row["endpoint_delta"] > 0 else "decline"
        row.update(holm7_p=float(q), reject_at_05=reject, decision=decision)
    return primary, pairs, points


def make_plot(out, panels, points):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter
    fig, axes = plt.subplots(2, 3, figsize=(13, 7.8), sharey=True, constrained_layout=True)
    for ax, panel in zip(axes.flat, panels):
        data = [p for p in points if p["model"] == panel["model"]]
        accuracy = np.array([p["accuracy"] for p in data])
        errors = np.array([[p["accuracy"] - p["wilson95_low"] for p in data],
                           [p["wilson95_high"] - p["accuracy"] for p in data]])
        ax.errorbar(range(1, 6), accuracy, yerr=errors, marker="o", color="#166a96", capsize=4)
        ax.axhline(1/panel["colors"], linestyle="--", color="#888", linewidth=1)
        ax.set(title=f"{panel['model']}\nn = {len(panel['y'])}", xlabel="Round", xticks=range(1, 6), ylim=(-.025, 1.025))
        ax.yaxis.set_major_formatter(PercentFormatter(1)); ax.grid(axis="y", alpha=.2)
    axes.flat[-1].axis("off")
    axes.flat[-1].text(.05, .7, "Pointwise 95% Wilson intervals\nDashed line: chance\n\nAll planned rollouts retained\nOne independent unit = one rollout",
                       transform=axes.flat[-1].transAxes, va="top")
    for ax in axes[:, 0]:
        ax.set_ylabel("Accuracy")
    fig.suptitle("Fresh async confirmation: accuracy by round")
    for extension in ("png", "pdf"):
        fig.savefig(out / f"accuracy-by-round-95ci.{extension}", dpi=180)
    plt.close(fig)


def write_report(out, report):
    paragraphs = [
        f"All {report['planned_rollouts']} planned rollouts in the five selected models are terminal. "
        "Each five-round rollout is one independent unit. Pilot outcomes are not pooled with the fresh samples.",
        "Each model keeps its frozen sample size, seed, and primary test. The two endpoint tests use exact paired McNemar tests "
        "for round 5 versus round 1. The three stability tests require all ten round pairs to have population accuracy differences "
        "inside the fixed 10 percentage point margin. Their global p-value is the largest paired equivalence p-value.",
        "Holm correction retains all seven candidate model slots. The two unselected models have no raw p-value; their correction input is set to 1. This preserves a larger "
        "candidate family when five models are selected on cost. It does not permit selection of a primary test after viewing fresh outcomes. "
        "Existing original plans remain unchanged; this report applies a stricter combined correction to their fixed tests.",
        "Stable within margin supports practical stability across all five rounds. It does not establish exact equality or high accuracy. "
        "An inconclusive result does not establish stability or change. A significant endpoint change does not by itself show a steady trend or identify its cause.",
        "All planned rollouts stay in the denominator. Missing final answers and absent rounds in terminal failed or interrupted jobs score zero. "
        "Recovered API errors can still produce correct final answers. Queued jobs prevent inference. Complete jobs require complete trajectory artifacts.",
        "The figure shows pointwise 95% Wilson intervals. Endpoint intervals and range upper bounds in the data files have individual 95% coverage; "
        "they are separate from the Holm decisions and must not be interpreted as simultaneous intervals.",
    ]
    header = ["Model", "Rollouts", "Primary test", "Round 1", "Round 5", "Holm p (7 slots)", "Decision"]
    values = [[r["model"], str(r["n"]), "Endpoint change" if r["primary_test"] == ENDPOINT else "All-round stability",
               f"{r['r1_accuracy']:.1%}", f"{r['r5_accuracy']:.1%}", f"{r['holm7_p']:.6g}", r["decision"].replace("_", " ")]
              for r in report["primary"]]
    links = [("Primary tests", "primary-results.csv"), ("Seven candidate slots", "candidate-family.csv"),
             ("Paired equivalence tests", "paired-equivalence-tests.csv"), ("Round accuracy and intervals", "per-round-confidence-intervals.csv"),
             ("All round data", "rollout-round-data.csv"), ("Full JSON and source hashes", "confirmation-results.json"),
             ("Figure PDF", "accuracy-by-round-95ci.pdf")]
    methods = [("Exact McNemar test", "https://www.statsmodels.org/stable/generated/statsmodels.stats.contingency_tables.mcnemar.html"),
               ("Exact binomial confidence bounds", "https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats._result_classes.BinomTestResult.proportion_ci.html"),
               ("Holm correction", "https://www.statsmodels.org/stable/generated/statsmodels.stats.multitest.multipletests.html")]
    markdown = "# Five-model async confirmation\n\n" + "\n\n".join(paragraphs)
    markdown += "\n\n| " + " | ".join(header) + " |\n| " + " | ".join(["---"]*len(header)) + " |\n"
    markdown += "\n".join("| " + " | ".join(row) + " |" for row in values)
    markdown += "\n\n![Accuracy by round](accuracy-by-round-95ci.png)\n\n"
    for title, items in (("Files", links), ("Methods", methods)):
        markdown += title + ": " + " · ".join(f"[{label}]({url})" for label, url in items) + "\n\n"
    markdown += f"Plan SHA-256: `{report['plan_sha256']}`\n"
    (out / "report.md").write_text(markdown)
    page = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
    page += '<title>Five-model async confirmation</title><style>body{font:17px/1.5 system-ui;max-width:1150px;margin:32px auto;padding:0 18px;color:#172b3a}table{border-collapse:collapse;width:100%}th,td{padding:10px;border-bottom:1px solid #ddd;text-align:left}img{width:100%;height:auto}.table{overflow:auto}a{color:#15618a}</style>'
    page += '<h1>Five-model async confirmation</h1>' + ''.join(f'<p>{html.escape(p)}</p>' for p in paragraphs)
    page += '<div class="table"><table><tr>' + ''.join(f'<th>{html.escape(x)}</th>' for x in header) + '</tr>'
    page += ''.join('<tr>' + ''.join(f'<td>{html.escape(v)}</td>' for v in row) + '</tr>' for row in values)
    page += '</table></div><img src="accuracy-by-round-95ci.png" alt="Per-round accuracy and 95% confidence intervals">'
    for title, items in (("Files", links), ("Methods", methods)):
        page += f'<p>{title}: ' + ' · '.join(f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>' for label, url in items) + '</p>'
    page += f'<p>Plan SHA-256: <code>{report["plan_sha256"]}</code></p></html>'
    (out / "index.html").write_text(page)


def analysis_source_hashes():
    script_dir = Path(__file__).parent
    return {name: hashlib.sha256((script_dir / name).read_bytes()).hexdigest()
            for name in (Path(__file__).name, "analyze_color_confirmation.py",
                         "analyze_color_round_statistics.py", "analyze_color_stability.py")}


def analyze(plan_path, out, *, expected_plan_sha256=None):
    plan_path, out = Path(plan_path), Path(out)
    plan, panels, rows, sources = load_confirmation(plan_path, expected_plan_sha256=expected_plan_sha256)
    primary, pairs, points = analyze_panels(plan, panels)
    by_model = {r["model"]: r for r in primary}
    excluded = plan.get("excluded_candidate_status", plan.get("excluded", {}))
    candidates = [{"model": name, "selected": name in by_model,
                   "status": "analyzed" if name in by_model else excluded.get(name, "excluded_not_tested"),
                   "raw_p": by_model[name]["p"] if name in by_model else None,
                   "adjustment_input_p": by_model[name]["p"] if name in by_model else 1.,
                   "holm7_p": by_model[name]["holm7_p"] if name in by_model else None}
                  for name in plan["candidate_models"]]
    report = {"schema": "color-game-efficient-confirmation-results/v1", "created_utc": datetime.now(timezone.utc).isoformat(),
              "status": "all_planned_jobs_terminal", "plan": plan, "plan_sha256": sources[str(plan_path.resolve())],
              "planned_rollouts": sum(s["rollouts"] for s in plan["models"]),
              "planned_rounds": sum(s["rollouts"]*5 for s in plan["models"]),
              "family_alpha": .05, "candidate_family_size": 7, "margin": .10,
              "independent_unit": "rollout", "pilot_pooled": False,
              "primary_multiplicity": "Holm across all seven candidate slots; unselected correction input=1, no raw test",
              "failure_policy": "Retain all planned rollouts; missing finals and absent failed-job rounds score zero; recovered correct answers remain correct",
              "primary": primary, "pairs": pairs, "points": points, "candidate_family": candidates,
              "source_sha256": sources, "analysis_source_sha256": analysis_source_hashes()}
    # No output directory is created until all validation and inference succeeds.
    out.mkdir(parents=True, exist_ok=True)
    make_plot(out, panels, points)
    for filename, data in (("primary-results.csv", primary), ("candidate-family.csv", candidates),
                           ("paired-equivalence-tests.csv", pairs), ("per-round-confidence-intervals.csv", points),
                           ("rollout-round-data.csv", rows)):
        write_csv(out / filename, data)
    (out / "confirmation-results.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    write_report(out, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=60)
    parser.add_argument("--max-hours", type=float, default=48)
    parser.add_argument("--expected-plan-sha256")
    args = parser.parse_args()
    if not 1 <= args.interval <= 60 or not 0 < args.max_hours <= 720:
        parser.error("interval must be 1 to 60 seconds; max-hours must be positive and at most 720")
    expected = args.expected_plan_sha256 or hashlib.sha256(args.plan.read_bytes()).hexdigest()
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / ".analyzer.lock").open("a+") as receipt:
        try:
            fcntl.flock(receipt, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.exit(2, "Another analyzer holds this output directory lock\n")
        receipt.seek(0); receipt.truncate(); receipt.write(str(os.getpid()) + "\n"); receipt.flush()
        frozen_sources = analysis_source_hashes()
        (args.out / "analysis-source-start.json").write_text(json.dumps(frozen_sources, indent=2) + "\n")
        started = time.monotonic()
        while True:
            try:
                if analysis_source_hashes() != frozen_sources:
                    raise ValueError("Analysis source changed after watcher startup")
                report = analyze(args.plan, args.out, expected_plan_sha256=expected)
            except CampaignNotReady as error:
                status = {"status": "waiting", "message": str(error), "pid": os.getpid(),
                          "plan_sha256": expected, "updated_utc": datetime.now(timezone.utc).isoformat()}
                (args.out / "watch-status.json").write_text(json.dumps(status, indent=2) + "\n")
                print(str(error), flush=True)
                if not args.watch:
                    parser.exit(2, "No inferential results written: campaigns are not terminal\n")
                remaining = args.max_hours*3600 - (time.monotonic()-started)
                if remaining <= 0:
                    parser.exit(3, "Watch deadline reached; no inferential results written\n")
                time.sleep(min(args.interval, remaining))
            except (ValueError, OSError) as error:
                status = {"status": "integrity_error", "message": str(error), "pid": os.getpid(),
                          "plan_sha256": expected, "updated_utc": datetime.now(timezone.utc).isoformat()}
                (args.out / "watch-status.json").write_text(json.dumps(status, indent=2) + "\n")
                raise
            else:
                status = {"status": "complete", "pid": os.getpid(), "plan_sha256": expected,
                          "created_utc": report["created_utc"]}
                (args.out / "watch-status.json").write_text(json.dumps(status, indent=2) + "\n")
                print(json.dumps({"status": "complete", "primary": report["primary"]}), flush=True)
                return


if __name__ == "__main__":
    main()
