"""Exercise real context/world construction; only model generation is intercepted."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from ai_collusion.arms import Arm
from ai_collusion.client import ModelConfig
from ai_collusion.episode import play
from ai_collusion.preparation import prepare_episode
from ai_collusion.preview import build_previews
from ai_collusion.wiki import Dump, build_wiki_context, load_spec, reconstruct, wiki_read_url, wiki_save_url


ROOT = Path(__file__).resolve().parents[1]
PAGE = "dse/DataUSAStateSequenceCollab2027"
REQUEST = "AgentNeedConnecticutValuesNow1781637367"


def scenario(tmp_path):
    """Use the shipped task verbatim and a tiny on-disk dump with fixture board text."""
    root = tmp_path / "dump"
    root.mkdir()
    pages = [PAGE, f"dse/{REQUEST}"]
    revisions = [
        dict(page_id=PAGE, seq=3, time="2026-06-16T09:33:05Z", label="prior", ip16="0",
             body="fixture board", hunks=[]),
        dict(page_id=PAGE, seq=4, time="2026-06-16T09:33:48Z", label="ParallelSectorAgentApr2", ip16="0",
             body="fixture board\nfixture reference", hunks=[dict(op="insert", b0=1, b1=2)]),
        dict(page_id=f"dse/{REQUEST}", seq=1, time="2026-06-16T19:16:08Z", label="OaiResearcher", ip16="0",
             body="fixture request", hunks=[]),
    ]
    (root / "pages.jsonl").write_text("".join(json.dumps(dict(page_id=p)) + "\n" for p in pages))
    (root / "revisions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in revisions))
    dump = Dump(root)
    spec = load_spec(ROOT / "wikitasks/sector61_state.yaml")
    return spec, dump, {4: reconstruct(dump, PAGE, 4)}


@pytest.mark.parametrize("variant", [None, "notable_request"])
@pytest.mark.parametrize("explicit", [False, True])
def test_preview_equals_first_request_and_saved_context(tmp_path, monkeypatch, variant, explicit):
    spec, dump, cuts = scenario(tmp_path)
    arm = Arm("trial", variant, "neutral")
    previews = build_previews(spec, dump, cuts, [variant], ["neutral"], [],
                              arms=[arm] if explicit else None)
    preview = next(p for p in previews if p["role"] == "evaluee")
    prepared = prepare_episode(spec, dump, cuts[4], variant, "neutral")
    initial = deepcopy(prepared.context)
    observed = []

    def generate(model, system, messages, **kwargs):
        observed.append(deepcopy(dict(system=system, messages=messages)))
        return {"text": 'wait("1")'}

    monkeypatch.setattr("ai_collusion.episode.generate", generate)
    body = play(model=ModelConfig("test", "stub", "test"), world=prepared.world,
                context=prepared.context, prepared=prepared, temperature=None, seed=0, max_turns=1)
    assert observed[0] == initial == body["context"]
    assert {k: preview[k] for k in ("system", "messages")} == initial
    assert body["messages_final"][:len(initial["messages"])] == initial["messages"]
    assert body["context_sha256"] == preview["context_sha256"] == prepared.context_sha256
    # Independent counts from the shipped spec: three completed rounds, one request read.
    assistant = [m["content"] for m in initial["messages"] if m["role"] == "assistant"]
    assert sum(text.startswith("answer(") for text in assistant) == 3
    assert sum(wiki_read_url(REQUEST) in text for text in assistant) == (1 if variant else 0)
    assert prepared.context == initial


def test_fresh_worlds_and_preview_sample_save_isolation(tmp_path):
    spec, dump, cuts = scenario(tmp_path)
    first = prepare_episode(spec, dump, cuts[4], "notable_request", "neutral")
    second = prepare_episode(spec, dump, cuts[4], "notable_request", "neutral")
    assert first.context_sha256 == second.context_sha256
    assert first.world.cache_key != second.world.cache_key
    first.world.wiki_save("Isolation", "preview-only-marker")
    first.context["messages"][0]["content"] = "mutated fixture"
    assert second.world.wiki_body("Isolation") is None
    assert second.context["messages"][0]["content"] != "mutated fixture"
    arm = Arm("isolation", "notable_request", "neutral")
    write = f"web_fetch({json.dumps(wiki_save_url('Isolation', 'preview-only-marker'))})"
    read = f"web_fetch({json.dumps(wiki_read_url('Isolation'))})"
    combined = build_previews(spec, dump, cuts, [], [], [write, read], arms=[arm])
    alone = build_previews(spec, dump, cuts, [], [], [read], arms=[arm])
    combined_read = [p for p in combined if p["role"] == "env-model"][-1]
    alone_read = next(p for p in alone if p["role"] == "env-model")
    assert combined_read["messages"] == alone_read["messages"]
    assert "preview-only-marker" not in str(combined_read["messages"])


def test_direct_play_installs_raw_context_without_mutating_it(tmp_path, monkeypatch):
    spec, dump, cuts = scenario(tmp_path)
    prepared = prepare_episode(spec, dump, cuts[4], "notable_request", "neutral")
    raw = build_wiki_context(spec, cuts[4], "notable_request")
    before = deepcopy(raw)
    from ai_collusion.env import make_world
    world = make_world(spec, dump, cuts[4], "notable_request", "neutral", None, 0)
    monkeypatch.setattr("ai_collusion.episode.generate", lambda *a, **kw: {"text": 'wait("1")'})
    result = play(model=ModelConfig("test", "stub", "test"), world=world, context=raw,
                  temperature=None, seed=0, max_turns=1)
    assert raw == before
    assert result["context"] == prepared.context


@pytest.mark.parametrize('command', ['show', 'run'])
def test_generic_cli_replay_does_not_require_episode_config(tmp_path, capsys, command):
    from ai_collusion.wiki_cli import main
    import yaml
    spec, dump, cuts = scenario(tmp_path)
    config = tmp_path / 'models.yaml'
    config.write_text(yaml.safe_dump({'models': [{'name': 'stub', 'transport': 'stub', 'model': 'stub'}]}))
    argv = ['--dump', str(dump.root), command, '--page', PAGE, '--rev', '3',
            '--spec', str(ROOT / 'wikitasks/sector61_state.yaml')]
    if command == 'show':
        main(argv + ['--json'])
        record = json.loads(capsys.readouterr().out)
    else:
        main(argv + ['--models', str(config), '--out', str(tmp_path / 'runs'), '--run-id', 'generic'])
        path = next(p for p in (tmp_path / 'runs/generic').glob('*.json') if p.name != 'manifest.json')
        record = json.loads(path.read_text())['context']
    cut = reconstruct(dump, PAGE, 3)
    expected = build_wiki_context(spec, cut, hint='none')
    assert record['system'] == expected['system']
    assert record['messages'] == expected['messages']
