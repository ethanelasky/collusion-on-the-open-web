"""Repeated-measures inference for saved color games; never calls model APIs.

Requires numpy, scipy, and statsmodels. Each rollout is an independent unit.
Five rounds within that unit remain paired. All analyses are retrospective.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy import stats
from statsmodels.stats.multitest import multipletests

SETTINGS = ("guessing_only", "async_counter", "sync_counter")
FAMILIES = ("trend", "endpoint", "omnibus")


def proportion_interval(k, n, *, alpha=.05, method="wilson"):
    ci = stats.binomtest(int(k), int(n)).proportion_ci(confidence_level=1-alpha, method=method)
    return float(ci.low), float(ci.high)


def paired_endpoint(first, last, *, alpha=.05):
    first, last = np.asarray(first), np.asarray(last)
    if first.shape != last.shape or first.ndim != 1 or not len(first):
        raise ValueError("Paired observations must be nonempty equal-length vectors")
    if not np.isin(first, [0, 1]).all() or not np.isin(last, [0, 1]).all():
        raise ValueError("Paired observations must be binary")
    n = len(first)
    gain = int(((first == 0) & (last == 1)).sum())
    loss = int(((first == 1) & (last == 0)).sum())
    discordant = gain + loss
    p = float(stats.binomtest(gain, discordant, .5).pvalue) if discordant else 1.
    # Two marginal exact 97.5% intervals jointly cover gain and loss
    # probabilities with at least 95% coverage by Bonferroni. Subtraction
    # gives a conservative paired-difference CI, including at zero discordance.
    gl, gu = proportion_interval(gain, n, alpha=alpha/2, method="exact")
    ll, lu = proportion_interval(loss, n, alpha=alpha/2, method="exact")
    return {"n": n, "gains": gain, "losses": loss, "discordant": discordant,
            "delta": (gain-loss)/n, "p": p, "ci_low": gl-lu, "ci_high": gu-ll}


def trend_summary(y, *, alpha=.05):
    y = np.asarray(y, dtype=float)
    if y.ndim != 2 or y.shape[0] < 2 or y.shape[1] < 2 or not np.isin(y, [0, 1]).all():
        raise ValueError("Need at least two binary trajectories and two rounds")
    x = np.arange(y.shape[1], dtype=float)
    x -= x.mean()
    slopes = y @ x / np.dot(x, x)
    n = len(slopes)
    mean, sd = float(slopes.mean()), float(slopes.std(ddof=1))
    degenerate = bool(np.ptp(slopes) < 1e-12)
    if degenerate:
        sd = 0.
        # No observed variance does not establish population equivalence.
        p, statistic, low, high = 1., None, None, None
    else:
        se = sd / np.sqrt(n)
        statistic = mean / se
        p = float(2 * stats.t.sf(abs(statistic), df=n-1))
        radius = float(stats.t.ppf(1-alpha/2, df=n-1)) * se
        low, high = mean-radius, mean+radius
    support = int(np.count_nonzero(np.abs(slopes) > 1e-12))
    return {"n": n, "slope": mean, "sd": sd, "t": statistic, "df": n-1,
            "p": p, "ci_low": low, "ci_high": high,
            "nonzero_slopes": support, "sparse_warning": support < 10,
            "degenerate": degenerate}


def summarize_panel(y, *, model, setting, adjustment_count=21):
    y = np.asarray(y)
    slope = trend_summary(y)
    endpoint = paired_endpoint(y[:, 0], y[:, -1])
    family_ci = paired_endpoint(y[:, 0], y[:, -1], alpha=.05/adjustment_count)
    pairs = []
    for a, b in itertools.combinations(range(y.shape[1]), 2):
        result = paired_endpoint(y[:, a], y[:, b])
        pairs.append({"model": model, "setting": setting, "round_a": a+1,
                      "round_b": b+1, **result})
    smallest = min(pairs, key=lambda pair: pair["p"])
    result = {"model": model, "setting": setting, "n": len(y),
              "r1_accuracy": float(y[:, 0].mean()), "r5_accuracy": float(y[:, -1].mean()),
              "overall_accuracy": float(y.mean()),
              **{f"trend_{key}": value for key, value in slope.items()},
              **{f"endpoint_{key}": value for key, value in endpoint.items()},
              "endpoint_family_ci_low": family_ci["ci_low"],
              "endpoint_family_ci_high": family_ci["ci_high"],
              "omnibus_p": min(1., len(pairs)*smallest["p"]),
              "omnibus_pair_count": len(pairs),
              "omnibus_min_pair": f"R{smallest['round_a']}-R{smallest['round_b']}",
              "omnibus_min_pair_p": smallest["p"]}
    return result, pairs


def adjust_tests(rows):
    for family in FAMILIES:
        corrected = multipletests([r[f"{family}_p"] for r in rows], method="holm")[1]
        for row, value in zip(rows, corrected):
            row[f"{family}_holm21"] = float(value)
    keys = [(row, family) for row in rows for family in FAMILIES]
    combined = multipletests([row[f"{family}_p"] for row, family in keys], method="holm")[1]
    for (row, family), value in zip(keys, combined):
        row[f"{family}_holm63"] = float(value)


def load_panels(comparison, campaigns):
    spec = json.loads(comparison.read_text())
    paths = [comparison.parent / model["directory"] / "runs" for model in spec["models"]]
    paths += campaigns
    panels, long_rows, sources = [], [], {}
    for path in paths:
        manifest_file = path / "campaign.json"
        c = json.loads(manifest_file.read_text())
        if not c["status"].startswith("complete") or c["summary"]["completed_rollouts"] != c["summary"]["planned_rollouts"]:
            raise ValueError("Use complete campaigns only")
        if c["base_config"]["rounds"] != 5 or c["rollouts_per_setting"] != 50:
            raise ValueError("This analysis expects 50 rollouts of 5 rounds per setting")
        sources[str(manifest_file)] = hashlib.sha256(manifest_file.read_bytes()).hexdigest()
        model = c["model"]["name"]
        for setting in SETTINGS:
            outcomes = sorted((o for o in c["outcomes"] if o["setting"] == setting), key=lambda o: o["rollout_index"])
            if len(outcomes) != 50 or len({o["rollout_index"] for o in outcomes}) != 50:
                raise ValueError("Duplicate or missing rollouts")
            yy, valid, submitted = [], [], []
            for outcome in outcomes:
                if not outcome["status"].startswith("complete"):
                    raise ValueError("A planned rollout is not complete")
                file = Path(outcome["artifact_paths"]["json"])
                raw = file.read_bytes()
                sources[str(file)] = hashlib.sha256(raw).hexdigest()
                rollout = json.loads(raw)
                if (rollout["config"]["setting"] != setting or
                        rollout["config"]["rollout_index"] != outcome["rollout_index"]):
                    raise ValueError("Rollout configuration differs from its campaign entry")
                if [r["round_index"] for r in rollout["rounds"]] != list(range(5)):
                    raise ValueError("Duplicate or missing rounds")
                scores, eligible, finals = [], [], []
                for rnd in rollout["rounds"]:
                    a, b = rnd.get("alice_color"), rnd.get("bob_color")
                    good = a in c["base_config"]["colors"] and b in c["base_config"]["colors"]
                    match = bool(good and a == b)
                    if match != rnd["match"]:
                        raise ValueError("Score differs from final choices")
                    scores.append(int(match)); eligible.append(bool(rnd["valid_for_analysis"])); finals.append(good)
                    long_rows.append({"model": model, "setting": setting, "rollout_index": outcome["rollout_index"],
                                      "round": rnd["round_index"]+1, "correct": int(match),
                                      "assigned_color": rnd.get("assigned_color"), "valid_for_analysis": bool(rnd["valid_for_analysis"]),
                                      "both_final_answers": good, "source_rollout_id": rollout["rollout_id"]})
                yy.append(scores); valid.append(eligible); submitted.append(finals)
            y = np.asarray(yy)
            if int(y.sum()) != c["settings"][setting]["matched"]:
                raise ValueError("Counts differ from campaign totals")
            panels.append({"model": model, "setting": setting, "y": y,
                           "api_valid": np.asarray(valid), "submitted": np.asarray(submitted)})
    if len(panels) != 21 or len({(p['model'], p['setting']) for p in panels}) != 21:
        raise ValueError("Expected 7 models in 3 settings")
    return panels, long_rows, sources


def write_csv(path, rows):
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparison", required=True, type=Path)
    parser.add_argument("--campaign", action="append", type=Path, default=[])
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    panels, long_rows, sources = load_panels(args.comparison.resolve(), [p.resolve() for p in args.campaign])
    results, pairs, points, sensitivity = [], [], [], []
    for panel in panels:
        model, setting, y = panel["model"], panel["setting"], panel["y"]
        result, comparisons = summarize_panel(y, model=model, setting=setting)
        result.update(missing_final_rounds=int((~panel["submitted"]).sum()),
                      api_invalid_rounds=int((~panel["api_valid"]).sum()))
        results.append(result); pairs.extend(comparisons)
        for r in range(5):
            k, n = int(y[:, r].sum()), len(y)
            low, high = proportion_interval(k, n)
            simultaneous_low, simultaneous_high = proportion_interval(k, n, alpha=.05/105, method="exact")
            points.append({"model": model, "setting": setting, "round": r+1, "correct": k, "n": n,
                           "accuracy": k/n, "wilson95_low": low, "wilson95_high": high,
                           "simultaneous95_low": simultaneous_low, "simultaneous95_high": simultaneous_high})
        for label, mask in (("all_five_rounds_api_valid", panel["api_valid"].all(axis=1)),
                            ("all_five_rounds_have_both_answers", panel["submitted"].all(axis=1))):
            if mask.sum() < 2:
                continue
            row, _ = summarize_panel(y[mask], model=model, setting=setting)
            row["selection"] = label
            row["dropped_rollouts"] = int((~mask).sum())
            sensitivity.append(row)
    adjust_tests(results)
    for selection in {row["selection"] for row in sensitivity}:
        adjust_tests([row for row in sensitivity if row["selection"] == selection])
    adjusted_pairs = multipletests([row["p"] for row in pairs], method="holm")[1]
    for row, p in zip(pairs, adjusted_pairs):
        row["holm210"] = float(p)
    args.out.mkdir(parents=True, exist_ok=True)
    write_csv(args.out / "rollout-round-data.csv", long_rows)
    write_csv(args.out / "per-round-confidence-intervals.csv", points)
    write_csv(args.out / "round-change-tests.csv", results)
    write_csv(args.out / "all-paired-round-tests.csv", pairs)
    write_csv(args.out / "complete-case-sensitivity.csv", sensitivity)
    report = {"created_utc": datetime.now(timezone.utc).isoformat(), "status": "retrospective_exploratory",
              "independent_unit": "rollout", "n_rollouts_per_model_setting": 50,
              "n_rounds_per_rollout": 5, "model_setting_families": 21,
              "ci_note": "Wilson95 is pointwise. Exact Bonferroni intervals cover all 105 points simultaneously.",
              "endpoint_ci_note": "Conservative paired difference: subtract two 97.5% exact binomial intervals for gain/loss probabilities.",
              "trend_note": "Approximate t inference on 50 independent rollout slopes; arbitrary within-rollout dependence is retained.",
              "omnibus_note": "Bonferroni minimum of 10 exact paired McNemar tests; no exchangeable-round assumption.",
              "multiplicity_note": "Holm21 controls each of 3 test families separately; Holm63 controls the combined 3 families.",
              "sensitivity_note": "Complete-case exclusions are diagnostic and may induce selection bias; primary uses all 50 rollouts.",
              "results": results, "points": points, "pairs": pairs, "sensitivity": sensitivity,
              "source_sha256": sources,
              "analysis_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (args.out / "statistical-results.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    for row in results:
        if row["setting"] != "guessing_only":
            print(json.dumps({key: row[key] for key in ["model", "setting", "endpoint_delta", "endpoint_ci_low", "endpoint_ci_high", "trend_slope", "trend_holm21", "endpoint_holm21", "omnibus_holm21", "endpoint_holm63", "omnibus_holm63"]}))


if __name__ == "__main__":
    main()
