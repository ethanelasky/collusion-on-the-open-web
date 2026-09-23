"""Independent exact checks for all-five-round practical equivalence."""
from __future__ import annotations

import math

import pytest

pytest.importorskip("scipy")
pytest.importorskip("statsmodels")

from scripts.analyze_color_stability import (  # noqa: E402
    ROUND_PAIRS,
    adjust_equivalence,
    equivalence_p,
    paired_bounds,
    panel_stability,
    summarize_stability,
    validate_counts,
    validate_input,
)


def panel_from_trajectories(y):
    n = len(y)
    return {"model": "offline", "setting": "async_counter", "n": n,
            "points": [{"accuracy": sum(row[r] for row in y)/n} for r in range(5)],
            "pairs": [{"round_a": a, "round_b": b, "n": n,
                       "gains": sum(row[a-1] < row[b-1] for row in y),
                       "losses": sum(row[a-1] > row[b-1] for row in y)}
                      for a, b in ROUND_PAIRS]}


@pytest.mark.parametrize("margin", [.05, .10, .20])
def test_zero_discordance_uses_one_sided_tail_allocation(margin):
    assert equivalence_p(0, 0, 50, margin) == pytest.approx(min(1., 2*(1-margin)**50), abs=1e-12)
    low, high = paired_bounds(0, 0, 50, .05)
    radius = 1 - .025**(1/50)
    assert (low, high) == pytest.approx((-radius, radius))
    assert radius > 0


@pytest.mark.parametrize("margin", [.10, .20])
def test_one_loss_reproduces_known_gemini_pair_p_without_normal_approximation(margin):
    exact = 2*((1-margin)**50 + 50*margin*(1-margin)**49)
    assert equivalence_p(0, 1, 50, margin) == pytest.approx(exact, abs=1e-12)
    assert equivalence_p(1, 0, 50, margin) == pytest.approx(exact, abs=1e-12)


def test_full_round_range_cannot_ignore_a_middle_round_drop():
    panel = panel_from_trajectories([[1, 1, 0, 1, 1]] * 22 + [[1, 1, 1, 1, 1]] * 28)
    row, tests, pairs = panel_stability(panel, margins=(.10,))
    assert row["n"] == 50
    assert row["observed_accuracy_range"] == pytest.approx(.44)
    assert row["range_upper95"] >= .44
    assert row["range_upper95_bonferroni21"] >= row["range_upper95"]
    assert tests[0]["stability_p"] == max(p["equivalence_p"] for p in pairs)
    assert tests[0]["stability_p"] > .05
    endpoints = next(p for p in pairs if (p["round_a"], p["round_b"]) == (1, 5))
    assert endpoints["equivalence_p"] < .05


def test_trajectory_helper_retains_pairing_and_confidence_level():
    y = [[1, 1, 0, 1, 1]] * 22 + [[1, 1, 1, 1, 1]] * 28
    result = summarize_stability(y)
    panel, tests, _ = panel_stability(panel_from_trajectories(y), margins=(.10,))
    assert result["n"] == 50
    assert result["observed_range"] == panel["observed_accuracy_range"]
    assert result["range_upper95"] == panel["range_upper95"]
    assert result["p"] == tests[0]["stability_p"]
    family = summarize_stability(y, alpha=.025)
    assert family["range_upper"] >= result["range_upper95"]
    assert family["range_upper95"] is None
    assert family["range_confidence_level"] == .975
    assert family["p"] == result["p"]


@pytest.mark.parametrize("y", [[], [[0, 1]], [[0, 1, 0, 1, .9]], [[0, 1, 0, 1, math.nan]]])
def test_trajectory_helper_rejects_invalid_observations(y):
    with pytest.raises(ValueError):
        summarize_stability(y)


def test_intersection_union_does_not_add_a_ten_pair_penalty():
    row, tests, pairs = panel_stability(panel_from_trajectories([[1]*5]*50), margins=(.10,))
    exact = 2*.9**50
    assert len(pairs) == 10
    assert tests[0]["stability_p"] == pytest.approx(exact)
    assert row["range_upper95"] == pytest.approx(1-.025**(1/50))
    assert row["range_upper95_bonferroni21"] == pytest.approx(1-(.05/42)**(1/50))


def test_holm_corrects_twenty_one_panel_claims_per_margin():
    rows = [{"model": f"offline{i}", "setting": "async_counter", "margin": .20,
             "stability_p": 1.} for i in range(21)]
    rows[0]["stability_p"] = 2*(.8**50 + 10*.8**49)
    rows[1]["stability_p"] = .0113127224019486
    adjust_equivalence(rows)
    assert rows[0]["stability_holm21"] == pytest.approx(21*rows[0]["stability_p"])
    assert rows[0]["stable_holm21"]
    assert rows[1]["stability_holm21"] == pytest.approx(20*rows[1]["stability_p"])
    assert not rows[1]["stable_holm21"]


@pytest.mark.parametrize("gains,losses,n", [(0, 0, 0), (2, 2, 3), (-1, 0, 50),
                                           (0.5, 0, 50), (True, 0, 50), (0, 0, 50.5)])
def test_invalid_counts_are_rejected(gains, losses, n):
    with pytest.raises(ValueError):
        validate_counts(gains, losses, n)
    # A previously cached integer result must not let bool/float counts bypass validation.
    paired_bounds(1, 0, 50, .05)
    with pytest.raises(ValueError):
        paired_bounds(gains, losses, n, .05)
    with pytest.raises(ValueError):
        equivalence_p(gains, losses, n, .10)


@pytest.mark.parametrize("margin", [0, -0.1, 1.1, math.nan, math.inf])
def test_invalid_margins_are_rejected(margin):
    with pytest.raises(ValueError):
        equivalence_p(0, 0, 50, margin)


def test_incomplete_input_cannot_be_reported_as_a_full_family():
    with pytest.raises(ValueError, match="seven models"):
        validate_input({"results": [], "points": [], "pairs": []})
    with pytest.raises(ValueError, match="all 21"):
        adjust_equivalence([{"model": "offline", "setting": "async_counter",
                             "margin": .10, "stability_p": .01}])
