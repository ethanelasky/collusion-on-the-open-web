"""Validate prospective paired power without data files or model API calls."""
from __future__ import annotations

import csv
import hashlib
import json
import math

import pytest

pytest.importorskip('numpy')
pytest.importorskip('scipy')
pytest.importorskip('statsmodels')

from scripts.plan_color_round_power import (  # noqa: E402
    DEFAULT_SENSITIVITY_Q,
    equivalence_sample_size,
    exact_power,
    main,
    read_pilot,
    sample_sizes,
)


def enumerated_power(n, q, delta, alpha):
    """Independent multinomial enumeration of gains, losses, and no change."""
    gain_probability = (q + delta) / 2
    loss_probability = (q - delta) / 2
    power = 0.
    for gains in range(n + 1):
        for losses in range(n - gains + 1):
            d = gains + losses
            # Under the null the two directional counts are equiprobable.
            p = min(1., 2 * sum(math.comb(d, j) for j in range(min(gains, losses) + 1)) / 2 ** d)
            if p <= alpha:
                unchanged = n - d
                coefficient = math.comb(n, gains) * math.comb(n - gains, losses)
                power += coefficient * gain_probability ** gains * loss_probability ** losses * (1 - q) ** unchanged
    return power


@pytest.mark.parametrize('q,delta', [(.4, .1), (.4, -.1), (.2, .2), (1., .2)])
@pytest.mark.parametrize('alpha', [.05, .025, .05 / 21])
def test_exact_power_matches_independent_multinomial_enumeration(q, delta, alpha):
    assert exact_power(15, q, delta, alpha) == pytest.approx(enumerated_power(15, q, delta, alpha), abs=1e-13)


@pytest.mark.parametrize('q', [0., .1, .5, 1.])
@pytest.mark.parametrize('n', [0, 15, 100])
def test_exact_null_rejection_does_not_exceed_alpha(q, n):
    assert exact_power(n, q, 0., .05) <= .05 + 1e-12


def test_sample_size_search_finds_first_crossing_and_respects_discreteness():
    sizes = sample_sizes(.2, .2, .05)
    for target in (.8, .9):
        n = sizes[str(target)]['n']
        assert exact_power(n, .2, .2, .05) >= target
        assert all(exact_power(smaller, .2, .2, .05) < target for smaller in range(n))


def test_sample_sizes_do_not_treat_infeasible_or_zero_effects_as_zero_cost():
    assert sample_sizes(.1, .2, .05) is None
    assert sample_sizes(0., .1, .05) is None
    with pytest.raises(ValueError, match='nonzero'):
        sample_sizes(.4, 0., .05)


@pytest.mark.parametrize('args', [(10, .4, .5, .05), (10, -.2, .1, .05),
                                  (10, .4, .1, 0.), (1.5, .4, .1, .05),
                                  (10, float('nan'), .1, .05)])
def test_invalid_power_parameters_fail(args):
    with pytest.raises(ValueError):
        exact_power(*args)


def example_primary_results():
    return {
        'independent_unit': 'rollout', 'n_rounds_per_rollout': 5,
        'results': [{'model': 'offline', 'setting': 'async_counter', 'n': 50,
                     'endpoint_n': 50, 'trend_n': 50,
                     'endpoint_gains': 8, 'endpoint_losses': 2,
                     'endpoint_discordant': 10, 'r1_accuracy': .4,
                     'r5_accuracy': .52, 'trend_sd': .12}],
        # A sensitivity result must never replace the full primary denominator.
        'complete_case': [{'n': 4, 'endpoint_discordant': 4}],
    }


def test_load_uses_primary_discordance_and_rollout_slope_sd(tmp_path):
    path = tmp_path / 'results.json'
    path.write_text(json.dumps(example_primary_results()))
    pilot, rounds = read_pilot(path)
    assert rounds == 5
    assert pilot[0]['pilot_n'] == 50
    assert pilot[0]['discordance_q'] == .2
    assert pilot[0]['per_rollout_slope_sd'] == .12
    assert pilot[0]['discordance_q_wilson_95_low'] < .2 < pilot[0]['discordance_q_wilson_95_high']


def test_load_rejects_inconsistent_paired_counts(tmp_path):
    document = example_primary_results()
    document['results'][0]['endpoint_discordant'] = 11
    path = tmp_path / 'results.json'
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match='paired counts'):
        read_pilot(path)


def test_cli_writes_reproducible_plans_and_q_point_six_equivalence(tmp_path):
    path, out = tmp_path / 'input.json', tmp_path / 'output'
    path.write_text(json.dumps(example_primary_results()))
    assert .6 in DEFAULT_SENSITIVITY_Q
    main(['--results', str(path), '--out', str(out), '--effects', '.2',
          '--family-tests', '1', '--sensitivity-q', '.6', '--sample-n', '30'])
    metadata = json.loads((out / 'power-method.json').read_text())
    assert metadata['input_results_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert metadata['fixed_sample_sizes'] == [30]
    assert metadata['fresh_sample'] is True
    with (out / 'power-equivalence-approximation.csv').open() as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    assert {r['discordance_q'] for r in rows} == {'0.6'}
    with (out / 'power-fixed-n.csv').open() as handle:
        fixed = list(csv.DictReader(handle))
    assert len(fixed) == 2  # Pilot q=.2 and sensitivity q=.6.
    for row in fixed:
        assert float(row['exact_power']) == pytest.approx(exact_power(30, float(row['discordance_q']), .2, .05))


def test_equivalence_size_is_explicitly_normal_approximate_and_includes_q_point_six():
    assert equivalence_sample_size(.6, .1, 2, .8) == 631
    assert equivalence_sample_size(.6, .1, 2, .9) == 780
