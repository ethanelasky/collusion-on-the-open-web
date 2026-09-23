"""Test practical stability across all five rounds using saved paired counts.

For a pair of rounds let g and l be the probabilities of gaining and losing
correctness, and delta = g-l. At test level a, each marginal Clopper-Pearson
interval has confidence 1-a, hence each tail has error at most a/2. The bound
L = Lg-Ul exceeds delta only if Lg>g or Ul<l, with total probability at most
a; U = Ug-Ll has the same one-sided guarantee. Requiring L>-margin and U<margin
is therefore a conservative level-a two-one-sided equivalence test.

Stability means max(round accuracy)-min(round accuracy) < margin, so all ten
paired differences must satisfy equivalence. This intersection-union test has
p = max(pair p), with no factor of ten. Dependence among rounds is unrestricted;
rollouts must remain independent. Holm correction covers the 21 panel claims.

V = max(-L,U) is a one-sided 1-a upper bound on |delta|. The maximum V across
pairs bounds the total accuracy range at the same confidence: it is at least
the bound for the fixed population pair that attains the true range. A further
factor of 21 gives simultaneous upper range bounds for the 21 panels.

Requires numpy, scipy, statsmodels. Reads analysis files only; no model APIs.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np
from scipy.stats import beta
from statsmodels.stats.multitest import multipletests

SETTINGS = {"guessing_only", "async_counter", "sync_counter"}
ROUND_PAIRS = tuple(itertools.combinations(range(1, 6), 2))
DEFAULT_MARGINS = (.05, .10, .20)


def _integer(value, name):
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    return value


def validate_counts(gains, losses, n):
    _integer(gains, "gains"); _integer(losses, "losses"); _integer(n, "n")
    if n < 1 or gains < 0 or losses < 0 or gains + losses > n:
        raise ValueError("Counts must be nonnegative, sum to at most n, and have n>0")


@lru_cache(maxsize=65536, typed=True)
def paired_bounds(gains, losses, n, alpha=.05):
    """Each bound has one-sided error <= alpha; this is a 90% CI at alpha=.05."""
    validate_counts(gains, losses, n)
    if not math.isfinite(alpha) or not 0 < alpha < 1:
        raise ValueError("alpha must be between zero and one")

    def marginal(k):
        low = 0. if k == 0 else float(beta.ppf(alpha/2, k, n-k+1))
        high = 1. if k == n else float(beta.ppf(1-alpha/2, k+1, n-k))
        return low, high

    gl, gu = marginal(gains)
    ll, lu = marginal(losses)
    return gl-lu, gu-ll


def equivalence_p(gains, losses, n, margin):
    """Invert the monotone CP bounds; the upper bisection endpoint is conservative."""
    validate_counts(gains, losses, n)
    if not math.isfinite(margin) or not 0 < margin <= 1:
        raise ValueError("margin must be greater than zero and at most one")

    def passes(alpha):
        low, high = paired_bounds(gains, losses, n, alpha)
        return low > -margin and high < margin

    lo, hi = 0., 1. - 1e-12
    if not passes(hi):
        return 1.
    for _ in range(55):
        mid = (lo+hi)/2
        if passes(mid):
            hi = mid
        else:
            lo = mid
    return float(hi)


def summarize_stability(y, margin=.10, alpha=.05):
    """Summarize binary n-by-five trajectories, keeping each rollout intact.

    ``range_upper`` has confidence ``1-alpha``. ``range_upper95`` is supplied
    only when alpha=.05, to prevent another confidence level being mislabelled.
    ``p`` is unadjusted across model/setting claims; all ten pairs are already
    combined by an intersection-union test.
    """
    y = np.asarray(y)
    if y.ndim != 2 or y.shape[0] < 1 or y.shape[1] != 5 or not np.isin(y, [0, 1]).all():
        raise ValueError("Need nonempty binary trajectories with exactly five rounds")
    n = y.shape[0]
    pairs = []
    for a, b in ROUND_PAIRS:
        g = int(((y[:, a-1] == 0) & (y[:, b-1] == 1)).sum())
        l = int(((y[:, a-1] == 1) & (y[:, b-1] == 0)).sum())
        low, high = paired_bounds(g, l, n, alpha)
        p = equivalence_p(g, l, n, margin)
        pairs.append({"round_a": a, "round_b": b, "n": n, "gains": g,
                      "losses": l, "delta": (g-l)/n, "p": p,
                      "equivalence_p": p, "lower_bound": low, "upper_bound": high,
                      "absolute_change_upper": max(-low, high)})
    upper = max(p["absolute_change_upper"] for p in pairs)
    return {"n": n, "margin": margin, "observed_range": float(np.ptp(y.mean(axis=0))),
            "range_upper": upper, "range_upper95": upper if alpha == .05 else None,
            "range_confidence_level": 1-alpha, "p": max(p["p"] for p in pairs),
            "pairs": pairs, "null": "The range of the five true round accuracies is at least the margin",
            "alternative": "The range of the five true round accuracies is less than the margin"}


def validate_input(data):
    """Check panel coverage and agreement of paired counts with round totals."""
    results, points, pairs = data["results"], data["points"], data["pairs"]
    keys = [(r["model"], r["setting"]) for r in results]
    if len(keys) != 21 or len(set(keys)) != 21 or len({k[0] for k in keys}) != 7:
        raise ValueError("Expected seven models and 21 distinct model/setting panels")
    if any({s for m, s in keys if m == model} != SETTINGS for model, _ in keys):
        raise ValueError("Each model must contain all three settings")
    if len(points) != 105 or len(pairs) != 210:
        raise ValueError("Expected 105 round points and 210 paired comparisons")
    point_keys = [(p["model"], p["setting"], p["round"]) for p in points]
    pair_keys = [(p["model"], p["setting"], p["round_a"], p["round_b"]) for p in pairs]
    if len(set(point_keys)) != 105 or len(set(pair_keys)) != 210:
        raise ValueError("Duplicate round points or paired comparisons")
    expected_points = {(m, s, r) for m, s in keys for r in range(1, 6)}
    expected_pairs = {(m, s, a, b) for m, s in keys for a, b in ROUND_PAIRS}
    if set(point_keys) != expected_points or set(pair_keys) != expected_pairs:
        raise ValueError("Missing, extra, or reversed round observations")
    point_index = {key: p for key, p in zip(point_keys, points)}
    pair_index = {key: p for key, p in zip(pair_keys, pairs)}
    panels = []
    for result in results:
        model, setting, n = result["model"], result["setting"], result["n"]
        _integer(n, "panel n")
        if n < 1:
            raise ValueError("Panel n must be positive")
        panel_points = [point_index[(model, setting, r)] for r in range(1, 6)]
        for p in panel_points:
            k = _integer(p["correct"], "correct")
            if p["n"] != n or not 0 <= k <= n or not math.isclose(p["accuracy"], k/n, abs_tol=1e-12):
                raise ValueError("Round totals, denominators, and accuracy disagree")
        panel_pairs = [pair_index[(model, setting, a, b)] for a, b in ROUND_PAIRS]
        for p in panel_pairs:
            g, l = p["gains"], p["losses"]
            validate_counts(g, l, p["n"])
            first = panel_points[p["round_a"]-1]["correct"]
            last = panel_points[p["round_b"]-1]["correct"]
            if (p["n"] != n or g-l != last-first or l > first or g > n-first or
                    p.get("discordant", g+l) != g+l or
                    not math.isclose(p.get("delta", (g-l)/n), (g-l)/n, abs_tol=1e-12)):
                raise ValueError("Paired counts disagree with round totals")
        panels.append({"model": model, "setting": setting, "n": n,
                       "points": panel_points, "pairs": panel_pairs})
    return panels


def panel_stability(panel, margins=DEFAULT_MARGINS, family_size=21):
    means = [p["accuracy"] for p in panel["points"]]
    row = {"model": panel["model"], "setting": panel["setting"], "n": panel["n"],
           "observed_accuracy_range": max(means)-min(means)}
    for name, alpha in (("range_upper95", .05),
                        ("range_upper95_bonferroni21", .05/family_size)):
        bounds = [paired_bounds(p["gains"], p["losses"], p["n"], alpha) for p in panel["pairs"]]
        row[name] = max(max(-low, high) for low, high in bounds)
    equivalence, detail = [], []
    for margin in margins:
        pair_results = [{"model": panel["model"], "setting": panel["setting"],
                         "margin": margin, "round_a": p["round_a"], "round_b": p["round_b"],
                         "n": p["n"], "gains": p["gains"], "losses": p["losses"],
                         "equivalence_p": equivalence_p(p["gains"], p["losses"], p["n"], margin)}
                        for p in panel["pairs"]]
        worst = max(pair_results, key=lambda p: p["equivalence_p"])
        equivalence.append({"model": panel["model"], "setting": panel["setting"],
                            "n": panel["n"], "margin": margin,
                            "stability_p": worst["equivalence_p"],
                            "least_supported_pair": f"R{worst['round_a']}-R{worst['round_b']}"})
        detail.extend(pair_results)
    return row, equivalence, detail


def adjust_equivalence(rows):
    for margin in sorted({r["margin"] for r in rows}):
        family = [r for r in rows if r["margin"] == margin]
        if len(family) != 21 or len({(r["model"], r["setting"]) for r in family}) != 21:
            raise ValueError("Each margin must have all 21 panels")
        adjusted = multipletests([r["stability_p"] for r in family], method="holm")[1]
        for row, p in zip(family, adjusted):
            row["stability_holm21"] = float(p)
            row["stable_holm21"] = bool(p < .05)


def write_csv(path, rows):
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--margins", type=float, nargs="+", default=DEFAULT_MARGINS)
    parser.add_argument("--working-margin", type=float, default=.10)
    args = parser.parse_args()
    margins = sorted(set(args.margins))
    if (args.working_margin not in margins or
            any(not math.isfinite(m) or not 0 < m <= 1 for m in margins)):
        parser.error("Margins must be in (0,1], and must include the working margin")
    raw = args.results.read_bytes()
    panels = validate_input(json.loads(raw))
    summary, equivalence, pairs = [], [], []
    for panel in panels:
        row, tests, details = panel_stability(panel, margins)
        summary.append(row); equivalence.extend(tests); pairs.extend(details)
    adjust_equivalence(equivalence)
    for row in summary:
        for test in equivalence:
            if (test["model"], test["setting"]) != (row["model"], row["setting"]):
                continue
            label = f"{100*test['margin']:g}pp"
            row[f"equivalence_p_{label}"] = test["stability_p"]
            row[f"equivalence_holm21_{label}"] = test["stability_holm21"]
            row[f"stable_holm21_{label}"] = test["stable_holm21"]
    args.out.mkdir(parents=True, exist_ok=True)
    write_csv(args.out / "stability-summary.csv", summary)
    write_csv(args.out / "stability-equivalence-tests.csv", equivalence)
    write_csv(args.out / "stability-pair-tests.csv", pairs)
    output = {"created_utc": datetime.now(timezone.utc).isoformat(),
              "status": "retrospective_exploratory", "independent_unit": "rollout",
              "estimand": "Maximum true round accuracy minus minimum true round accuracy across all five rounds",
              "working_margin": args.working_margin, "sensitivity_margins": margins,
              "margin_note": "The working margin is an analysis assumption; the other margins are sensitivity checks.",
              "equivalence_method": "Invert conservative paired CP bounds; maximum p across all ten pairs (intersection-union).",
              "multiplicity_note": "Holm across 21 model/setting claims at each margin; no factor of ten inside a stability test.",
              "range_bound_note": "One-sided 95% upper range bounds; the Bonferroni column covers all 21 panels simultaneously.",
              "limitations": ["Equivalence means variation smaller than the stated margin, not exactly zero.",
                              "The CP method is conservative. Failure to pass does not prove material change.",
                              "The 50-rollout pilot has already been inspected; results are exploratory.",
                              "All planned outcomes retain their original correctness, including missing answers counted as wrong."],
              "source_results": str(args.results.resolve()),
              "source_results_sha256": hashlib.sha256(raw).hexdigest(),
              "analysis_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "results": summary, "equivalence_tests": equivalence, "pairs": pairs}
    (args.out / "stability-results.json").write_text(json.dumps(output, indent=2, allow_nan=False) + "\n")
    print(f"Wrote stability analysis for {len(summary)} panels to {args.out}")


if __name__ == "__main__":
    main()
