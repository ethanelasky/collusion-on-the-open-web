"""Offline campaign checks across an arbitrary number of model cohorts."""
import json
import threading

import pytest

from experiments.covert_channel import overnight
from experiments.covert_channel.analysis import load_records
from experiments.covert_channel.plans import make_plan


MODELS = ["glm-5.3-high", "deepseek-v4-flash-high", "gpt-5.6-high", "opus-5.1-high"]


def test_four_cohorts_share_inputs_and_request_controls_with_split_workers(tmp_path, monkeypatch):
    # Run real frozen-source child processes with the local unary demo only.
    # Four colors let that demo finish each question in five actions per agent.
    monkeypatch.setattr(overnight.time, "sleep", lambda seconds: threading.Event().wait(0.05))
    answers = ["red", "green", "blue", "yellow"]
    saved_plans = [make_plan(123, 0, answers, 5)]
    plan_path = tmp_path / "paired.json"
    plan_path.write_text(json.dumps(saved_plans))
    out = tmp_path / "four-models"
    assert overnight.main(["--demo", "--sessions", "1", "--workers", "6",
                           "--model-names", *MODELS, "--answer-set", ",".join(answers),
                           "--session-plan", str(plan_path), "--out", str(out)]) == 0

    campaign = json.loads((out / "campaign.json").read_text())
    assert campaign["planned_sessions"] == 16
    assert campaign["planned_guesses"] == 80
    assert campaign["max_model_turns"] == 800
    assert campaign["max_active_sessions"] == 6
    assert [job["model"] for job in campaign["jobs"]] == MODELS
    assert [job["workers"] for job in campaign["jobs"]] == [2, 2, 1, 1]
    assert [job["run_id"] for job in campaign["jobs"][:2]] == ["glm53", "deepseek4flash"]
    assert len({job["run_id"] for job in campaign["jobs"]}) == 4
    assert campaign["session_plan_source"] == str(plan_path)
    assert json.loads((out / "plans.json").read_text()) == saved_plans
    for job in campaign["jobs"]:
        assert job["groups"] == overnight.GROUPS
        assert job["status"] == "complete"
        manifest, records = load_records(out / job["run_id"])
        assert len(records) == 20
        assert manifest["status"] == "complete"
        assert manifest["config"]["workers"] == job["workers"]
        assert manifest["max_turns_per_session"] == 50
        assert manifest["questions_per_session"] == 5
        for option, expected in (("--stop-file", campaign["stop_file"]),
                                 ("--admission-file", campaign["admission_file"]),
                                 ("--request-limit", "6"),
                                 ("--session-plan", str(out / "plans.json"))):
            assert job["command"][job["command"].index(option) + 1] == expected
        for record in records:
            index = record["question_index"]
            plan = saved_plans[0]
            assert record["secret"] == plan["targets"][index]
            assert record["question_fuzz"] is None and plan["question_tags"][index]
            assert record["nonce"] == plan["nonce"]
            assert record["displayed_answer_sets"] == plan["displayed_answer_sets"]
        for group in job["groups"]:
            assert sum(r["n_turns"] for r in records if r["condition"] == group) <= 50

    progress = json.loads((out / "progress.json").read_text())
    assert progress["status"] == progress["analysis_status"] == "complete"
    assert progress["scored_guesses"] == 80
    assert progress["completed_sessions"] == 16
    assert progress["questions_with_model_errors"] == 0
    assert len(progress["groups"]) == 16
    assert {g["model"] for g in progress["groups"]} == set(MODELS)
    report = (out / "report.html").read_text()
    assert "4 model cohorts, 4 counter groups" in report
    for model in MODELS:
        assert f"<option>{model}</option>" in report


@pytest.mark.parametrize("workers,expected", [(50, [13, 13, 12, 12]), (7, [2, 2, 2, 1])])
def test_worker_budget_allocates_every_worker(workers, expected):
    jobs = overnight.make_jobs(MODELS, workers, overnight.GROUPS)
    assert [job["workers"] for job in jobs] == expected
    assert sum(job["workers"] for job in jobs) == workers


@pytest.mark.parametrize("options", [
    ["--model-names", *MODELS, "--workers", "3"],
    ["--model-names", "repeat", "repeat"],
])
def test_invalid_cohort_selection_fails_before_output_creation(tmp_path, options):
    out = tmp_path / "invalid"
    with pytest.raises(SystemExit):
        overnight.main(["--demo", "--answer-set", "red,blue", "--out", str(out), *options])
    assert not out.exists()


def test_invalid_saved_plan_fails_before_output_creation(tmp_path):
    plan_path = tmp_path / "wrong-plans.json"
    plan_path.write_text(json.dumps([make_plan(123, 0, ["red", "blue"], 4)]))
    out = tmp_path / "invalid"
    with pytest.raises(SystemExit):
        overnight.main(["--demo", "--sessions", "1", "--answer-set", "red,blue",
                        "--session-plan", str(plan_path), "--out", str(out)])
    assert not out.exists()
