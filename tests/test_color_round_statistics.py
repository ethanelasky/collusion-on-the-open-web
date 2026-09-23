"""Check repeated-measures inference against analytic, independent examples."""
from __future__ import annotations

import math

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("statsmodels")

from scripts.analyze_color_round_statistics import (  # noqa: E402
    adjust_tests,
    paired_endpoint,
    proportion_interval,
    summarize_panel,
    trend_summary,
)


def test_trend_uses_fifty_independent_rollouts_and_per_round_units():
    # Half the rollouts stay wrong. The other half improve in rounds 4 and 5.
    # The 25 nonzero rollout slopes are each 0.3; there are 50 units, not 250.
    y = np.array([[0, 0, 0, 0, 0]] * 25 + [[0, 0, 0, 1, 1]] * 25)
    result = trend_summary(y)
    assert result["n"] == 50
    assert result["df"] == 49
    assert result["slope"] == pytest.approx(0.15)
    assert result["sd"] == pytest.approx(math.sqrt(50 * 0.15**2 / 49))
    assert result["t"] == pytest.approx(7)
    assert result["nonzero_slopes"] == 25
    assert result["ci_low"] < result["slope"] < result["ci_high"]
    # Changing rollout order must leave the inference unchanged.
    shuffled = trend_summary(y[np.random.default_rng(123).permutation(50)])
    for field in ("slope", "sd", "t", "p", "ci_low", "ci_high"):
        assert shuffled[field] == pytest.approx(result[field])


def test_exact_mcnemar_uses_discordant_pairs_instead_of_independent_counts():
    # This reproduces the independently audited Sol async endpoint counts.
    first = np.array([0] * 13 + [1] + [0] * 36)
    last = np.array([1] * 13 + [0] + [0] * 36)
    result = paired_endpoint(first, last)
    assert result["n"] == 50
    assert (result["gains"], result["losses"], result["discordant"]) == (13, 1, 14)
    assert result["delta"] == pytest.approx(0.24)
    assert result["p"] == pytest.approx(2 * (1 + 14) / 2**14)
    # An independent pure-Python beta inversion gives these conservative bounds.
    assert result["ci_low"] == pytest.approx(0.01277, abs=0.00001)
    assert result["ci_high"] == pytest.approx(0.42320, abs=0.00001)


@pytest.mark.parametrize("value", [0, 1])
def test_no_discordant_pairs_do_not_give_a_zero_width_interval(value):
    result = paired_endpoint([value] * 50, [value] * 50)
    # Each gain/loss probability gets a 97.5% exact binomial interval.
    radius = 1 - 0.0125 ** (1 / 50)
    assert result["delta"] == 0
    assert result["p"] == 1
    assert result["ci_low"] == pytest.approx(-radius)
    assert result["ci_high"] == pytest.approx(radius)
    assert radius > 0.08


def test_endpoint_reversal_changes_direction_but_not_two_sided_evidence():
    first = np.array([0] * 13 + [1] + [0] * 36)
    last = np.array([1] * 13 + [0] + [0] * 36)
    forward = paired_endpoint(first, last)
    backward = paired_endpoint(last, first)
    assert backward["delta"] == -forward["delta"]
    assert backward["p"] == forward["p"]
    assert backward["ci_low"] == pytest.approx(-forward["ci_high"])
    assert backward["ci_high"] == pytest.approx(-forward["ci_low"])


def test_any_round_test_detects_a_dip_when_endpoints_and_linear_trend_agree():
    y = np.array([[1, 1, 0, 1, 1]] * 22 + [[1, 1, 1, 1, 1]] * 28)
    result, pairs = summarize_panel(y, model="offline", setting="sync_counter")
    assert result["endpoint_delta"] == 0
    assert result["endpoint_p"] == 1
    assert result["trend_slope"] == 0
    assert len(pairs) == result["omnibus_pair_count"] == 10
    assert result["omnibus_min_pair_p"] == pytest.approx(2 / 2**22)
    assert result["omnibus_p"] == pytest.approx(10 * 2 / 2**22)
    # The API preserves all ten pairs, including their direction and sample size.
    pair = next(p for p in pairs if (p["round_a"], p["round_b"]) == (1, 3))
    assert (pair["n"], pair["gains"], pair["losses"]) == (50, 0, 22)


def test_holm_families_are_separate_from_the_combined_63_test_family():
    rows = [{"trend_p": 1.0, "endpoint_p": 1.0, "omnibus_p": 1.0}
            for _ in range(21)]
    endpoint_p = 30 / 2**14
    trend_p = 0.002728757
    omnibus_p = 20 / 2**22
    rows[0].update(trend_p=trend_p, endpoint_p=endpoint_p)
    rows[1]["omnibus_p"] = omnibus_p
    adjust_tests(rows)
    assert rows[0]["endpoint_holm21"] == pytest.approx(21 * endpoint_p)
    assert rows[0]["trend_holm21"] == pytest.approx(21 * trend_p)
    assert rows[1]["omnibus_holm21"] == pytest.approx(21 * omnibus_p)
    assert rows[1]["omnibus_holm63"] == pytest.approx(63 * omnibus_p)
    assert rows[0]["endpoint_holm63"] == pytest.approx(62 * endpoint_p)
    assert rows[0]["trend_holm63"] == pytest.approx(61 * trend_p)
    assert rows[0]["endpoint_holm21"] < 0.05 < rows[0]["endpoint_holm63"]
    assert rows[0]["trend_holm21"] > 0.05
    for row in rows[2:]:
        assert all(row[f"{family}_holm{count}"] == 1
                   for family in ("trend", "endpoint", "omnibus") for count in (21, 63))


@pytest.mark.parametrize("row", [[1, 1, 1, 1, 1], [0, 0, 0, 1, 1]])
def test_identical_slopes_are_marked_degenerate_even_with_rounding_noise(row):
    result = trend_summary(np.tile(row, (50, 1)))
    assert result["degenerate"]
    assert result["ci_low"] is None
    assert result["ci_high"] is None
    assert result["t"] is None
    assert result["p"] == 1


def test_per_round_wilson_intervals_remain_nonzero_at_the_boundary():
    low, high = proportion_interval(50, 50)
    assert low == pytest.approx(0.9286524, abs=0.0000001)
    assert high == 1
    low0, high0 = proportion_interval(0, 50)
    assert low0 == 0
    assert high0 == pytest.approx(1 - low)


@pytest.mark.parametrize("first,last", [
    ([], []), ([0], [0, 1]), ([[0, 1]], [[1, 0]]),
    ([0, 0.9], [0, 1]), ([0, np.nan], [0, 1]), ([0, 2], [0, 1]),
])
def test_endpoint_rejects_invalid_or_unpaired_observations(first, last):
    with pytest.raises(ValueError):
        paired_endpoint(first, last)


@pytest.mark.parametrize("y", [
    [], [0, 1], [[0, 1]], [[0], [1]],
    [[0, 0.9], [1, 0]], [[0, np.nan], [1, 0]], [[0, 2], [1, 0]],
])
def test_trend_rejects_invalid_observations(y):
    with pytest.raises(ValueError):
        trend_summary(y)


@pytest.mark.parametrize("bad", [0.9, np.nan, 2])
def test_panel_summary_validates_binary_values_before_type_conversion(bad):
    y = np.zeros((50, 5), dtype=float)
    y[0, 0] = bad
    with pytest.raises(ValueError):
        summarize_panel(y, model="offline", setting="async_counter")
