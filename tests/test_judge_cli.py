"""Offline CLI artifact and denominator contracts; provider calls use stub transport."""
from copy import deepcopy
import json

import pytest

from ai_collusion import judge, judge_cli
from test_judge import citation, event, judgment, record


def inputs(tmp_path, response=None):
    run = tmp_path / "source"
    run.mkdir()
    (run / "manifest.json").write_text('{}')
    source = run / "sample.json"
    source.write_text(json.dumps(record()))
    rubric = tmp_path / "rubric.yaml"
    rubric.write_text("version: test\ninstructions: Apply fixture label.\nlabels:\n  help: Helping.\n")
    models = tmp_path / "models.json"
    models.write_text(json.dumps({"models": [{"name": "judge", "model": "stub", "transport": "stub", "max_tokens": 713,
                                                "stub_text": json.dumps(response or judgment())}]}))
    out = tmp_path / "judgments"
    args = ["--run", str(run), "--models", str(models), "--judge", "judge", "--rubric", str(rubric), "--out", str(out)]
    return args, source, rubric, models, out


def attempts(out):
    return sorted((out / "attempts").glob("*.json"))


def test_preview_never_calls_provider_and_preserves_source(tmp_path, monkeypatch):
    args, source, _, _, out = inputs(tmp_path)
    before = source.read_bytes()
    def forbidden(*args, **kwargs):
        raise AssertionError("preview invoked provider")
    monkeypatch.setattr(judge, "generate", forbidden)
    assert judge_cli.main(args + ["--preview"]) == 0
    assert source.read_bytes() == before
    outputs = [json.loads(p.read_text()) for p in out.rglob("*.json")]
    rendered = json.dumps(outputs)
    assert "I will help." in rendered
    assert "PREFILL_ONLY" in rendered
    assert "PRIVATE_SIMULATOR" not in rendered


@pytest.mark.parametrize("assessment", ["present", "absent"])
def test_exact_success_skips_and_changed_rubric_rejects(tmp_path, monkeypatch, assessment):
    args, source, rubric, _, out = inputs(tmp_path, judgment(assessment))
    before = source.read_bytes()
    assert judge_cli.main(args) == 0
    files = {p: p.read_bytes() for p in attempts(out)}
    assert len(files) == 1
    def forbidden(*args, **kwargs):
        raise AssertionError("successful identity called judge twice")
    monkeypatch.setattr(judge_cli, "judge_record", forbidden)
    assert judge_cli.main(args) == 0
    assert {p: p.read_bytes() for p in attempts(out)} == files
    rubric.write_text(rubric.read_text().replace("Helping.", "Changed fixture definition."))
    assert judge_cli.main(args) != 0
    assert {p: p.read_bytes() for p in attempts(out)} == files
    assert source.read_bytes() == before


def test_changed_model_config_rejects_without_overwriting_manifest(tmp_path):
    args, _, _, models, out = inputs(tmp_path)
    assert judge_cli.main(args) == 0
    original = (out / "manifest.json").read_bytes()
    data = json.loads(models.read_text())
    data["models"][0]["max_tokens"] = 714
    models.write_text(json.dumps(data))
    assert judge_cli.main(args) != 0
    assert (out / "manifest.json").read_bytes() == original


def test_failure_retry_retains_original_attempt(tmp_path):
    args, _, _, models, out = inputs(tmp_path)
    data = json.loads(models.read_text())
    data["models"][0]["stub_text"] = "invalid JSON"
    models.write_text(json.dumps(data))
    assert judge_cli.main(args) != 0
    first = attempts(out)
    assert len(first) == 1
    original = first[0].read_bytes()
    assert judge_cli.main(args) != 0
    assert len(attempts(out)) == 2
    assert first[0].read_bytes() == original
    for path in attempts(out):
        envelope = json.loads(path.read_text())
        assert envelope["judgment"] is None and envelope["error"]


def test_same_basename_in_distinct_sources_never_collides(tmp_path):
    args, source, _, _, out = inputs(tmp_path)
    second = tmp_path / "second"
    second.mkdir()
    (second / "manifest.json").write_text('{}')
    # Even identical bytes must remain two source identities.
    (second / source.name).write_bytes(source.read_bytes())
    args.insert(2, str(second))
    assert judge_cli.main(args) == 0
    envelopes = [json.loads(p.read_text()) for p in attempts(out)]
    assert len(envelopes) == 2
    assert len({e["source_path"] for e in envelopes}) == 2
    assert len({e["identity"] for e in envelopes}) == 2


def test_appended_sources_are_judged_without_rewriting_successes(tmp_path):
    args, source, _, _, out = inputs(tmp_path)
    assert judge_cli.main(args) == 0
    original = {p: p.read_bytes() for p in attempts(out)}
    added = record()
    added["sample_index"] = 1
    (source.parent / "added.json").write_text(json.dumps(added))
    assert judge_cli.main(args) == 0
    assert len(attempts(out)) == 2
    assert all(p.read_bytes() == content for p, content in original.items())
    summary = json.loads((out / "summary.json").read_text())
    assert summary["groups"][0]["source_count"] == 2


def test_primary_denominator_excludes_unknown_censored_interface_and_failed_judges(tmp_path):
    args, source, _, _, out = inputs(tmp_path)
    base = record()
    variants = [base]
    for end in ["no_call", "max_turns"]:
        rec = deepcopy(base)
        rec["episode"]["end_reason"] = end
        variants.append(rec)
    unknown = deepcopy(base)
    del unknown["error"]
    variants.append(unknown)
    invalid = deepcopy(base)
    invalid["error"] = {"type": "TimeoutError", "message": "failed"}
    variants.append(invalid)
    for i, rec in enumerate(variants):
        rec["sample_index"] = i
        (source.parent / ("sample.json" if i == 0 else f"sample-{i}.json")).write_text(json.dumps(rec))
    assert judge_cli.main(args) != 0
    envelopes = [json.loads(p.read_text()) for p in attempts(out)]
    eligible = next(e for e in envelopes if e["source_facts"]["sample_index"] == 0)
    failed = deepcopy(eligible)
    failed["error"] = {"type": "InvalidJudgment", "message": "bad evidence"}
    failed["judgment"] = None
    envelopes.append(failed)
    uncertain = deepcopy(eligible)
    uncertain["judgment"] = {**judgment("uncertain"), "labels": {"help": "uncertain"}}
    # Ensure this additional record is otherwise fully eligible.
    uncertain["source_facts"] = judge.source_facts(base)
    envelopes.append(uncertain)
    group = judge_cli.summarize(envelopes, ["help"])["groups"][0]
    assert group["source_count"] == 7
    assert group["judge_failures"] == 1
    assert group["source_transport_invalid"] == 1
    assert group["interface_limited"] == 1
    assert group["max_turn_censored"] == 1
    label = group["labels"]["help"]
    assert label["uncertain"] == 1
    assert label["primary_denominator"] == 1
    assert label["primary_present"] == 1
    assert label["primary_rate"] == 1.0


def test_shared_evidence_stage_and_channel_counts_are_per_episode_and_label():
    payload = {"labels": {"help": "present", "coordinate": "present"}, "evidence": [
        event(labels=["help", "coordinate"], quotes=[citation(), citation(turn=9, quote="Finished")]),
        event(labels=["help", "coordinate"], quotes=[citation(field="reasoning", quote="They need tomorrow's question.")])]}
    envelope = {"source_facts": judge.source_facts(record()), "judgment": payload, "error": None}
    group = judge_cli.summarize([envelope], ["help", "coordinate"])["groups"][0]
    assert group["source_count"] == 1
    for label in ("help", "coordinate"):
        counts = group["labels"][label]
        assert counts["present"] == counts["primary_denominator"] == 1
        assert counts["stages"] == {"expression": 1}
        assert counts["channels"] == {"text": 1, "reasoning": 1}
