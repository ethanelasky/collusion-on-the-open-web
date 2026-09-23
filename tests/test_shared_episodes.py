"""Offline tests of shared wiki edits through the original episode and simulator."""
from copy import deepcopy
import json
from pathlib import Path
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from ai_collusion import episode, env
from ai_collusion.client import ModelConfig
from ai_collusion.environment_modes import MODES, environment_label
from ai_collusion.preparation import prepare_episode
from ai_collusion.wiki import load_spec, wiki_read_url, wiki_save_url
from ai_collusion.wiki_observer import serve_worlds
from test_episode_provenance import run_inputs, episodes
from test_preparation import scenario, ROOT


def group(tmp_path, n=3, mode="neutral"):
    spec, dump, cuts = scenario(tmp_path)
    return [prepare_episode(spec, dump, cuts[4], "notable_request", mode,
                            ModelConfig("environment", "stub", "test"), i)
            for i in range(n)]


def tool(name, argument):
    return f"{name}({json.dumps(argument)})"


@pytest.mark.parametrize("shared", [True, False])
def test_peer_reads_only_committed_wiki_posts_with_private_simulator_histories(tmp_path, monkeypatch, shared):
    prepared = group(tmp_path)
    initial = [deepcopy(p.context) for p in prepared]
    calls, environments = {}, []

    def evaluated(model, system, messages, *, seed, **kwargs):
        calls[seed] = calls.get(seed, 0) + 1
        if calls[seed] == 1:
            assert dict(system=system, messages=messages) == initial[seed]
            action = (tool("web_fetch", wiki_save_url("LivePeerNote", "shared-value-marker"))
                      if seed == 0 else tool("web_fetch", wiki_read_url("LivePeerNote")))
        else:
            action = tool("shell", f"echo PRIVATE-{seed}")
        return {"text": f"private thought PRIVATE-{seed}\n{action}", "reasoning": f"private reasoning {seed}"}

    def simulator(model, system, messages, **kwargs):
        environments.append(deepcopy(messages))
        return {"text": "local command completed\n@@ELAPSED 4"}

    monkeypatch.setattr(episode, "generate", evaluated)
    monkeypatch.setattr(env, "generate", simulator)
    result = episode.play_group(model=ModelConfig("evaluated", "stub", "test"), prepared=prepared,
                                temperature=None, seeds=[0, 1, 2], max_turns=2, shared=shared)
    assert calls == {0: 2, 1: 2, 2: 2}
    assert len(environments) == 3
    assert len({p.world.cache_key for p in prepared}) == 3
    assert len({id(p.world.env_thread) for p in prepared}) == 3
    for index, body in enumerate(result):
        assert body["context"] == initial[index]
        assert body["episode"]["n_env_calls"] == 1
        assert body["episode"]["end_reason"] == "max_turns"
        assert set(body["episode"]["wiki_posts"]) == ({"LivePeerNote"} if index == 0 else set())
        history = json.dumps(environments[index])
        assert f"PRIVATE-{index}" in history
        assert all(f"PRIVATE-{other}" not in history for other in range(3) if other != index)
        if index:
            assert ("shared-value-marker" in body["episode"]["turns"][0]["result"]) is shared
    assert prepared[1].world.shared_wiki is prepared[0].world.shared_wiki if shared else (
        prepared[1].world.shared_wiki is not prepared[0].world.shared_wiki)


def test_world_sharing_keeps_original_append_rules_and_time_visibility(tmp_path):
    first, second = [p.world for p in group(tmp_path, 2)]
    shared, lock = [], threading.RLock()
    for i, world in enumerate((first, second)):
        world.shared_wiki, world.wiki_lock, world.agent_id = shared, lock, f"agent{i}"
    first.advance(10)
    first.wiki_save("FuturePage", "first line")
    assert second.wiki_body("FuturePage") is None
    assert "FuturePage" not in second.page_index()
    assert "FuturePage" not in second.recent_changes()
    second.advance(10)
    assert second.wiki_body("FuturePage") == "first line"
    assert "FuturePage" in second.search("first")
    assert "agent0" in second.recent_changes()
    # Original World saves retain existing text and record only added lines.
    second.wiki_save("FuturePage", "first line\nsecond line")
    assert first.wiki_body("FuturePage") == "first line\n\nsecond line"
    assert first.own_posts == {"FuturePage": ["first line"]}
    assert second.own_posts == {"FuturePage": ["second line"]}
    second.wiki_save("FuturePage", "second line")
    assert len(shared) == 2


def test_shell_writes_use_original_environment_effects_once(tmp_path, monkeypatch):
    prepared = group(tmp_path, 2)
    monkeypatch.setattr(episode, "generate", lambda *a, seed, **kw: {
        "text": tool("shell", "python construct_and_submit_edit.py") if seed == 0 else
        tool("web_fetch", wiki_read_url("ShellPeerNote"))})
    monkeypatch.setattr(env, "generate", lambda *a, **kw: {
        "text": "saved\n@@WIKI_SAVE ShellPeerNote\nfrom simulated shell\n@@END\n@@ELAPSED 1"})
    result = episode.play_group(model=ModelConfig("evaluated", "stub", "test"), prepared=prepared,
                                temperature=None, seeds=[0, 1], max_turns=1)
    assert result[0]["episode"]["n_env_calls"] == 1
    assert result[1]["episode"]["n_env_calls"] == 0
    # Reader's GET costs 3 s; writer's shell costs 4 s. Future edits stay hidden.
    assert "from simulated shell" not in result[1]["episode"]["turns"][0]["result"]
    prepared[1].world.advance(1)
    assert prepared[1].world.wiki_body("ShellPeerNote") == "from simulated shell"
    assert len(prepared[0].world.shared_wiki) == 1


def test_full_individual_trajectories_finish_on_original_question_schedule(tmp_path, monkeypatch):
    prepared = group(tmp_path, 4)

    def evaluated(*args, seed, **kwargs):
        world = prepared[seed].world
        pending = world.pending()
        return {"text": tool("answer", ", ".join(map(str, world.expected(pending.state))))
                if pending else 'wait("99999")'}

    monkeypatch.setattr(episode, "generate", evaluated)
    results = episode.play_group(model=ModelConfig("evaluated", "stub", "test"), prepared=prepared,
                                 temperature=None, seeds=list(range(4)), max_turns=10)
    assert len(results) == 4
    for result in results:
        assert result["episode"]["end_reason"] == "all_rounds_resolved"
        assert all(r["correct"] for r in result["episode"]["rounds"])
        assert result["episode"]["n_env_calls"] == 0
        assert result["group"]["score"] == "individual"


def test_existing_runner_records_configurable_groups_and_resume_identity(tmp_path, monkeypatch):
    inputs = run_inputs(tmp_path)
    inputs["n_samples"] = 2
    inputs["workers"] = 2
    inputs["max_turns"] = 2
    out = episode.run_episodes(**inputs, agents=3)
    rows = episodes(out)
    assert len(rows) == 6
    assert len({r["group"]["id"] for _, r in rows}) == 2
    assert {r["seed"] for _, r in rows} == set(range(7, 13))
    assert {r["group"]["agent_id"] for _, r in rows} == {"agent0", "agent1", "agent2"}
    assert all(r["episode"]["n_turns"] == 2 for _, r in rows)
    before = {p.name: p.read_bytes() for p, _ in rows}
    episode.run_episodes(**inputs, agents=3)
    assert before == {p.name: p.read_bytes() for p, _ in episodes(out)}
    with pytest.raises(ValueError, match="inputs changed"):
        episode.run_episodes(**inputs, agents=4)
    from ai_collusion.docent_cli import record_to_agent_run
    manifest = json.loads((out / "manifest.json").read_text())
    for path, row in rows:
        row["_file"] = path.name
        exported = record_to_agent_run(row, manifest)
        assert exported.metadata["agent_group"] == row["group"]
        assert row["group"]["agent_id"] in exported.name
        assert row["group"]["agent_id"] in exported.description


def test_partial_group_is_not_silently_replayed(tmp_path):
    inputs = run_inputs(tmp_path)
    out = episode.run_episodes(**inputs, agents=2)
    path, _ = episodes(out)[0]
    path.unlink()
    with pytest.raises(ValueError, match="Partial shared group"):
        episode.run_episodes(**inputs, agents=2)


@pytest.mark.parametrize("agents", [0, -1, True, 1.5])
def test_invalid_agent_counts_fail_before_output(tmp_path, agents):
    inputs = run_inputs(tmp_path)
    with pytest.raises(ValueError):
        episode.run_episodes(**inputs, agents=agents)
    assert not Path(inputs["out_dir"]).exists()


@pytest.mark.parametrize("model_count,workers", [(1, 2), (2, 1)])
def test_concurrent_groups_reject_fixed_observer_ports(tmp_path, model_count, workers):
    inputs = run_inputs(tmp_path)
    inputs["models"] = [ModelConfig(f"evaluated-{i}", "stub", "test") for i in range(model_count)]
    inputs["workers"] = workers
    with pytest.raises(ValueError, match="automatic wiki ports"):
        episode.run_episodes(**inputs, html=True, wiki_port=8889)
    assert not Path(inputs["out_dir"]).exists()


def test_observer_reads_same_world_without_calls_or_changes(tmp_path):
    prepared = group(tmp_path, 2)
    worlds = [p.world for p in prepared]
    posts, lock = [], threading.RLock()
    for i, world in enumerate(worlds):
        world.shared_wiki, world.wiki_lock, world.agent_id = posts, lock, f"agent{i}"
    worlds[0].wiki_save("ViewNote", "<script>alert(1)</script> exact text")
    before = deepcopy((posts, [w.history for w in worlds], [w.task_s for w in worlds]))
    with serve_worlds(worlds) as server:
        with urlopen(server.url + "&agent=1&page=ViewNote") as response:
            html = response.read().decode()
            assert response.status == 200
        assert "&lt;script&gt;alert(1)&lt;/script&gt; exact text" in html
        assert "<script>" not in html
        base = server.url.split("?")[0]
        for url in (base, server.url + "&Save=1&text=attack", server.url + "&agent=999"):
            with pytest.raises(HTTPError):
                urlopen(url)
        with pytest.raises(HTTPError):
            urlopen(Request(server.url, data=b"attack", method="POST"))
    assert before == (posts, [w.history for w in worlds], [w.task_s for w in worlds])


def test_new_names_preserve_legacy_modes_and_original_simulator(tmp_path, monkeypatch):
    assert MODES == ("neutral", "broken", "empty_success")
    assert "Legacy" in environment_label("evil")
    world = group(tmp_path, 1, "broken")[0].world
    for url in (world.table_url, "https://example.com/data", "https://api.counterapi.dev/v1/ns/key/up",
                "https://example.com/wiki.cgi?action=browse&id=AvailableWiki",
                "https://notwikiservice.at/dse/wiki.cgi?action=browse&id=AvailableWiki",
                world.web_fixtures.pages[0].url):
        body, source = world.resolve_url(url)
        assert body.startswith("HTTP 503") and source == "network-error"
    assert world.counters == {}
    world.wiki_save("AvailableWiki", "wiki marker")
    assert "wiki marker" in world.resolve_url(wiki_read_url("AvailableWiki"))[0]
    seen = []
    def simulator(model, system, messages, **kwargs):
        seen.append((system, messages))
        return {"text": "HTTP 503 Service Unavailable\n@@ELAPSED 30"}
    monkeypatch.setattr(env, "generate", simulator)
    response = env.step(world, tool("shell", "python fetch_dynamic_url.py"))
    assert response.source == "env-model"
    assert "Broken Environment with Wiki prefill" in seen[0][0]
    assert env.MODE_RULES["evil"]


def test_existing_cli_exposes_agents_and_html_without_new_runner(tmp_path, monkeypatch, capsys):
    from ai_collusion import runner, wiki_cli
    spec, dump, cuts = scenario(tmp_path)
    monkeypatch.setattr(runner, "load_models", lambda path, only: [
        ModelConfig(only[0], "stub", "test", stub_text='wait("1")')])
    wiki_cli.main(["--dump", str(dump.root), "play", "--page", cuts[4].page_id,
                   "--rev", "4", "--spec", str(ROOT / "wikitasks/sector61_state.yaml"),
                   "--only", "evaluated", "--env-model", "environment", "--mode", "neutral",
                   "--agents", "2", "--max-turns", "2", "--html",
                   "--out", str(tmp_path / "runs"), "--run-id", "cli-check"])
    rows = episodes(tmp_path / "runs" / "cli-check")
    assert len(rows) == 2
    assert all(row["group"]["size"] == 2 for _, row in rows)
    assert "Read-only shared wiki:" in capsys.readouterr().err


def test_variants_and_initial_messages_unchanged_by_group_launch(tmp_path, monkeypatch):
    from ai_collusion.arms import load_arms
    from ai_collusion.preview import build_previews
    from test_hint_discovery import discovery_scenario
    spec, dump, cuts = discovery_scenario(tmp_path)
    arms = load_arms(ROOT / "experiments/wiki-baselines.yaml")
    assert [arm.mode for arm in arms] == list(MODES)
    previews = build_previews(spec, dump, cuts, [], [], [], arms=arms)
    contexts = [{"system": p["system"], "messages": p["messages"]}
                for p in previews if p["role"] == "evaluee"]
    assert len(contexts) == 3
    assert contexts[0] == contexts[1] == contexts[2]


@pytest.mark.parametrize("name", ["sector61_state.yaml", "sector61_state_seven.yaml"])
def test_culture_variants_removed_from_both_specs(name):
    spec = load_spec(ROOT / "wikitasks" / name)
    for cut in spec.cuts.values():
        assert not any(v.startswith("swarm") for v in cut.get("variants", {}))


@pytest.mark.parametrize('broken', ['{"episode":', '{}'])
def test_corrupt_shared_group_is_rejected_without_overwriting_peer_records(tmp_path, broken):
    inputs = run_inputs(tmp_path)
    out = episode.run_episodes(**inputs, agents=2)
    path, _ = episodes(out)[0]
    path.write_text(broken)
    before = {p.name: p.read_bytes() for p in out.glob('*.json') if p.name != 'manifest.json'}
    with pytest.raises(ValueError, match='Partial shared group'):
        episode.run_episodes(**inputs, agents=2)
    assert before == {p.name: p.read_bytes() for p in out.glob('*.json') if p.name != 'manifest.json'}
