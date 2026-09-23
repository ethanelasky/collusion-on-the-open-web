"""Check fixed-batch stability reporting, gates, and interpretation offline."""
from __future__ import annotations

import json

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("statsmodels")

from scripts.analyze_color_confirmation import CampaignNotReady, load_confirmation  # noqa: E402
from scripts.analyze_color_stability import summarize_stability  # noqa: E402
from scripts.analyze_color_stability_confirmation import analyze, analyze_panels  # noqa: E402
from test_color_confirmation import build_batch, change_campaign, save  # noqa: E402


def build_stability_batch(root, n=20):
    path = build_batch(root, n=n)
    plan = json.loads(path.read_text())
    plan.update(primary_test="all_rounds_cp_equivalence", stability_margin=.10)
    save(path, plan)
    return path


def test_stability_primary_is_explicit_and_endpoint_default_is_unchanged(tmp_path):
    path = build_stability_batch(tmp_path)
    with pytest.raises(ValueError, match="primary_test"):
        load_confirmation(path)
    plan, panels, _, _ = load_confirmation(path, expected_primary_test="all_rounds_cp_equivalence")
    assert plan["primary_test"] == "all_rounds_cp_equivalence"
    assert len(panels) == 2


def test_holm_applies_to_two_global_stability_tests_without_factor_ten():
    y = np.ones((100, 5), dtype=int)
    panels = [{"model": model, "setting": "async_counter", "y": y, "failures": {}}
              for model in ("astra", "gemini")]
    results, pairs, points = analyze_panels(panels, margin=.10)
    expected = summarize_stability(y, margin=.10)
    assert len(pairs) == 20
    assert len(points) == 10
    for row in results:
        assert row["p"] == expected["p"]
        assert row["holm2_p"] == pytest.approx(2*expected["p"])
        assert row["decision"] == "stable_within_margin"
        assert row["range_upper95"] == expected["range_upper95"]


def test_matching_endpoints_with_a_middle_dip_does_not_pass_stability():
    flat = np.ones((300, 5), dtype=int)
    dip = flat.copy()
    dip[:75, 2] = 0
    panels = [{"model": model, "setting": "async_counter", "y": y, "failures": {}}
              for model, y in (("astra", flat), ("gemini", dip))]
    results, pairs, _ = analyze_panels(panels, margin=.10)
    assert results[0]["decision"] == "stable_within_margin"
    assert results[1]["r1_accuracy"] == results[1]["r5_accuracy"] == 1
    assert results[1]["observed_range"] == .25
    assert results[1]["decision"] == "inconclusive"
    model_pairs = [p for p in pairs if p["model"] == "gemini"]
    assert results[1]["p"] == max(p["p"] for p in model_pairs)


def test_small_flat_sample_can_be_inconclusive():
    y = np.ones((8, 5), dtype=int)
    panels = [{"model": model, "setting": "async_counter", "y": y, "failures": {}}
              for model in ("astra", "gemini")]
    results, _, _ = analyze_panels(panels, margin=.10)
    assert all(r["decision"] == "inconclusive" for r in results)
    assert all(r["observed_range"] == 0 and r["range_upper95"] > .10 for r in results)


def test_missing_failed_jobs_keep_the_whole_300_rollout_sample(tmp_path):
    path = build_stability_batch(tmp_path, n=300)
    def fail_first(c):
        c["status"] = "complete_with_errors"
        c["outcomes"][0].update(status="failed", artifact_paths={})
    change_campaign(tmp_path, "sol", fail_first)
    _, panels, long_rows, _ = load_confirmation(path, expected_primary_test="all_rounds_cp_equivalence")
    results, _, points = analyze_panels(panels, margin=.10)
    assert len(long_rows) == 3000
    assert results[0]["n"] == 300
    assert results[0]["overall_accuracy"] == 299/300
    assert results[0]["failed_rollouts"] == 1
    assert results[0]["absent_rounds"] == 5
    assert all(p["n"] == 300 for p in points)


def test_running_jobs_do_not_publish_stability_inference(tmp_path):
    path = build_stability_batch(tmp_path)
    change_campaign(tmp_path, "deepseek", lambda c: c["outcomes"][0].update(status="running"))
    out = tmp_path / "analysis"
    with pytest.raises(CampaignNotReady):
        analyze(path, out)
    assert not out.exists()


def test_plan_hash_change_prevents_stability_inference(tmp_path):
    path = build_stability_batch(tmp_path)
    out = tmp_path / "analysis"
    with pytest.raises(ValueError, match="frozen plan"):
        analyze(path, out, expected_plan_sha256="0"*64)
    assert not out.exists()


@pytest.mark.parametrize("margin", [None, 0, 1, -1, float("nan"), True])
def test_margin_must_be_fixed_and_valid(tmp_path, margin):
    path = build_stability_batch(tmp_path)
    plan = json.loads(path.read_text())
    plan["stability_margin"] = margin
    save(path, plan)
    with pytest.raises(ValueError, match="stability margin"):
        analyze(path, tmp_path / "analysis")


def test_report_separates_practical_stability_from_equality_and_success(tmp_path):
    pytest.importorskip("matplotlib")
    path = build_stability_batch(tmp_path, n=50)
    out = tmp_path / "analysis"
    report = analyze(path, out)
    assert report["planned_rollouts"] == 100
    assert report["planned_rounds"] == 500
    assert report["pilot_pooled"] is False
    assert report["margin"] == .10
    assert len(report["pairs"]) == 20
    assert len(report["points"]) == 10
    assert all(r["decision"] == "stable_within_margin" for r in report["results"])
    assert (out / "accuracy-by-round-95ci.png").read_bytes().startswith(b"\x89PNG")
    assert (out / "accuracy-by-round-95ci.pdf").read_bytes().startswith(b"%PDF")
    markdown = (out / "report.md").read_text()
    assert "does not establish exact equality" in markdown
    assert "inconclusive result does not establish change" in markdown
    assert "Stability also does not mean high accuracy" in markdown
    assert "Holm" in (out / "index.html").read_text()
    assert json.loads((out / "stability-results.json").read_text())["plan_sha256"] == report["plan_sha256"]
