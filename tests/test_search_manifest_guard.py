"""Exercise manifest conformance through the real batch writer and preparation."""
from copy import deepcopy
import json

import pytest

from ai_collusion import wiki
from ai_collusion.arms import Arm
from ai_collusion.client import ModelConfig
from ai_collusion.episode import run_episodes
from test_hint_discovery import discovery_scenario


def batch_inputs(tmp_path):
    spec, dump, cuts = discovery_scenario(tmp_path)
    return dict(models=[ModelConfig("test", "stub", "test", max_tokens=1024)],
                env_model=ModelConfig("env", "stub", "test"), spec=spec, dump=dump,
                cuts=cuts, variants=[], modes=[],
                arms=[Arm("search", "notable", "neutral", max_turns=1,
                          max_tokens=512, hint="search_result")],
                n_samples=2, out_dir=str(tmp_path / "runs"), base_seed=0,
                temperature=None, max_turns=3, run_id="search-guard", workers=1)


@pytest.mark.parametrize("edit", ["body", "provenance_only"])
def test_search_fixture_edit_stops_batch_before_next_provider_call(tmp_path, monkeypatch, edit):
    inputs = batch_inputs(tmp_path)
    fixture = tmp_path / "search.html"
    original = wiki.SEARCH_FIXTURE.read_text()
    fixture.write_text(original)
    monkeypatch.setattr(wiki, "SEARCH_FIXTURE", fixture)
    observed = []

    def generate(model, system, messages, **kwargs):
        observed.append(deepcopy(messages))
        # Whitespace at EOF leaves the rendered context identical, but changes
        # the fixture identity recorded in the resolved configuration.
        fixture.write_text(original + ("\n" if edit == "provenance_only" else
                                       "\n<!-- Changed during the batch. -->"))
        return {"text": 'wait("1")'}

    monkeypatch.setattr("ai_collusion.episode.generate", generate)
    with pytest.raises(ValueError, match="prepared episode differs from.*manifest"):
        run_episodes(**inputs)

    assert len(observed) == 1
    out = tmp_path / "runs" / "search-guard"
    manifest = json.loads((out / "manifest.json").read_text())
    records = [json.loads(p.read_text()) for p in out.glob("*.json") if p.name != "manifest.json"]
    assert len(records) == 1
    record = records[0]
    condition = manifest["experiment"]["conditions"][0]
    assert record["sample_index"] == 0
    assert record["context"]["messages"] == observed[0]
    assert record["context_sha256"] == condition["context_sha256"]
    assert record["experiment_sha256"] == manifest["experiment_sha256"]
    assert record["resolved_config"] == {**condition["resolved_config"], "max_tokens": 512}


@pytest.mark.parametrize("arm_cap,global_cap", [(1, 3), (None, 1), (None, None)])
def test_unchanged_search_and_effective_caps_match_manifest(tmp_path, monkeypatch, arm_cap, global_cap):
    inputs = batch_inputs(tmp_path)
    inputs.update(max_turns=global_cap, arms=[Arm("search", "notable", "neutral",
                  max_turns=arm_cap, max_tokens=512, hint="search_result")])
    observed = []

    def generate(model, system, messages, **kwargs):
        observed.append(model.max_tokens)
        return {"text": "Finished."}

    monkeypatch.setattr("ai_collusion.episode.generate", generate)
    out = run_episodes(**inputs)
    manifest = json.loads((out / "manifest.json").read_text())
    condition = manifest["experiment"]["conditions"][0]
    records = [json.loads(p.read_text()) for p in out.glob("*.json") if p.name != "manifest.json"]
    assert len(records) == 2
    assert observed and set(observed) == {512}
    for record in records:
        assert record["context_sha256"] == condition["context_sha256"]
        assert record["resolved_config"] == {**condition["resolved_config"], "max_tokens": 512}
