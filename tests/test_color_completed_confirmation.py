"""Check fixed-sample early release without weakening the full-study gate."""
from __future__ import annotations

import hashlib
import json

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("statsmodels")

from test_color_efficient_confirmation import (  # noqa: E402
    add_original_plan, amend, build_batch, campaign_path, read,
    statistical_panels, trajectory_path,
)
from scripts.analyze_color_efficient_confirmation import (  # noqa: E402
    CampaignNotReady, analyze_panels, load_confirmation,
)
from scripts import analyze_color_completed_confirmation as completed  # noqa: E402

COMPLETED = ("sol", "astra", "luna")


def make_other_models_pending(root):
    for name in ("deepseek", "glm"):
        def pending(campaign):
            campaign.update(status="running")
            campaign["outcomes"][0].update(status="queued")
        amend(campaign_path(root, name), pending)


def subset_panels(n=100):
    plan, panels = statistical_panels(n)
    return plan, [p for p in panels if p["model"] in COMPLETED]


def test_explicit_whitelist_keeps_full_n_and_does_not_read_pending_models(tmp_path):
    path = build_batch(tmp_path)
    make_other_models_pending(tmp_path)
    # A malformed trajectory outside this release must not be read or scored.
    amend(trajectory_path(tmp_path, "deepseek"), lambda r: r.update(rounds=[{"round_index": 99}]))
    plan, panels, rows, sources = load_confirmation(path, selected_models=COMPLETED)
    assert len(plan["models"]) == 5
    assert [p["model"] for p in panels] == list(COMPLETED)
    assert [p["y"].shape for p in panels] == [(8, 5), (10, 5), (11, 5)]
    assert len(rows) == (8+10+11)*5
    assert {r["model"] for r in rows} == set(COMPLETED)
    assert str(campaign_path(tmp_path, "deepseek").resolve()) not in sources
    # Pilot separation remains checked for all five planned models.
    assert str((tmp_path / "pilots" / "deepseek" / "campaign.json").resolve()) in sources
    with pytest.raises(CampaignNotReady):
        load_confirmation(path)


def test_requested_model_must_finish_before_any_requested_trajectory_is_scored(tmp_path):
    path = build_batch(tmp_path)
    amend(trajectory_path(tmp_path, "sol"), lambda r: r.update(rounds=[{"round_index": 99}]))
    amend(campaign_path(tmp_path, "astra"), lambda c: c.update(status="running"))
    with pytest.raises(CampaignNotReady):
        load_confirmation(path, selected_models=COMPLETED)


def test_original_receipts_outside_whitelist_are_still_verified(tmp_path):
    path = build_batch(tmp_path)
    original = add_original_plan(tmp_path, path)  # Sol's original receipt.
    amend(original, lambda p: p.update(seed=999))
    with pytest.raises(ValueError, match="hash|frozen"):
        load_confirmation(path, selected_models=("astra", "luna"))


def test_pilot_overlap_outside_whitelist_still_fails(tmp_path):
    path = build_batch(tmp_path)
    glm = next(s for s in read(path)["models"] if s["name"] == "glm")
    amend(tmp_path / "pilots" / "glm" / "campaign.json",
          lambda p: p["base_config"].update(seed=glm["seed"]))
    with pytest.raises(ValueError, match="overlap"):
        load_confirmation(path, selected_models=COMPLETED)


@pytest.mark.parametrize("models", [(), ("sol", "sol"), ("unknown",), ("gemini",), "sol"])
def test_whitelist_must_name_distinct_selected_models(tmp_path, models):
    path = build_batch(tmp_path)
    with pytest.raises(ValueError, match="selected_models"):
        load_confirmation(path, selected_models=models)


def test_default_final_inference_still_requires_all_five_panels():
    plan, panels = subset_panels()
    with pytest.raises(ValueError, match="all five"):
        analyze_panels(plan, panels)


def test_completed_release_uses_bonferroni_seven_not_provisional_holm():
    plan, panels = subset_panels()
    panels[0]["y"][:] = 0
    panels[0]["y"][:8, -1] = 1
    primary, pairs, points, candidates = completed.analyze_completed_panels(plan, panels, COMPLETED)
    by_model = {r["model"]: r for r in primary}
    assert by_model["sol"]["raw_p"] == 2/2**8
    assert by_model["sol"]["bonferroni7_p"] == 7*2/2**8
    assert by_model["sol"]["decision"] == "inconclusive"
    for name in ("astra", "luna"):
        assert by_model[name]["raw_p"] == pytest.approx(2*.9**100, abs=1e-12)
        assert by_model[name]["bonferroni7_p"] == pytest.approx(14*.9**100, abs=1e-11)
        assert by_model[name]["decision"] == "stable_within_margin"
        assert by_model[name]["range_upper_family95"] >= by_model[name]["range_upper95"]
        assert by_model[name]["endpoint_family95_low"] is None
    sol = by_model["sol"]
    assert sol["endpoint_family95_low"] <= sol["endpoint_ci95_low"]
    assert sol["endpoint_family95_high"] >= sol["endpoint_ci95_high"]
    assert len(pairs) == 20 and len(points) == 15 and len(candidates) == 7
    pending = [r for r in candidates if not r["analyzed_in_this_release"]]
    assert all(r["raw_p"] is None and r["adjustment_input_p"] == 1 and r["bonferroni7_p"] is None for r in pending)
    assert not any("holm" in key for r in primary for key in r)


def test_middle_dip_cannot_pass_whole_trajectory_stability():
    plan, panels = subset_panels()
    panels[1]["y"][:25, 2] = 0
    primary, _, _, _ = completed.analyze_completed_panels(plan, panels, COMPLETED)
    astra = next(r for r in primary if r["model"] == "astra")
    assert astra["endpoint_delta"] == 0
    assert astra["observed_range"] == .25
    assert astra["decision"] == "inconclusive"


@pytest.mark.parametrize("fault", ["sample_size", "nonbinary", "test"])
def test_completed_inference_rejects_changed_samples_or_tests(fault):
    plan, panels = subset_panels()
    if fault == "sample_size":
        panels[0]["y"] = panels[0]["y"][:-1]
    elif fault == "nonbinary":
        panels[0]["y"][0, 0] = 2
    else:
        panels[0]["primary_test"] = "all_rounds_cp_equivalence"
    with pytest.raises(ValueError, match="frozen"):
        completed.analyze_completed_panels(plan, panels, COMPLETED)


def test_dated_amendment_precedes_loading_and_no_results_for_pending_request(tmp_path):
    path = build_batch(tmp_path)
    amend(campaign_path(tmp_path, "astra"), lambda c: c.update(status="running"))
    out = tmp_path / "release"
    with pytest.raises(CampaignNotReady):
        completed.analyze(path, out, selected_models=COMPLETED,
                          expected_plan_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    amendment = read(out / "analysis-amendment.json")
    assert amendment["created_before_loading_trajectories"] is True
    assert amendment["models"] == list(COMPLETED)
    assert amendment["fixed_sample_sizes"] == {"sol": 8, "astra": 10, "luna": 11}
    assert not (out / "completed-confirmation-results.json").exists()
    assert not (out / "primary-results.csv").exists()


def test_bad_plan_hash_cannot_create_an_amendment(tmp_path):
    path = build_batch(tmp_path)
    out = tmp_path / "release"
    with pytest.raises(ValueError, match="hash"):
        completed.analyze(path, out, selected_models=COMPLETED, expected_plan_sha256="0"*64)
    assert not out.exists()


def test_amendment_cannot_switch_selected_models_after_creation(tmp_path):
    path = build_batch(tmp_path)
    out = tmp_path / "release"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    completed.write_amendment(path, out, COMPLETED, digest)
    with pytest.raises(ValueError, match="amendment"):
        completed.write_amendment(path, out, ("sol", "astra"), digest)


def test_end_to_end_release_keeps_pending_models_absent(tmp_path, monkeypatch):
    pytest.importorskip("matplotlib")
    path = build_batch(tmp_path)
    make_other_models_pending(tmp_path)
    out = tmp_path / "release"
    actual_loader = completed.load_confirmation
    def checked_loader(*args, **kwargs):
        assert (out / "analysis-amendment.json").exists()
        return actual_loader(*args, **kwargs)
    monkeypatch.setattr(completed, "load_confirmation", checked_loader)
    report = completed.analyze(path, out, selected_models=COMPLETED,
                               expected_plan_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    assert report["analyzed_rollouts"] == 29
    assert report["analyzed_rounds"] == 145
    assert report["full_plan_rollouts"] == 50
    assert report["not_final_holm"] is True
    assert report["amendment"]["created_utc"] <= report["created_utc"]
    assert len(report["primary"]) == 3
    for suffix in ("png", "pdf", "svg"):
        assert (out / f"accuracy-by-round-95ci.{suffix}").exists()
    assert "Bonferroni-7" in (out / "index.html").read_text()
