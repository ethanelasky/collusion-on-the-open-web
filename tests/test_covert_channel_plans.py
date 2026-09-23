import json

import pytest

from experiments.covert_channel.analysis import load_records
from experiments.covert_channel.plans import make_plan, validate_plan
from experiments.covert_channel.run import main

COLORS = ["red", "green", "blue", "yellow"]


def test_saved_plans_pair_all_sessions_and_groups_across_invocations(tmp_path):
    plans = [make_plan(9, i, COLORS, 2) for i in range(2)]
    path = tmp_path / "plans.json"
    path.write_text(json.dumps(plans))
    cohorts = []
    for name in ("a", "b"):
        main(["--preset", "counter-four-50", "--demo", "--sessions", "2", "--rounds-per-session", "2",
              "--answer-set", ",".join(COLORS), "--session-plan", str(path), "--skip-analysis",
              "--out", str(tmp_path), "--run-id", name])
        manifest, records = load_records(tmp_path / name)
        assert len(records) == 16 and manifest["status"] == "complete"
        for r in records:
            p = plans[r["session_index"]]
            assert r["secret"] == p["targets"][r["round_index"]]
            assert r["question_fuzz"] == p["question_tags"][r["round_index"]]
            assert r["displayed_answer_sets"] == p["displayed_answer_sets"]
        cohorts.append(manifest["session_plan_sha256"])
    assert cohorts[0] == cohorts[1]


def test_invalid_plan_targets_and_tags_are_rejected():
    plan = make_plan(1, 0, COLORS, 2)
    validate_plan(plan, COLORS, 2, True)
    plan["targets"][0] = "not-a-candidate"
    with pytest.raises(ValueError, match="targets"):
        validate_plan(plan, COLORS, 2, True)
    plan["targets"][0] = "red"
    plan["question_tags"][0] = "\nnot a tag"
    with pytest.raises(ValueError, match="question tag"):
        validate_plan(plan, COLORS, 2, True)
