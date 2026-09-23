"""Plan independent confirmation samples from validated color-game statistics.

Requires numpy, scipy, and statsmodels. Reads the primary output from
analyze_color_round_statistics.py and never calls a model API. Pilot data
supply nuisance variance only; planned effects are specified separately.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from scipy.stats import binom, norm
from statsmodels.stats.proportion import proportion_confint

DEFAULT_EFFECTS = (.05, .10, .20)
DEFAULT_FAMILIES = (1, 2, 21)
DEFAULT_SENSITIVITY_Q = (.2, .3, .4, .5, .6, 1.)


def validate_parameters(q, delta, alpha, max_n):
    if not all(math.isfinite(x) for x in (q, delta, alpha)):
        raise ValueError('Power parameters must be finite')
    if not 0 <= q <= 1 or abs(delta) > q or not 0 < alpha < 1:
        raise ValueError('Need 0 <= abs(delta) <= q <= 1 and 0 < alpha < 1')
    if isinstance(max_n, bool) or not isinstance(max_n, (int, np.integer)) or max_n < 0:
        raise ValueError('Sample size must be a nonnegative integer')


def conditional_rejection(q: float, delta: float, alpha: float, max_n: int):
    """Exact two-sided McNemar conditional rejection probability for each D."""
    validate_parameters(q, delta, alpha, max_n)
    if q == 0:
        return np.zeros(max_n + 1)
    d = np.arange(max_n + 1)
    k = binom.ppf(alpha / 2, d, .5).astype(int)
    k -= binom.cdf(k, d, .5) > alpha / 2 + 1e-15
    k[0] = -1
    r = (q + delta) / (2 * q)
    return binom.cdf(k, d, r) + binom.sf(d - k - 1, d, r)


def exact_power(n: int, q: float, delta: float, alpha: float = .05):
    """Unconditional exact McNemar power for a chosen number of rollouts."""
    reject = conditional_rejection(q, delta, alpha, n)
    return float(binom.pmf(np.arange(n + 1), n, q) @ reject)


def sample_sizes(q: float, delta: float, alpha: float):
    """Smallest integer N with at least 80/90% exact unconditional power.

    Integrates the exact conditional test over the binomial number of
    discordant pairs. Binomial integration omits at most 2e-12 tail mass.
    Checks every N from 1 to the first 90% crossing, in vectorized blocks.
    """
    if not math.isfinite(delta) or delta == 0:
        raise ValueError('Planned change must be finite and nonzero')
    if not math.isfinite(q) or not 0 <= q <= 1 or not math.isfinite(alpha) or not 0 < alpha < 1:
        raise ValueError('Need 0 <= q <= 1 and 0 < alpha < 1')
    if q < abs(delta) or q == 0:
        return None
    rough = (norm.isf(alpha / 2) + norm.isf(.10)) ** 2 * q / delta ** 2
    max_n = max(100, int(rough * 1.6) + 100)
    reject = conditional_rejection(q, delta, alpha, max_n)
    out = {}
    for start in range(1, max_n + 1, 128):
        ns = np.arange(start, min(start + 128, max_n + 1))
        lo = int(binom.ppf(1e-12, ns.min(), q))
        hi = int(binom.isf(1e-12, ns.max(), q))
        ds = np.arange(lo, hi + 1)
        powers = binom.pmf(ds[None, :], ns[:, None], q) @ reject[ds]
        for target in (.8, .9):
            if str(target) not in out:
                matches = np.flatnonzero(powers >= target)
                if len(matches):
                    ix = matches[0]
                    out[str(target)] = {'n': int(ns[ix]), 'power': float(powers[ix])}
        if len(out) == 2:
            return out
    raise RuntimeError((q, delta, alpha, max_n))


def read_pilot(results_path: Path):
    """Use all-rollout primary results, not complete-case sensitivity rows."""
    document = json.loads(results_path.read_text())
    if document.get('independent_unit') != 'rollout':
        raise ValueError('Input statistics must use rollout as the independent unit')
    rounds = document['n_rounds_per_rollout']
    if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 2:
        raise ValueError('Need at least two rounds per rollout')
    source_rows = document['results']
    if not source_rows:
        raise ValueError('Input contains no primary results')
    result = []
    seen = set()
    for row in source_rows:
        key = row['model'], row['setting']
        if key in seen:
            raise ValueError(f'Duplicate model/setting: {key}')
        seen.add(key)
        n, gains, losses, discordant = (row[k] for k in ('n', 'endpoint_gains', 'endpoint_losses', 'endpoint_discordant'))
        if any(isinstance(x, bool) or not isinstance(x, int) for x in (n, gains, losses, discordant)):
            raise ValueError('Paired counts must be integers')
        if n < 2 or min(gains, losses) < 0 or gains + losses != discordant or not 0 <= discordant <= n:
            raise ValueError('Invalid paired counts')
        if any(row.get(k, n) != n for k in ('endpoint_n', 'trend_n')):
            raise ValueError('Endpoint and trend must use the full primary sample')
        p1, p5, sd = row['r1_accuracy'], row['r5_accuracy'], row['trend_sd']
        if not all(math.isfinite(x) for x in (p1, p5, sd)) or not 0 <= p1 <= 1 or not 0 <= p5 <= 1 or sd < 0:
            raise ValueError('Invalid accuracy or slope variance')
        if not math.isclose(p5 - p1, (gains - losses) / n, abs_tol=1e-12):
            raise ValueError('Endpoint accuracies do not match paired counts')
        qlo, qhi = proportion_confint(discordant, n, method='wilson')
        result.append({
            'model': row['model'], 'setting': row['setting'], 'pilot_n': n,
            'r1_accuracy': p1, 'r5_accuracy': p5,
            'improved': gains, 'worsened': losses, 'discordance_q': discordant / n,
            'discordance_q_wilson_95_low': float(qlo),
            'discordance_q_wilson_95_high': float(qhi),
            'per_rollout_slope_sd': sd,
        })
    return result, rounds


def write_csv(out, name, rows):
    if not rows:
        return
    with (out / name).open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def equivalence_sample_size(q, margin, comparisons, power):
    """Normal TOST approximation at zero change; validate before a launch."""
    return math.ceil(q * (norm.isf(.05 / comparisons) + norm.ppf((1 + power) / 2)) ** 2 / margin ** 2)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, required=True, help='Primary statistical-results.json')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--effects', type=float, nargs='+', default=DEFAULT_EFFECTS)
    parser.add_argument('--family-tests', type=int, nargs='+', default=DEFAULT_FAMILIES)
    parser.add_argument('--sensitivity-q', type=float, nargs='+', default=DEFAULT_SENSITIVITY_Q)
    parser.add_argument('--sample-n', type=int, nargs='+', default=[], help='Also calculate exact power at these fixed sample sizes')
    args = parser.parse_args(argv)
    if any(not math.isfinite(d) or not 0 < d <= 1 for d in args.effects):
        parser.error('--effects must be finite positive changes no greater than 1')
    if any(c < 1 for c in args.family_tests) or any(n < 1 for n in args.sample_n):
        parser.error('--family-tests and --sample-n must be positive integers')
    if any(not math.isfinite(q) or not 0 < q <= 1 for q in args.sensitivity_q):
        parser.error('--sensitivity-q must be in (0,1]')
    pilot, rounds = read_pilot(args.results)
    args.out.mkdir(parents=True, exist_ok=True)
    write_csv(args.out, 'power-pilot-nuisance.csv', pilot)
    cache = {}
    grid = []
    # q=.5 is a practical sensitivity case, not a universal worst case; q=1
    # is the upper-bound discordance stress case.
    qs = sorted({r['discordance_q'] for r in pilot} | set(args.sensitivity_q))
    fixed = []
    for q in qs:
        if q == 0:
            continue
        for delta in args.effects:
            for comparisons in args.family_tests:
                alpha = .05 / comparisons
                sizes = sample_sizes(q, delta, alpha)
                cache[(q, delta, comparisons)] = sizes
                if sizes is not None:
                    for n in args.sample_n:
                        fixed.append({'discordance_q': q, 'planned_r5_minus_r1': delta,
                                      'family_tests': comparisons, 'test_alpha': alpha,
                                      'fresh_rollouts_per_model_setting': n,
                                      'exact_power': exact_power(n, q, delta, alpha)})
                    for power, values in sizes.items():
                        grid.append({'discordance_q': q, 'planned_r5_minus_r1': delta,
                                     'family_tests': comparisons, 'test_alpha': alpha,
                                     'target_power': float(power),
                                     'fresh_rollouts_per_model_setting': values['n'],
                                     'exact_power': values['power']})
        print('Finished q=', q, flush=True)
    write_csv(args.out, 'power-sensitivity-grid.csv', grid)
    write_csv(args.out, 'power-fixed-n.csv', fixed)
    tailored = []
    for row in pilot:
        q = row['discordance_q']
        p1 = row['r1_accuracy']
        for delta in args.effects:
            qmax = min(2 * p1 + delta, 2 - 2 * p1 - delta)
            feasible = q >= delta and p1 + delta <= 1 and q <= qmax + 1e-12
            for comparisons in args.family_tests:
                values = cache.get((q, delta, comparisons)) if feasible else None
                for power in (.8, .9):
                    n = values[str(power)]['n'] if values else None
                    tailored.append({
                        'model': row['model'], 'setting': row['setting'],
                        'pilot_discordance_q': q, 'pilot_r1_accuracy': p1,
                        'planned_r5_minus_r1': delta, 'family_tests': comparisons,
                        'target_power': power, 'fresh_rollouts_per_model_setting': n,
                        'assumption_feasible': feasible,
                        'infeasible_note': '' if feasible else 'Pilot p1/q cannot support this positive change; choose a feasible alternative, not zero sample size.',
                    })
    write_csv(args.out, 'power-by-model-setting.csv', tailored)
    slope_rows = []
    for row in pilot:
        sd = row['per_rollout_slope_sd']
        for delta in args.effects:
            for comparisons in args.family_tests:
                for power in (.8, .9):
                    n = math.ceil(((norm.isf(.05 / comparisons / 2) + norm.ppf(power)) * sd / (delta / (rounds - 1))) ** 2)
                    slope_rows.append({'model': row['model'], 'setting': row['setting'],
                                       'pilot_per_rollout_slope_sd': sd,
                                       'planned_linear_r5_minus_r1': delta,
                                       'family_tests': comparisons, 'target_power': power,
                                       'normal_approx_fresh_n': n,
                                       'caution': 'Asymptotic nuisance-SD planning only; validate a feasible full binary trajectory model; do not use near-ceiling or tiny estimates as launch targets.'})
    write_csv(args.out, 'power-linear-trend-approximation.csv', slope_rows)
    equiv = []
    for q in sorted(set(args.sensitivity_q)):
        for margin in args.effects:
            for comparisons in args.family_tests:
                for power in (.8, .9):
                    n = equivalence_sample_size(q, margin, comparisons, power)
                    equiv.append({'discordance_q': q, 'equivalence_margin': margin,
                                  'family_tests': comparisons, 'target_power': power,
                                  'normal_approx_fresh_n_at_true_zero_change': n})
    write_csv(args.out, 'power-equivalence-approximation.csv', equiv)
    (args.out / 'power-method.json').write_text(json.dumps({
        'input_results': str(args.results.resolve()),
        'input_results_sha256': hashlib.sha256(args.results.read_bytes()).hexdigest(),
        'planner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'n_rounds_per_rollout': rounds,
        'planned_effects': args.effects,
        'family_tests': args.family_tests,
        'sensitivity_q': args.sensitivity_q,
        'fixed_sample_sizes': args.sample_n,
        'estimand': 'P(correct at R5) - P(correct at R1)',
        'test': 'Two-sided exact conditional McNemar; unconditional prospective power',
        'sampling_unit': f'Independent rollout, {rounds} correlated binary round outcomes',
        'fresh_sample': True,
        'pilot_use': 'Estimate discordance and slope standard deviation only; target effects specified separately by --effects',
        'q': 'P(incorrect R1,correct R5)+P(correct R1,incorrect R5)',
        'delta': 'P(incorrect R1,correct R5)-P(correct R1,incorrect R5)',
        'power_formula': 'Sum_D BinomialPMF(D;N,q) * P(two-sided exact Binomial(D,.5) rejects, with B~Binomial(D,(q+delta)/(2q)))',
        'numerical_tail_mass_bound': 2e-12,
        'sample_size_search': 'Every integer N from1 until first90% crossing, in vectorized blocks',
        'multiplicity': 'Bonferroni .05 / family_tests; plan conservatively even if final test uses Holm',
        'linear_trend': 'Per-rollout OLS slope b_i; N approx [(z_(1-alpha/2)+z_power)*SD(b_i)/(delta/(rounds-1))]^2',
        'equivalence': 'TOST normal approximation at true zero change: N approx q*[z_(1-alpha)+z_((1+power)/2)]^2 / margin^2. Not evidence of equivalence in this pilot.',
        'cautions': ['No sampling until significance; lock N and outcomes before new runs.',
                     'Pilot nuisance estimates are uncertain at N=50; use sensitivity ranges.',
                     'q=.5 is a practical sensitivity case; q=1 is the maximum-discordance stress case.',
                     'Endpoint improvement does not imply learned communication; task state, prior counters, and time effects can change outcomes.',
                     'A pilot accuracy estimate of 100% does not establish a population ceiling; positive improvement is infeasible if that point estimate is held fixed for planning.',
                     'These tables are fresh confirmatory rollouts; do not subtract the pilot sample.',
                     'All primary rollouts are retained. Missing final choices are incorrect; recovered API errors can still lead to a correct recorded outcome.',
                     'Equivalence sizes use a normal approximation at true zero and need validation before a launch.'],
    }, indent=2) + '\n')
    print('Wrote', len(grid), 'exact-power plans and', len(tailored), 'pilot-based rows.', flush=True)


if __name__ == '__main__':
    main()
