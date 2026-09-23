"""Offline integrity and inference checks for the mixed confirmation family."""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("statsmodels")

from experiments.color_game.config import GameConfig, make_plan  # noqa: E402
from scripts.analyze_color_efficient_confirmation import (  # noqa: E402
    CampaignNotReady, analysis_source_hashes, analyze, analyze_panels, load_confirmation, validate_plan,
)


SELECTED = ("sol", "deepseek", "astra", "luna", "glm")
CANDIDATES = [*SELECTED, "gemini", "haiku"]


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def read(path):
    return json.loads(path.read_text())


def amend(path, callback):
    value = read(path)
    callback(value)
    save(path, value)


def campaign_path(root, model):
    return root / model / "runs" / "campaign.json"


def trajectory_path(root, model, position=0):
    outcome = read(campaign_path(root, model))["outcomes"][position]
    return Path(outcome["artifact_paths"]["json"])


def build_batch(root, n=8):
    """Use distinct per-model N, seed, and indices, with no paid API calls."""
    plan = {
        "schema": "color-game-efficient-confirmation/v1",
        "settings": ["async_counter"], "rounds": 5, "family_alpha": .05,
        "stability_margin": .10, "candidate_models": CANDIDATES.copy(), "models": [],
    }
    for offset, name in enumerate(SELECTED):
        count, seed, start = n + offset, 401 + offset, 20 + offset
        config = GameConfig(colors=("red", "blue"), seed=seed, rollout_index=start).to_dict()
        model = {"name": name, "model": f"offline/{name}", "effort": "high", "temperature": 1.0}
        spec = {
            "name": name, "directory": name, "rollouts": count, "seed": seed,
            "start_index": start, "base_config": config, "model_config": model,
            "primary_test": "exact_two_sided_mcnemar" if offset < 2 else "all_rounds_cp_equivalence",
            "pilot_campaign": f"pilots/{name}",
        }
        plan["models"].append(spec)
        save(root / "pilots" / name / "campaign.json", {
            "base_config": {"seed": 9}, "outcomes": [{"rollout_index": i} for i in range(50)],
        })
        campaign = {
            "schema": "color-game-campaign/v1", "model": model,
            "base_config": config, "rollouts_per_setting": count,
            "selected_settings": ["async_counter"], "status": "complete", "outcomes": [],
        }
        for index in range(start, start + count):
            cfg = {**config, "rollout_index": index}
            prepared = make_plan(GameConfig(colors=("red", "blue"), seed=seed, rollout_index=index),
                                 namespace=f"{name}-{index}")
            path = root / name / "runs" / str(index) / "rollout.json"
            campaign["outcomes"].append({
                "setting": "async_counter", "rollout_index": index, "config": cfg,
                "status": "complete", "artifact_paths": {"json": str(path)},
                "namespace": prepared["namespace"], "plan": prepared,
            })
            save(path, {
                "schema": "color-game/v1", "rollout_id": str(uuid.uuid5(uuid.NAMESPACE_URL, str(path))),
                "status": "complete", "config": cfg, "plan": prepared,
                "models": {"alice": model, "bob": model},
                "rounds": [{
                    "round_index": r, "assigned_color": color, "alice_color": color,
                    "bob_color": color, "match": True, "valid_for_analysis": True, "actions": [],
                } for r, color in enumerate(prepared["assigned_colors"])],
            })
        save(campaign_path(root, name), campaign)
    path = root / "efficient-confirmation-plan.json"
    save(path, plan)
    return path


def add_original_plan(root, plan_path):
    plan = read(plan_path)
    spec = plan["models"][0]
    original = {
        "schema": "color-game-confirmation/v1", "settings": ["async_counter"],
        "rounds": 5, "rollouts_per_model": spec["rollouts"], "seed": spec["seed"],
        "start_index": spec["start_index"], "primary_test": spec["primary_test"],
        "family_tests": 2, "family_alpha": .05, "base_config": spec["base_config"],
        "models": [{key: spec[key] for key in ("name", "directory", "model_config", "pilot_campaign")},
                   {"name": "other-original", "directory": "other-original", "model_config": {"name": "other-original"}}],
    }
    original_path = root / "original-plan.json"
    save(original_path, original)
    spec.update(original_plan="original-plan.json",
                original_plan_sha256=hashlib.sha256(original_path.read_bytes()).hexdigest())
    save(plan_path, plan)
    return original_path


def statistical_panels(n=100):
    plan = {"candidate_models": CANDIDATES.copy(), "family_alpha": .05, "stability_margin": .10,
            "models": [{"name": name, "rollouts": n, "primary_test": "exact_two_sided_mcnemar" if i < 2
                        else "all_rounds_cp_equivalence"} for i, name in enumerate(SELECTED)]}
    panels = [{"model": spec["name"], "setting": "async_counter", "primary_test": spec["primary_test"],
               "y": np.ones((n, 5), dtype=int), "colors": 2, "failures": {}}
              for spec in plan["models"]]
    return plan, panels


def test_per_model_design_and_pilot_sources_are_preserved(tmp_path):
    path = build_batch(tmp_path)
    plan, panels, rows, sources = load_confirmation(path)
    assert [p["y"].shape for p in panels] == [(n, 5) for n in range(8, 13)]
    assert [p["primary_test"] for p in panels] == [s["primary_test"] for s in plan["models"]]
    assert len(rows) == 250
    assert all(p["y"].sum() == p["y"].size for p in panels)
    for name in SELECTED:
        assert str((tmp_path / "pilots" / name / "campaign.json").resolve()) in sources


def test_failed_missing_artifact_and_partial_rounds_keep_planned_denominator(tmp_path):
    path = build_batch(tmp_path)
    second = trajectory_path(tmp_path, "sol", 1)
    def fail(campaign):
        campaign["status"] = "complete_with_errors"
        campaign["outcomes"][0].update(status="failed", artifact_paths={})
        campaign["outcomes"][1].update(status="interrupted")
    amend(campaign_path(tmp_path, "sol"), fail)
    amend(second, lambda r: r.update(status="interrupted", rounds=r["rounds"][:2]))
    plan, panels, rows, _ = load_confirmation(path)
    sol = panels[0]
    assert sol["y"].shape == (8, 5)
    assert sol["y"].sum() == 32
    assert sol["failures"]["missing_artifact_rollouts"] == 1
    assert sol["failures"]["absent_rounds"] == sol["failures"]["missing_final_rounds"] == 8
    primary, _, points = analyze_panels(plan, panels)
    assert primary[0]["n"] == 8
    assert primary[0]["r1_accuracy"] == 7/8
    assert primary[0]["r5_accuracy"] == 6/8
    assert len(rows) == 250
    assert {p["n"] for p in points if p["model"] == "sol"} == {8}


@pytest.mark.parametrize("status", ["queued", "running", "unknown"])
def test_all_terminal_gate_precedes_any_trajectory_scoring(tmp_path, status):
    path = build_batch(tmp_path)
    amend(trajectory_path(tmp_path, "sol"), lambda r: r.update(rounds=[{"round_index": 99}]))
    amend(campaign_path(tmp_path, "glm"), lambda c: c["outcomes"][0].update(status=status))
    out = tmp_path / "analysis"
    with pytest.raises(CampaignNotReady):
        analyze(path, out)
    assert not out.exists()


def test_campaign_running_blocks_even_when_jobs_are_complete(tmp_path):
    path = build_batch(tmp_path)
    amend(campaign_path(tmp_path, "glm"), lambda c: c.update(status="running"))
    with pytest.raises(CampaignNotReady):
        load_confirmation(path)


def test_recovered_api_failure_does_not_discard_correct_saved_answer(tmp_path):
    path = build_batch(tmp_path)
    amend(trajectory_path(tmp_path, "sol"), lambda r: r["rounds"][0].update(
        valid_for_analysis=False, actions=[{"error": {"category": "model_api"}}, {"error": None}]))
    _, panels, _, _ = load_confirmation(path)
    assert panels[0]["y"][0, 0] == 1
    assert panels[0]["failures"]["api_error_actions"] == 1
    assert panels[0]["failures"]["api_error_rounds"] == 1
    assert panels[0]["failures"]["invalid_analysis_rounds"] == 1


@pytest.mark.parametrize("missing", ["path", "file", "rounds"])
def test_complete_jobs_cannot_silently_lose_data(tmp_path, missing):
    path = build_batch(tmp_path)
    if missing == "path":
        amend(campaign_path(tmp_path, "sol"), lambda c: c["outcomes"][0].update(artifact_paths={}))
    elif missing == "file":
        trajectory_path(tmp_path, "sol").unlink()
    else:
        amend(trajectory_path(tmp_path, "sol"), lambda r: r.update(rounds=r["rounds"][:2]))
    with pytest.raises(ValueError):
        load_confirmation(path)


@pytest.mark.parametrize("fault", ["duplicate", "not_uuid", "noncanonical"])
def test_saved_rollout_identity_must_be_canonical_and_globally_unique(tmp_path, fault):
    path = build_batch(tmp_path)
    old = read(trajectory_path(tmp_path, "sol"))["rollout_id"]
    replacement = old if fault == "duplicate" else ("not-a-uuid" if fault == "not_uuid" else old.upper())
    amend(trajectory_path(tmp_path, "deepseek"), lambda r: r.update(rollout_id=replacement))
    with pytest.raises(ValueError):
        load_confirmation(path)


def test_counter_namespaces_are_unique_across_models(tmp_path):
    path = build_batch(tmp_path)
    shared = read(trajectory_path(tmp_path, "sol"))["plan"]["namespace"]
    second = trajectory_path(tmp_path, "deepseek")
    def use_shared(c):
        c["outcomes"][0]["plan"]["namespace"] = shared
        c["outcomes"][0]["namespace"] = shared
    amend(campaign_path(tmp_path, "deepseek"), use_shared)
    amend(second, lambda r: r["plan"].update(namespace=shared))
    with pytest.raises(ValueError, match="namespace"):
        load_confirmation(path)


@pytest.mark.parametrize("location", ["round", "saved_plan", "outcome_plan"])
def test_target_assignments_cannot_drift(tmp_path, location):
    path = build_batch(tmp_path)
    trajectory = trajectory_path(tmp_path, "sol")
    old = read(trajectory)["plan"]["assigned_colors"][0]
    other = "blue" if old == "red" else "red"
    if location == "round":
        amend(trajectory, lambda r: r["rounds"][0].update(assigned_color=other))
    elif location == "saved_plan":
        amend(trajectory, lambda r: r["plan"]["assigned_colors"].__setitem__(0, other))
    else:
        amend(campaign_path(tmp_path, "sol"),
              lambda c: c["outcomes"][0]["plan"]["assigned_colors"].__setitem__(0, other))
    with pytest.raises(ValueError):
        load_confirmation(path)


def test_wholly_replaced_target_stream_still_must_match_frozen_seed(tmp_path):
    path = build_batch(tmp_path)
    trajectory = trajectory_path(tmp_path, "sol")
    original = read(trajectory)["plan"]["assigned_colors"]
    changed = ["blue" if c == "red" else "red" for c in original]
    amend(campaign_path(tmp_path, "sol"),
          lambda c: c["outcomes"][0]["plan"].update(assigned_colors=changed))
    def change_saved(r):
        r["plan"]["assigned_colors"] = changed
        for rnd, color in zip(r["rounds"], changed):
            rnd.update(assigned_color=color, alice_color=color, bob_color=color)
    amend(trajectory, change_saved)
    with pytest.raises(ValueError, match="seed/index"):
        load_confirmation(path)


@pytest.mark.parametrize("role", ["alice", "bob"])
@pytest.mark.parametrize("fault", ["missing", "changed"])
def test_both_saved_actor_model_configs_are_required(tmp_path, role, fault):
    path = build_batch(tmp_path)
    def change(r):
        if fault == "missing":
            r["models"].pop(role)
        else:
            r["models"][role]["effort"] = "low"
    amend(trajectory_path(tmp_path, "sol"), change)
    with pytest.raises(ValueError):
        load_confirmation(path)


@pytest.mark.parametrize("field,value", [("seed", 999), ("actions_per_agent", 9),
                                         ("rollout_index", 0), ("colors", ["red", "green"])])
def test_campaign_game_config_must_match_each_frozen_model_spec(tmp_path, field, value):
    path = build_batch(tmp_path)
    amend(campaign_path(tmp_path, "sol"), lambda c: c["base_config"].update({field: value}))
    with pytest.raises(ValueError):
        load_confirmation(path)


def test_saved_trajectory_config_must_match_outcome(tmp_path):
    path = build_batch(tmp_path)
    amend(trajectory_path(tmp_path, "sol"), lambda r: r["config"].update(fuzz_bob=False))
    with pytest.raises(ValueError):
        load_confirmation(path)


def test_duplicate_rollout_indices_are_rejected(tmp_path):
    path = build_batch(tmp_path)
    amend(campaign_path(tmp_path, "sol"), lambda c: c["outcomes"][1].update(
        rollout_index=c["outcomes"][0]["rollout_index"]))
    with pytest.raises(ValueError):
        load_confirmation(path)


def test_pilot_overlap_is_checked_against_each_model_seed_and_indices(tmp_path):
    path = build_batch(tmp_path)
    spec = read(path)["models"][4]
    pilot = tmp_path / "pilots" / "glm" / "campaign.json"
    amend(pilot, lambda p: p["base_config"].update(seed=spec["seed"]))
    with pytest.raises(ValueError, match="overlap"):
        load_confirmation(path)


def test_frozen_plan_hash_is_verified_before_writing_results(tmp_path):
    path = build_batch(tmp_path)
    frozen = hashlib.sha256(path.read_bytes()).hexdigest()
    amend(path, lambda p: p.update(note="changed after freezing"))
    out = tmp_path / "analysis"
    with pytest.raises(ValueError, match="hash|frozen"):
        analyze(path, out, expected_plan_sha256=frozen)
    assert not out.exists()


@pytest.mark.parametrize("fault", ["schema", "candidate_duplicate", "missing_candidate", "unselected_name",
                                   "model_count", "test_count", "alpha", "margin"])
def test_mixed_family_contract_cannot_change_silently(tmp_path, fault):
    path = build_batch(tmp_path)
    def change(p):
        if fault == "schema":
            p["schema"] = "color-game-confirmation/v1"
        elif fault == "candidate_duplicate":
            p["candidate_models"][-1] = p["candidate_models"][0]
        elif fault == "missing_candidate":
            p["candidate_models"].pop()
        elif fault == "unselected_name":
            p["models"][0]["name"] = "foreign-model"
        elif fault == "model_count":
            p["models"].pop()
        elif fault == "test_count":
            p["models"][0]["primary_test"] = "all_rounds_cp_equivalence"
        elif fault == "alpha":
            p["family_alpha"] = .10
        else:
            p["stability_margin"] = .20
    amend(path, change)
    with pytest.raises(ValueError):
        load_confirmation(path)


def test_original_fixed_plan_is_retained_and_hashed(tmp_path):
    path = build_batch(tmp_path)
    original = add_original_plan(tmp_path, path)
    _, panels, _, sources = load_confirmation(path)
    assert len(panels) == 5
    assert sources[str(original.resolve())] == hashlib.sha256(original.read_bytes()).hexdigest()


def test_original_plan_hash_cannot_be_changed_after_reference(tmp_path):
    path = build_batch(tmp_path)
    original = add_original_plan(tmp_path, path)
    amend(original, lambda p: p.update(seed=902))
    with pytest.raises(ValueError, match="hash|original|frozen"):
        load_confirmation(path)


@pytest.mark.parametrize("fault", [None, "original_hash", "pilot_overlap"])
def test_prelaunch_validation_checks_receipts_without_any_campaign_manifest(tmp_path, fault):
    path = build_batch(tmp_path)
    original = add_original_plan(tmp_path, path)
    for name in SELECTED:
        campaign_path(tmp_path, name).unlink()
    if fault == "original_hash":
        amend(original, lambda p: p.update(note="receipt changed"))
    elif fault == "pilot_overlap":
        amend(tmp_path / "pilots" / "glm" / "campaign.json",
              lambda p: p["base_config"].update(seed=read(path)["models"][4]["seed"]))
    if fault is None:
        sources = validate_plan(read(path), plan_path=path)
        assert str(original.resolve()) in sources
        assert all(str((tmp_path / "pilots" / name / "campaign.json").resolve()) in sources
                   for name in SELECTED)
    else:
        with pytest.raises(ValueError, match="hash|overlap"):
            validate_plan(read(path), plan_path=path)
    assert all(not campaign_path(tmp_path, name).exists() for name in SELECTED)


@pytest.mark.parametrize("field", ["schema", "settings", "rollouts_per_model", "seed", "start_index",
                                   "primary_test", "base_config", "model_config", "directory"])
def test_original_plan_identity_cannot_change_even_with_refreshed_hash(tmp_path, field):
    path = build_batch(tmp_path)
    original = add_original_plan(tmp_path, path)
    def change(p):
        if field == "schema":
            p[field] = "unrecognized/v1"
        elif field == "settings":
            p[field] = ["sync_counter"]
        elif field in ("rollouts_per_model", "seed", "start_index"):
            p[field] += 1
        elif field == "primary_test":
            p[field] = "all_rounds_cp_equivalence"
        elif field == "base_config":
            p[field]["actions_per_agent"] = 7
        elif field == "model_config":
            p["models"][0][field]["effort"] = "low"
        else:
            p["models"][0][field] = "elsewhere"
    amend(original, change)
    amend(path, lambda p: p["models"][0].update(
        original_plan_sha256=hashlib.sha256(original.read_bytes()).hexdigest()))
    with pytest.raises(ValueError):
        load_confirmation(path)


def test_mixed_holm_retains_two_unselected_candidates_as_p_one():
    plan, panels = statistical_panels(n=100)
    # Sol has eight gains, DeepSeek stays flat; each stability test is very strong.
    panels[0]["y"][:] = 0
    panels[0]["y"][:8, -1] = 1
    primary, pairs, points = analyze_panels(plan, panels)
    by_model = {r["model"]: r for r in primary}
    exact_stability = 2*.9**100
    exact_endpoint = 2/2**8
    assert len(primary) == 5 and len(pairs) == 30 and len(points) == 25
    for name in ("astra", "luna", "glm"):
        assert by_model[name]["p"] == pytest.approx(exact_stability, abs=1e-12)
        assert by_model[name]["holm7_p"] == pytest.approx(7*exact_stability, abs=1e-11)
        assert by_model[name]["decision"] == "stable_within_margin"
    assert by_model["sol"]["p"] == exact_endpoint
    # Three smaller p values leave four candidate hypotheses, not two.
    assert by_model["sol"]["holm7_p"] == pytest.approx(4*exact_endpoint)
    assert by_model["sol"]["decision"] == "improvement"
    assert by_model["deepseek"]["p"] == by_model["deepseek"]["holm7_p"] == 1
    assert by_model["deepseek"]["decision"] == "inconclusive"


def test_same_endpoints_with_middle_dip_does_not_establish_stability():
    plan, panels = statistical_panels(n=100)
    panels[2]["y"][:25, 2] = 0
    primary, pairs, _ = analyze_panels(plan, panels)
    astra = next(r for r in primary if r["model"] == "astra")
    assert astra["r1_accuracy"] == astra["r5_accuracy"] == 1
    assert astra["observed_range"] == .25
    assert astra["decision"] == "inconclusive"
    assert astra["p"] == max(p["p"] for p in pairs if p["model"] == "astra")


def test_endpoint_test_reports_decline_without_calling_it_stability():
    plan, panels = statistical_panels(n=100)
    panels[1]["y"][:20, -1] = 0
    primary, _, _ = analyze_panels(plan, panels)
    deepseek = next(r for r in primary if r["model"] == "deepseek")
    assert deepseek["endpoint_delta"] == pytest.approx(-.20)
    assert deepseek["decision"] == "decline"
    assert deepseek["reject_at_05"]


def test_small_flat_sample_remains_inconclusive():
    plan, panels = statistical_panels(n=8)
    primary, _, _ = analyze_panels(plan, panels)
    assert all(r["decision"] == "inconclusive" for r in primary)


@pytest.mark.parametrize("value", [.4, 2.0])
def test_raw_nonbinary_values_cannot_be_coerced_into_scores(value):
    plan, panels = statistical_panels(n=8)
    panels[2]["y"] = panels[2]["y"].astype(float)
    panels[2]["y"][0, 0] = value
    with pytest.raises(ValueError, match="binary"):
        analyze_panels(plan, panels)


def test_source_freeze_receipt_hashes_all_four_analysis_modules():
    hashes = analysis_source_hashes()
    expected = {"analyze_color_efficient_confirmation.py", "analyze_color_confirmation.py",
                "analyze_color_round_statistics.py", "analyze_color_stability.py"}
    assert set(hashes) == expected
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    for name, digest in hashes.items():
        assert digest == hashlib.sha256((scripts / name).read_bytes()).hexdigest()


def test_analyze_publishes_seven_candidate_family_rows_only_after_completion(tmp_path):
    pytest.importorskip("matplotlib")
    path = build_batch(tmp_path)
    amend(path, lambda p: p.update(excluded={"gemini": "excluded_budget", "haiku": "not_launched"}))
    out = tmp_path / "analysis"
    analyze(path, out, expected_plan_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    candidates = []
    for file in out.glob("*.json"):
        data = read(file)
        if isinstance(data, dict) and "candidate_family" in data:
            candidates.append(data)
    assert len(candidates) == 1
    report = candidates[0]
    assert len(report["primary"]) == 5
    assert len(report["candidate_family"]) == 7
    unselected = [r for r in report["candidate_family"] if r["model"] in {"gemini", "haiku"}]
    assert len(unselected) == 2
    assert all(r["raw_p"] is None and r["adjustment_input_p"] == 1 for r in unselected)
    assert all(r["holm7_p"] is None and r["selected"] is False for r in unselected)
    assert {r["model"]: r["status"] for r in unselected} == {"gemini": "excluded_budget", "haiku": "not_launched"}
