from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest

from ai_collusion.arms import Arm
from ai_collusion.client import ModelConfig
from ai_collusion.episode import run_episodes
from ai_collusion.preparation import prepare_episode
from ai_collusion.preview import build_previews
from test_preparation import scenario


def run_inputs(tmp_path):
    spec, dump, cuts = scenario(tmp_path)
    return dict(models=[ModelConfig("evaluated", "stub", "test", max_tokens=8192, stub_text='wait("1")')],
                env_model=ModelConfig("environment", "stub", "test", max_tokens=1234),
                spec=spec, dump=dump, cuts=cuts, variants=[None], modes=["neutral"], n_samples=1,
                out_dir=str(tmp_path / "runs"), base_seed=7, temperature=None, max_turns=3, run_id="trial")


def episodes(out):
    return [(p, json.loads(p.read_text())) for p in sorted(out.glob("*.json")) if p.name != "manifest.json"]


def test_each_model_gets_workers_without_waiting_for_other_model(tmp_path, monkeypatch):
    import threading

    inputs = run_inputs(tmp_path)
    inputs.update(models=[replace(inputs["models"][0], name=name) for name in ["first", "second"]],
                  n_samples=2, max_turns=1, workers=1)
    barrier = threading.Barrier(2, timeout=3)
    seen = []

    def generate(model, system, messages, **kwargs):
        seen.append(model.name)
        # A shared one-worker pool would time out before starting the second model.
        barrier.wait()
        return {"text": 'wait("1")'}

    monkeypatch.setattr("ai_collusion.episode.generate", generate)
    rows = episodes(run_episodes(**inputs))
    assert len(rows) == 4 and all(r["error"] is None for _, r in rows)
    assert seen.count("first") == seen.count("second") == 2


def test_arm_budgets_reach_model_and_writer_without_changing_environment(tmp_path, monkeypatch):
    inputs = run_inputs(tmp_path)
    arms = [Arm("short", None, "neutral", max_tokens=4096, max_turns=1, num_live_problems=1),
            Arm("longer", None, "neutral", max_turns=2)]
    seen = []

    def generate(model, system, messages, **kwargs):
        seen.append((model.name, model.max_tokens, deepcopy(messages)))
        return {"text": 'shell("pwd")' if model.name == "evaluated" else "/workspace"}

    monkeypatch.setattr("ai_collusion.episode.generate", generate)
    monkeypatch.setattr("ai_collusion.env.generate", generate)
    out = run_episodes(**inputs, arms=arms)
    rows = episodes(out)
    assert len(rows) == 2
    by_arm = {r["arm_id"]: (p, r) for p, r in rows}
    for arm in arms:
        path, record = by_arm[arm.id]
        assert arm.id in path.name
        assert record["episode"]["n_turns"] == arm.max_turns
        assert record["episode"]["end_reason"] == "max_turns"
        assert record["live_start_message_index"] == len(record["context"]["messages"])
        preview = next(p for p in build_previews(inputs["spec"], inputs["dump"], inputs["cuts"], [], [], [],
                                                 arms=[arm]) if p["role"] == "evaluee")
        assert record["context_sha256"] == preview["context_sha256"]
        assert record["model_config"]["max_tokens"] == (arm.max_tokens or 8192)
        assert record["env_model_config"]["max_tokens"] == 1234
    assert [tokens for name, tokens, _ in seen if name == "evaluated"] == [4096, 8192, 8192]
    assert [tokens for name, tokens, _ in seen if name == "environment"] == [1234] * 3
    first = next(messages for name, _, messages in seen if name == "evaluated")
    assert by_arm["short"][1]["context"]["messages"] == first
    assert inputs["models"][0].max_tokens == 8192
    assert by_arm["short"][1]["resolved_config"]["num_live_problems"] == 1


@pytest.mark.parametrize("key", ["max_tokens", "max_completion_tokens", "max_output_tokens"])
def test_explicit_token_cap_rejects_extra_body_override_before_calls_or_writes(tmp_path, monkeypatch, key):
    inputs = run_inputs(tmp_path)
    inputs["models"] = [replace(inputs["models"][0], extra_body={key: 16384})]
    calls = []

    def generate(*args, **kwargs):
        calls.append(args)
        return {"text": 'wait("1")'}

    monkeypatch.setattr("ai_collusion.episode.generate", generate)
    monkeypatch.setattr("ai_collusion.env.generate", generate)
    with pytest.raises(ValueError):
        run_episodes(**inputs, arms=[Arm("bounded", None, "neutral", max_tokens=4096)])
    assert calls == []
    assert not Path(inputs["out_dir"]).exists()


@pytest.mark.parametrize("explicit", [False, True])
def test_extra_body_token_setting_remains_accepted_without_arm_cap(tmp_path, explicit):
    inputs = run_inputs(tmp_path)
    inputs["models"] = [replace(inputs["models"][0], extra_body={"max_tokens": 16384})]
    if explicit:
        inputs["arms"] = [Arm("unbounded", None, "neutral", max_turns=1)]
    out = run_episodes(**inputs)
    record = episodes(out)[0][1]
    assert record["error"] is None
    assert record["model_config"]["extra_body"] == {"max_tokens": 16384}


def test_live_problem_selection_preserves_prefill_and_other_worlds(tmp_path):
    spec, dump, cuts = scenario(tmp_path)
    original = deepcopy(spec.cuts)
    one = prepare_episode(spec, dump, cuts[4], None, "neutral", num_live_problems=1)
    all_rounds = prepare_episode(spec, dump, cuts[4], None, "neutral")
    assert [r.state for r in one.world.ep.upcoming] == ["West Virginia"]
    assert [r.state for r in all_rounds.world.ep.upcoming] == ["West Virginia", "Idaho"]
    assert one.world.ep.rounds_done == all_rounds.world.ep.rounds_done
    assert len(one.world.ep.rounds_done) == 3
    assert one.context == all_rounds.context
    assert spec.cuts == original


@pytest.mark.parametrize("fallback, expected", [(3, 3), (None, 40)])
def test_unspecified_arm_turn_budget_uses_cli_then_spec(tmp_path, fallback, expected):
    inputs = run_inputs(tmp_path)
    inputs["max_turns"] = fallback
    out = run_episodes(**inputs, arms=[Arm("defaults", None, "neutral")])
    record = episodes(out)[0][1]
    assert record["episode"]["n_turns"] == expected
    assert record["resolved_config"]["max_turns"] == expected


@pytest.mark.parametrize("change", ["model", "environment", "spec", "dump", "arm", "seed"])
def test_changed_inputs_cannot_overwrite_resume_provenance(tmp_path, change):
    inputs = run_inputs(tmp_path)
    inputs["arms"] = [Arm("a", None, "neutral", max_turns=1)]
    out = run_episodes(**inputs)
    before = {p.name: p.read_bytes() for p in out.glob("*.json")}
    if change == "model":
        inputs["models"] = [replace(inputs["models"][0], extra_body={"changed": True})]
    elif change == "environment":
        inputs["env_model"] = replace(inputs["env_model"], max_tokens=5678)
    elif change == "spec":
        inputs["spec"].web["https://fixture.invalid"] = "changed fixture"
    elif change == "dump":
        path = inputs["dump"].root / "pages.jsonl"
        with path.open("a") as f:
            f.write(json.dumps({"page_id": "dse/Added"}) + "\n")
        from ai_collusion.wiki import Dump
        inputs["dump"] = Dump(inputs["dump"].root)
    elif change == "arm":
        inputs["arms"] = [Arm("a", None, "neutral", max_turns=2)]
    else:
        inputs["base_seed"] += 1
    with pytest.raises(ValueError):
        run_episodes(**inputs)
    assert {p.name: p.read_bytes() for p in out.glob("*.json")} == before


@pytest.mark.parametrize("explicit", [False, True])
def test_identical_resume_and_more_samples_keep_existing_episodes(tmp_path, explicit):
    inputs = run_inputs(tmp_path)
    if explicit:
        inputs["arms"] = [Arm("a", None, "neutral", max_turns=1)]
    out = run_episodes(**inputs)
    path, record = episodes(out)[0]
    before = path.read_bytes()
    identity = json.loads((out / "manifest.json").read_text())["experiment_sha256"]
    run_episodes(**inputs)
    inputs["n_samples"] = 2
    run_episodes(**inputs)
    assert len(episodes(out)) == 2
    assert path.read_bytes() == before
    assert json.loads((out / "manifest.json").read_text())["experiment_sha256"] == identity
    if not explicit:
        assert path.name == "evaluated__DataUSAStateSequenceCollab2027@4:env-neutral__n00_seed7.json"
        assert record["episode"]["n_turns"] == 3
