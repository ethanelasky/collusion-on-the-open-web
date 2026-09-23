"""Offline checks for the fixed fresh confirmation analysis and failure policy."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("statsmodels")

from scripts.analyze_color_confirmation import (  # noqa: E402
    CampaignNotReady, analyze, analyze_panels, load_confirmation, validate_plan,
)


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def build_batch(root, n=8):
    plan = {"schema": "color-game-confirmation/v1", "models": [], "settings": ["async_counter"],
            "rollouts_per_model": n, "rounds": 5, "seed": 381, "start_index": 0,
            "primary_test": "exact_two_sided_mcnemar", "family_tests": 2}
    for model in ("sol", "deepseek"):
        plan["models"].append({"name": model, "directory": model})
        config = {"setting": "async_counter", "colors": ["red", "blue"], "rounds": 5,
                  "seed": 381, "rollout_index": 0, "actions_per_agent": 8}
        c = {"schema": "color-game-campaign/v1", "model": {"name": model}, "base_config": config,
             "rollouts_per_setting": n, "selected_settings": ["async_counter"], "status": "complete", "outcomes": []}
        for i in range(n):
            cfg = {**config, "rollout_index": i}
            trajectory = root / model / "runs" / str(i) / "rollout.json"
            c["outcomes"].append({"setting": "async_counter", "rollout_index": i, "config": cfg,
                                   "status": "complete", "artifact_paths": {"json": str(trajectory)},
                                   "plan": {"assigned_colors": ["red"]*5}})
            save(trajectory, {"rollout_id": f"{model}-{i}", "status": "complete", "config": cfg, "rounds": [
                {"round_index": r, "assigned_color": "red", "alice_color": "red", "bob_color": "red",
                 "match": True, "valid_for_analysis": True, "actions": []} for r in range(5)]})
        save(root / model / "runs" / "campaign.json", c)
    path = root / "confirmation-plan.json"
    save(path, plan)
    return path


def change_campaign(root, model, update):
    path = root / model / "runs" / "campaign.json"
    campaign = json.loads(path.read_text())
    update(campaign)
    save(path, campaign)


def change_trajectory(root, model, index, update):
    path = root / model / "runs" / str(index) / "rollout.json"
    rollout = json.loads(path.read_text())
    update(rollout)
    save(path, rollout)


def test_missing_failed_job_and_partial_rounds_keep_full_200_denominator(tmp_path):
    path = build_batch(tmp_path, n=200)
    def fail_first(c):
        c["status"] = "complete_with_errors"
        c["outcomes"][0].update(status="failed", artifact_paths={})
        c["outcomes"][1].update(status="interrupted")
    change_campaign(tmp_path, "sol", fail_first)
    change_trajectory(tmp_path, "sol", 1, lambda r: r.update(status="interrupted", rounds=r["rounds"][:2]))
    _, panels, rows, sources = load_confirmation(path)
    sol = panels[0]
    assert sol["y"].shape == (200, 5)
    assert sol["y"].sum() == 992
    assert sol["failures"]["failed_rollouts"] == 1
    assert sol["failures"]["interrupted_rollouts"] == 1
    assert sol["failures"]["missing_artifact_rollouts"] == 1
    assert sol["failures"]["absent_rounds"] == sol["failures"]["missing_final_rounds"] == 8
    primary, _, points = analyze_panels(panels)
    assert primary[0]["n"] == 200
    assert primary[0]["r1_accuracy"] == 199/200
    assert primary[0]["r5_accuracy"] == 198/200
    assert all(p["n"] == 200 for p in points)
    assert len(rows) == 2000
    assert str(path.resolve()) in sources


@pytest.mark.parametrize("status", ["queued", "running", "unknown"])
def test_nonterminal_jobs_block_all_inference(tmp_path, status):
    path = build_batch(tmp_path)
    # Check the entire batch before reading completed trajectories.
    change_trajectory(tmp_path, "sol", 0, lambda r: r.update(rounds=[{"round_index": 99}]))
    change_campaign(tmp_path, "deepseek", lambda c: c["outcomes"][0].update(status=status))
    out = tmp_path / "analysis"
    with pytest.raises(CampaignNotReady):
        analyze(path, out)
    assert not out.exists()


def test_terminal_campaign_with_queued_jobs_still_blocks(tmp_path):
    path = build_batch(tmp_path)
    def interrupt(c):
        c["status"] = "interrupted"
        c["outcomes"][0]["status"] = "queued"
    change_campaign(tmp_path, "sol", interrupt)
    with pytest.raises(CampaignNotReady, match="not terminal"):
        load_confirmation(path)


def test_recovered_api_error_retains_correct_answer_and_counts_error(tmp_path):
    path = build_batch(tmp_path)
    def api_error(r):
        r["rounds"][0].update(valid_for_analysis=False,
                              actions=[{"error": {"category": "model_api"}}, {"error": None}])
    change_trajectory(tmp_path, "sol", 0, api_error)
    _, panels, _, _ = load_confirmation(path)
    assert panels[0]["y"][0, 0] == 1
    assert panels[0]["failures"]["api_error_actions"] == 1
    assert panels[0]["failures"]["api_error_rounds"] == 1
    assert panels[0]["failures"]["invalid_analysis_rounds"] == 1


@pytest.mark.parametrize("field,value", [("seed", 99), ("rounds", 4), ("setting", "sync_counter"), ("rollout_index", 9)])
def test_rejects_campaign_config_drift(tmp_path, field, value):
    path = build_batch(tmp_path)
    change_campaign(tmp_path, "sol", lambda c: c["base_config"].update({field: value}))
    with pytest.raises(ValueError, match="differs from the plan"):
        load_confirmation(path)


def test_rejects_model_config_drift_from_frozen_plan(tmp_path):
    path = build_batch(tmp_path)
    plan = json.loads(path.read_text())
    plan["models"][0]["model_config"] = {"effort": "high"}
    save(path, plan)
    with pytest.raises(ValueError, match="Model.*effort"):
        load_confirmation(path)


def test_rejects_duplicate_rollouts(tmp_path):
    path = build_batch(tmp_path)
    change_campaign(tmp_path, "sol", lambda c: c["outcomes"][1].update(rollout_index=0))
    with pytest.raises(ValueError, match="duplicate"):
        load_confirmation(path)


def test_rejects_trajectory_config_drift(tmp_path):
    path = build_batch(tmp_path)
    change_trajectory(tmp_path, "sol", 0, lambda r: r["config"].update(seed=382))
    with pytest.raises(ValueError, match="Trajectory config"):
        load_confirmation(path)


@pytest.mark.parametrize("missing", ["path", "file"])
def test_missing_completed_artifact_is_an_integrity_error(tmp_path, missing):
    path = build_batch(tmp_path)
    if missing == "path":
        change_campaign(tmp_path, "sol", lambda c: c["outcomes"][0].update(artifact_paths={}))
    else:
        (tmp_path / "sol" / "runs" / "0" / "rollout.json").unlink()
    with pytest.raises(ValueError, match="no available trajectory"):
        load_confirmation(path)


def test_completed_trajectory_with_absent_rounds_is_an_integrity_error(tmp_path):
    path = build_batch(tmp_path)
    change_trajectory(tmp_path, "sol", 0, lambda r: r.update(rounds=r["rounds"][:2]))
    with pytest.raises(ValueError, match="absent rounds"):
        load_confirmation(path)


def test_rejects_nonterminal_saved_trajectory(tmp_path):
    path = build_batch(tmp_path)
    change_trajectory(tmp_path, "sol", 0, lambda r: r.update(status="running"))
    with pytest.raises(ValueError, match="not terminal"):
        load_confirmation(path)


def test_rejects_duplicate_source_rollout_ids_across_models(tmp_path):
    path = build_batch(tmp_path)
    change_trajectory(tmp_path, "deepseek", 0, lambda r: r.update(rollout_id="sol-0"))
    with pytest.raises(ValueError, match="distinct nonempty rollout IDs"):
        load_confirmation(path)


@pytest.mark.parametrize("summary_location", ["campaign", "setting"])
def test_rejects_saved_campaign_score_mismatch(tmp_path, summary_location):
    path = build_batch(tmp_path)
    def wrong_total(c):
        if summary_location == "campaign":
            c["summary"] = {"matched": 39}
        else:
            c["settings"] = {"async_counter": {"matched": 39}}
    change_campaign(tmp_path, "sol", wrong_total)
    with pytest.raises(ValueError, match="score total"):
        load_confirmation(path)


def test_accepts_matching_campaign_score_total(tmp_path):
    path = build_batch(tmp_path)
    change_campaign(tmp_path, "sol", lambda c: c.update(summary={"matched": 40},
                                                         settings={"async_counter": {"matched": 40}}))
    _, panels, _, _ = load_confirmation(path)
    assert int(panels[0]["y"].sum()) == 40


@pytest.mark.parametrize("fault", ["duplicate", "out_of_range", "wrong_score", "wrong_assignment"])
def test_rejects_inconsistent_saved_rounds(tmp_path, fault):
    path = build_batch(tmp_path)
    def corrupt(r):
        if fault == "duplicate":
            r["rounds"].append(r["rounds"][0].copy())
        elif fault == "out_of_range":
            r["rounds"][0]["round_index"] = 5
        elif fault == "wrong_score":
            r["rounds"][0]["match"] = False
        else:
            r["rounds"][0]["assigned_color"] = "blue"
    change_trajectory(tmp_path, "sol", 0, corrupt)
    with pytest.raises(ValueError):
        load_confirmation(path)


def test_pilot_seed_index_overlap_is_rejected(tmp_path):
    path = build_batch(tmp_path)
    plan = json.loads(path.read_text())
    plan["models"][0]["pilot_campaign"] = "sol/runs"
    save(path, plan)
    with pytest.raises(ValueError, match="overlap the pilot"):
        load_confirmation(path)


def test_pilot_sample_is_not_pooled(tmp_path):
    path = build_batch(tmp_path)
    pilot = {"base_config": {"seed": 4}, "outcomes": [{"rollout_index": i} for i in range(50)]}
    save(tmp_path / "pilot" / "campaign.json", pilot)
    plan = json.loads(path.read_text())
    plan["models"][0]["pilot_campaign"] = "pilot"
    save(path, plan)
    _, panels, rows, sources = load_confirmation(path)
    assert panels[0]["y"].shape == (8, 5)
    assert len(rows) == 80
    assert str(tmp_path / "pilot" / "campaign.json") in sources


def test_plan_hash_drift_blocks_results(tmp_path):
    path = build_batch(tmp_path)
    out = tmp_path / "analysis"
    with pytest.raises(ValueError, match="frozen plan"):
        analyze(path, out, expected_plan_sha256="0"*64)
    assert not out.exists()


def test_primary_holm_covers_only_two_endpoint_tests():
    # Eight gains and no losses gives exact p=2/256. The second panel is flat.
    improve = np.array([[0, 0, 0, 0, 1]]*8 + [[0, 0, 0, 0, 0]]*12)
    flat = np.ones((20, 5), dtype=int)
    panels = [{"model": name, "setting": "async_counter", "y": y, "failures": {}}
              for name, y in (("sol", improve), ("deepseek", flat))]
    primary, exploratory, _ = analyze_panels(panels)
    assert primary[0]["p"] == 2/256
    assert primary[0]["holm2_p"] == 4/256
    assert primary[0]["reject_at_05"]
    assert primary[1]["holm2_p"] == 1
    assert all("trend_p" not in row for row in primary)
    assert all("trend_p" in row and "any_round_p" in row for row in exploratory)
    assert primary[0]["family95_ci_low"] <= primary[0]["ci_low"]
    assert primary[0]["family95_ci_high"] >= primary[0]["ci_high"]


def test_full_offline_report_contains_planned_denominators_and_artifacts(tmp_path):
    pytest.importorskip("matplotlib")
    path = build_batch(tmp_path)
    out = tmp_path / "analysis"
    report = analyze(path, out)
    assert report["planned_rollouts"] == 16
    assert report["planned_rounds"] == 80
    assert report["pilot_pooled"] is False
    assert report["status"] == "all_planned_jobs_terminal"
    assert len(report["source_sha256"]) == 19
    assert (out / "accuracy-by-round-95ci.png").read_bytes().startswith(b"\x89PNG")
    assert (out / "accuracy-by-round-95ci.pdf").read_bytes().startswith(b"%PDF")
    assert "Holm" in (out / "index.html").read_text()
    assert "not pooled" in (out / "report.md").read_text()
    assert len((out / "rollout-round-data.csv").read_text().splitlines()) == 81
    assert json.loads((out / "confirmation-results.json").read_text())["plan_sha256"] == report["plan_sha256"]


@pytest.mark.parametrize("field,value", [("family_tests", 3), ("settings", ["sync_counter"]),
                                         ("rollouts_per_model", 0), ("rounds", 4), ("seed", True)])
def test_rejects_unsupported_plan(tmp_path, field, value):
    path = build_batch(tmp_path)
    plan = json.loads(path.read_text())
    plan[field] = value
    with pytest.raises(ValueError):
        validate_plan(plan)
