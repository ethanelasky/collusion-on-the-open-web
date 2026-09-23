"""The env-model conversation is append-only: each request's whole prompt is a prefix of the next
request, which is the only shape the provider's prompt cache rewards (verified live 2026-09-09:
an identical or extended prompt hits, a prompt sharing only a partial prefix never does)."""
from ai_collusion import env
from ai_collusion.preparation import prepare_episode
from test_episode_provenance import run_inputs


def _world(tmp_path, monkeypatch, replies):
    inputs = run_inputs(tmp_path)
    cut = next(iter(inputs["cuts"].values()))
    prepared = prepare_episode(inputs["spec"], inputs["dump"], cut, None, "neutral", env_model=inputs["env_model"])
    captured = []

    def generate(model, system, messages, **kwargs):
        captured.append([dict(m) for m in messages])
        return {"text": replies[len(captured) - 1]}

    monkeypatch.setattr(env, "generate", generate)
    return prepared.world, captured


def test_each_request_extends_the_previous_one(tmp_path, monkeypatch):
    world, captured = _world(tmp_path, monkeypatch, [
        "HTTP/1.1 200 OK\nBackground page.\n@@ELAPSED 2",
        "HTTP/1.1 200 OK\nBackground page again.\n@@ELAPSED 2",
        "total 0\n@@ELAPSED 1",
    ])
    page = "https://www.census.gov/programs-surveys/acs/microdata.html?x=1"
    env.step(world, f'web_fetch("{page}")')
    env.step(world, 'wait("5")')                       # deterministic, no env call
    env.step(world, f'web_fetch("{page}")')
    env.step(world, 'shell("ls /tmp")')
    assert len(captured) == 3
    for earlier, later in zip(captured, captured[1:]):
        assert later[:len(earlier)] == earlier, "an earlier request must be a verbatim prefix of the next"
        assert later[len(earlier)]["role"] == "assistant"
        assert later[-1]["role"] == "user"
    # The env model's own reply stands in for that call's history entry; only the other calls repeat.
    second = captured[1][-1]["content"]
    assert 'wait("5")' in second
    assert "Background page." not in second
    assert "SINCE THE LAST ENTRY ABOVE" in second
    # A reply is frozen as the assistant turn that follows its request.
    assert captured[1][len(captured[0])]["content"].startswith("HTTP/1.1 200 OK\nBackground page.")


def test_repeated_facts_are_referenced_not_resent(tmp_path, monkeypatch):
    world, captured = _world(tmp_path, monkeypatch, ["one\n@@ELAPSED 1", "two\n@@ELAPSED 1"])
    call = 'shell("curl -s https://www.census.gov/programs-surveys/acs/microdata.html | head -c 200")'
    env.step(world, call)
    env.step(world, call)
    first, second = captured[0][-1]["content"], captured[1][-1]["content"]
    assert "RESOLVED FACTS" in first and "RESOLVED FACTS" in second
    assert "(unchanged: the same content you were given for this URL earlier" not in first
    assert "(unchanged: the same content you were given for this URL earlier" in second


def test_preview_does_not_extend_the_thread(tmp_path, monkeypatch):
    world, captured = _world(tmp_path, monkeypatch, ["ok\n@@ELAPSED 1"])
    call = 'shell("ls")'
    env.preview_env_prompt(world, call)
    assert world.env_thread == [] and world.env_seen == 0
    env.step(world, call)
    assert len(world.env_thread) == 2
    _, preview = env.preview_env_prompt(world, call)
    assert "[env model reply]\nok" in preview
    assert len(world.env_thread) == 2
