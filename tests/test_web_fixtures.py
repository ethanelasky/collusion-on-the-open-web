"""Check static page dispatch, simulator fallback, and immutable page snapshots."""
from copy import deepcopy
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil

import pytest

from ai_collusion import env, episode, preview, web_fixtures
from ai_collusion.arms import Arm
from ai_collusion.episode import run_episodes
from ai_collusion.preparation import prepare_episode
from ai_collusion.preview import build_previews
from ai_collusion.wiki import wiki_read_url
from test_episode_provenance import run_inputs


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "ai_collusion/fixtures/web"
PAGES = [
    ("census-pums", "https://www.census.gov/programs-surveys/acs/microdata.html"),
    ("datausa-industry", "https://datausa.io/profile/naics/educational-services-health-care-social-assistance"),
]


class PageInspection(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.visible = []
        self.in_style = False

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        if tag == "style":
            self.in_style = True

    def handle_endtag(self, tag):
        if tag == "style":
            self.in_style = False

    def handle_data(self, data):
        if not self.in_style:
            self.visible.append(data)


@pytest.mark.parametrize("stem,url", PAGES)
def test_saved_html_has_no_controls_data_routes_or_hidden_payloads(stem, url):
    html = (ASSETS / f"{stem}.html").read_text()
    page = PageInspection()
    page.feed(html)
    tags = {tag for tag, _ in page.tags}
    assert {"html", "head", "title", "body", "main", "nav"} <= tags
    assert not tags & {"script", "iframe", "object", "embed", "form", "input", "button",
                       "select", "textarea", "table", "link", "img"}
    ids = {attrs["id"] for _, attrs in page.tags if "id" in attrs}
    for tag, attrs in page.tags:
        assert not {"src", "srcset", "action", "download", "hidden", "data"} & attrs.keys()
        assert not any(name.startswith(("on", "data-")) for name in attrs)
        assert attrs.get("aria-hidden") != "true"
        if "href" in attrs:
            assert attrs["href"].startswith("#")
            assert attrs["href"][1:] in ids
        if tag == "meta":
            assert "http-equiv" not in attrs
    visible = " ".join(page.visible).lower()
    assert not re.search(r"\b(download|export|api|ftp|simulation|simulated|evaluation|fixture)\b", visible)
    assert not re.search(r"\d", visible), "No displayed counts, years, or answer tables belong on these pages"
    assert not re.search(r"url\s*\(|@import|display\s*:\s*none|visibility\s*:\s*hidden", html, re.I)
    provenance = json.loads((ASSETS / f"{stem}.provenance.json").read_text())
    assert provenance["source_url"] == url
    assert provenance["fixture"] == f"{stem}.html"
    assert provenance["retrieved_at_utc"]


@pytest.fixture
def captured(monkeypatch):
    calls = []

    def generate(model, system, messages, **kwargs):
        calls.append(deepcopy({"system": system, "messages": messages}))
        return {"text": "Informational page response.\n@@ELAPSED 9"}

    monkeypatch.setattr(env, "generate", generate)
    return calls


def prepare(inputs, mode="neutral"):
    return prepare_episode(inputs["spec"], inputs["dump"], inputs["cuts"][4], None, mode,
                           env_model=inputs["env_model"])


def tool_call(tool, url):
    arg = url if tool == "web_fetch" else f"curl -s '{url}' | head -c 120"
    return f"{tool}({json.dumps(arg)})"


@pytest.mark.parametrize("stem,url", PAGES)
@pytest.mark.parametrize("mode", ("neutral", "evil", "empty_success"))
@pytest.mark.parametrize("tool", ["shell"])
def test_complex_dispatch_and_preview_deliver_complete_html_with_existing_clocks(
        tmp_path, captured, stem, url, mode, tool):
    inputs = run_inputs(tmp_path)
    prepared = prepare(inputs, mode)
    world = prepared.world
    html = (ASSETS / f"{stem}.html").read_text()
    call = tool_call(tool, url)
    before = world.task_s
    history = deepcopy(world.history)
    _, preview = env.preview_env_prompt(world, call)
    assert world.task_s == before
    assert world.history == history
    assert (html in preview) == (mode != "evil")
    result = env.step(world, call)
    assert result.source == "env-model"
    assert len(captured) == 1
    delivered = captured[0]["messages"][-1]["content"]
    assert (html in delivered) == (mode != "evil")
    assert env.MODE_RULES[mode] in captured[0]["system"]
    assert world.task_s - before == round(9 * world.ep.rate)
    assert result.elapsed == {"reported_s": 9, "charged_s": 9}
    assert "@@ELAPSED" not in result.result
    assert "Informational page response." in result.result
    # Prompt-preview production uses the same prepared-world path as live dispatch.
    previews = build_previews(inputs["spec"], inputs["dump"], inputs["cuts"], [None],
                              [mode], [call])
    rendered = next(p for p in previews if p["role"] == "env-model")
    assert rendered["messages"][0]["content"] == preview
    assert rendered["resolved_config"]["web_fixtures"] == prepared.resolved_config["web_fixtures"]


@pytest.fixture
def forbid_environment_model(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("A registered static page must not call the environment model")
    monkeypatch.setattr(env, "generate", fail)


@pytest.mark.parametrize("stem,url", PAGES)
@pytest.mark.parametrize("mode", ["neutral", "empty_success"])
@pytest.mark.parametrize("command", [None, "curl {url}", "curl -sSLf '{url}'",
                                      "wget -q -O - '{url}'"])
def test_static_dispatch_and_docent_preview_return_exact_bytes_without_generation(
        tmp_path, forbid_environment_model, stem, url, mode, command):
    inputs = run_inputs(tmp_path)
    world = prepare(inputs, mode).world
    tool = "shell" if command else "web_fetch"
    call = f"{tool}({json.dumps(command.format(url=url) if command else url)})"
    html = (ASSETS / f"{stem}.html").read_bytes()
    for _ in range(2):
        before = world.task_s
        result = env.step(world, call)
        assert result.source == "web-fixture"
        assert result.env_call is None
        assert result.elapsed is None
        assert world.env_calls == []
        assert world.history[-1]["result"].encode() == html
        assert html.decode() in result.result
        assert world.task_s - before == round(world.ep.call_cost_s.get(tool, 3) * world.ep.rate)
    entries = build_previews(inputs["spec"], inputs["dump"], inputs["cuts"], [None], [mode], [call])
    entry = next(p for p in entries if p["role"] == "static-page")
    reference = env.step(prepare(inputs, mode).world, call)
    assert entry["system"] == ""
    assert entry["messages"] == [{"role": "assistant", "content": call},
                                 {"role": "user", "content": reference.result}]
    assert entry["resolved_config"]["web_fixtures"] == world.web_fixtures.identity()
    run = preview.to_agent_run(entry)
    messages = run.transcripts[0].messages
    assert [message.role for message in messages] == ["assistant", "user"]
    assert all(message.metadata["prefill"] is False for message in messages)
    assert all(message.metadata["provenance"] == "static_page_preview" for message in messages)
    assert not any("[PREFILL]" in str(message.content) for message in messages)


@pytest.mark.parametrize("command", [None, "curl -sSLf '{url}'", "wget -q -O - '{url}'"])
def test_evil_mode_keeps_static_urls_on_simulator_path(tmp_path, captured, command):
    inputs = run_inputs(tmp_path)
    world = prepare(inputs, "evil").world
    url = PAGES[0][1]
    call = (tool_call("web_fetch", url) if command is None else
            "shell(" + json.dumps(command.format(url=url)) + ")")
    result = env.step(world, call)
    assert result.source == "env-model"
    assert len(captured) == 1
    assert (ASSETS / "census-pums.html").read_text() not in captured[0]["messages"][-1]["content"]
    entries = build_previews(inputs["spec"], inputs["dump"], inputs["cuts"], [None], ["evil"], [call])
    assert [p["role"] for p in entries] == ["evaluee", "env-model"]


@pytest.mark.parametrize("command", [
    "curl -I {url}", "curl -X POST {url}", "curl -d data {url}",
    "curl -o page.html {url}", "curl {url} {url}", "wget {url}",
    "wget -q -O page.html {url}", "curl {url} | head -c 120",
    "curl {url}; echo done", "curl {url} > page.html", "env curl {url}",
    "curl --header 'X-Test: true' {url}", "curl {url} && echo done",
    "curl {url}$(echo extra)", "curl {url}`echo extra`", "curl {url}\necho done",
])
def test_non_get_or_complex_shell_calls_keep_simulator_path(tmp_path, captured, command):
    inputs = run_inputs(tmp_path)
    call = "shell(" + json.dumps(command.format(url=PAGES[0][1])) + ")"
    result = env.step(prepare(inputs).world, call)
    assert result.source == "env-model"
    assert len(captured) == 1


@pytest.mark.parametrize("suffix", ["", "#overview", "/", "/#overview"])
def test_known_page_normalization_returns_saved_bytes(tmp_path, captured, suffix):
    inputs = run_inputs(tmp_path)
    stem, url = PAGES[0]
    world = prepare(inputs).world
    result = env.step(world, tool_call("web_fetch", url + suffix))
    assert result.source == "web-fixture"
    assert world.history[-1]["result"].encode() == (ASSETS / f"{stem}.html").read_bytes()
    assert captured == []


@pytest.mark.parametrize("url", [
    PAGES[0][1] + "?download=1",
    PAGES[0][1] + "?year=2019",
    PAGES[0][1] + "/other",
    PAGES[0][1] + "//",
    PAGES[0][1].replace("www.census.gov", "www.census.gov.attacker.invalid"),
    PAGES[0][1].replace("www.census.gov", "attacker.invalid@www.census.gov"),
    PAGES[0][1].replace("www.census.gov", "www.census.gov:443"),
    PAGES[0][1].replace("https:", "http:"),
    "https://datausa.io/profile/geo/idaho",
    "https://example.org/background",
])
def test_unknown_paths_queries_and_lookalike_hosts_remain_simulated(tmp_path, captured, url):
    inputs = run_inputs(tmp_path)
    world = prepare(inputs).world
    result = env.step(world, tool_call("web_fetch", url))
    assert result.source == "env-model"
    assert len(captured) == 1
    delivered = captured[0]["messages"][-1]["content"]
    assert url in delivered
    for stem, _ in PAGES:
        assert (ASSETS / f"{stem}.html").read_text() not in delivered


@pytest.mark.parametrize("command,matched", [
    ("curl {url}; echo done", True),
    ("curl {url}|head", True),
    ("curl '{url};foo'", False),
    ('curl "{url};foo"', False),
    ('curl "{url})"', False),
    ('curl "{url}>foo"', False),
    ('curl "{url} extra"', False),
    ("curl '{url}?year=2019'", False),
    ("curl {url}?year=2019|head", False),
])
def test_shell_operators_preserve_url_boundaries_in_live_and_preview(
        tmp_path, captured, command, matched):
    inputs = run_inputs(tmp_path)
    stem, url = PAGES[0]
    html = (ASSETS / f"{stem}.html").read_text()
    call = "shell(" + json.dumps(command.format(url=url)) + ")"
    world = prepare(inputs).world
    _, preview = env.preview_env_prompt(world, call)
    assert (html in preview) == matched
    result = env.step(world, call)
    assert result.source == "env-model"
    assert len(captured) == 1
    assert (html in captured[0]["messages"][-1]["content"]) == matched


@pytest.fixture
def fixture_copy(tmp_path, monkeypatch):
    target = tmp_path / "web"
    shutil.copytree(ASSETS, target)
    monkeypatch.setattr(web_fixtures, "FIXTURE_DIR", target)
    return target


def test_loaded_page_bytes_survive_disk_edit_and_repeated_reads(tmp_path, fixture_copy, captured):
    inputs = run_inputs(tmp_path)
    prepared = prepare(inputs)
    stem, url = PAGES[0]
    path = fixture_copy / f"{stem}.html"
    original = path.read_bytes()
    identity = deepcopy(prepared.resolved_config["web_fixtures"])
    assert hashlib.sha256(original).hexdigest() in json.dumps(identity)
    updated = original.replace(b"</main>", b"<p>Changed informational wording.</p></main>")
    assert updated != original
    path.write_bytes(updated)
    for tool in ("web_fetch", "shell", "web_fetch"):
        env.step(prepared.world, tool_call(tool, url))
        body = (captured[-1]["messages"][-1]["content"] if tool == "shell"
                else prepared.world.history[-1]["result"])
        assert original.decode() in body
        assert "Changed informational wording." not in body
    assert prepared.world.web_fixtures.identity() == identity
    fresh = prepare(inputs)
    env.step(fresh.world, tool_call("web_fetch", url))
    assert fresh.world.history[-1]["result"].encode() == updated
    assert fresh.resolved_config["web_fixtures"] != identity


def test_complete_large_fixture_reaches_fetch_shell_and_preview(tmp_path, fixture_copy, captured):
    inputs = run_inputs(tmp_path)
    stem, url = PAGES[0]
    path = fixture_copy / f"{stem}.html"
    original = path.read_text()
    expanded = original.replace("</main>", "<p>" + "Background description. " * 250 + "</p></main>")
    assert len(expanded) > 4000
    path.write_text(expanded)
    world = prepare(inputs).world
    for tool in ("web_fetch", "shell"):
        call = tool_call(tool, url)
        entries = build_previews(inputs["spec"], inputs["dump"], inputs["cuts"], [None],
                                 ["neutral"], [call])
        rendered = entries[-1]
        assert expanded in rendered["messages"][-1]["content"]
        result = env.step(world, call)
        delivered = (world.history[-1]["result"] if tool == "web_fetch" else
                     captured[-1]["messages"][-1]["content"])
        assert expanded in delivered
        assert "source_hash_scope" not in delivered
        assert result.source == ("web-fixture" if tool == "web_fetch" else "env-model")


@pytest.mark.parametrize("extension", ["html", "provenance.json"])
def test_fixture_byte_edit_rejects_resume_and_keeps_existing_artifacts(tmp_path, fixture_copy, extension):
    inputs = run_inputs(tmp_path)
    inputs["arms"] = [Arm("fixture", None, "neutral", max_turns=1)]
    out = run_episodes(**inputs)
    run_episodes(**inputs)  # Identical loaded fixture bytes can resume.
    before = {p.name: p.read_bytes() for p in out.glob("*.json")}
    path = fixture_copy / f"census-pums.{extension}"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="cannot resume"):
        run_episodes(**inputs)
    assert {p.name: p.read_bytes() for p in out.glob("*.json")} == before


def test_batch_keeps_manifest_snapshot_when_disk_changes_between_samples(
        tmp_path, fixture_copy, captured, monkeypatch):
    inputs = run_inputs(tmp_path)
    inputs.update(n_samples=2, workers=1, arms=[Arm("fixture", None, "neutral", max_turns=1)])
    stem, url = PAGES[0]
    path = fixture_copy / f"{stem}.html"
    original = path.read_text()
    updated = original.replace("</main>", "<p>Edited during the batch.</p></main>")
    calls = []

    def evaluated_generate(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            path.write_text(updated)
        return {"text": tool_call("web_fetch", url)}

    monkeypatch.setattr(episode, "generate", evaluated_generate)
    out = run_episodes(**inputs)
    manifest = json.loads((out / "manifest.json").read_text())
    identity = manifest["experiment"]["conditions"][0]["resolved_config"]["web_fixtures"]
    records = [json.loads(p.read_text()) for p in out.glob("*.json") if p.name != "manifest.json"]
    assert len(records) == len(calls) == 2
    assert captured == []
    assert path.read_text() == updated
    for record in records:
        assert record["resolved_config"]["web_fixtures"] == identity
        assert record["experiment_sha256"] == manifest["experiment_sha256"]
        turn = record["episode"]["turns"][0]
        assert turn["source"] == "web-fixture"
        assert turn["env_call"] is None
        assert original in turn["result"]
        assert "Edited during the batch." not in turn["result"]


def test_preview_invocation_keeps_one_snapshot_across_prepared_worlds(
        tmp_path, fixture_copy, monkeypatch):
    inputs = run_inputs(tmp_path)
    stem, url = PAGES[0]
    path = fixture_copy / f"{stem}.html"
    original = path.read_text()
    updated = original.replace("</main>", "<p>Edited during preview generation.</p></main>")
    real_prepare = preview.prepare_episode
    prepared_worlds = []

    def prepare_then_edit(*args, **kwargs):
        prepared = real_prepare(*args, **kwargs)
        prepared_worlds.append(prepared)
        if len(prepared_worlds) == 1:
            path.write_text(updated)
        return prepared

    monkeypatch.setattr(preview, "prepare_episode", prepare_then_edit)
    previews = build_previews(inputs["spec"], inputs["dump"], inputs["cuts"], [None],
                              ["neutral", "empty_success"], [tool_call("web_fetch", url)])
    assert len(previews) == len(prepared_worlds) == 3
    first_identity = prepared_worlds[0].resolved_config["web_fixtures"]
    assert path.read_text() == updated
    for entry, prepared in zip(previews, prepared_worlds):
        assert prepared.resolved_config["web_fixtures"] == first_identity
        assert entry["resolved_config"]["web_fixtures"] == first_identity
        if entry["role"] == "static-page":
            facts = entry["messages"][-1]["content"]
            assert original in facts
            assert "Edited during preview generation." not in facts


@pytest.mark.parametrize("mode", ("neutral", "evil", "empty_success"))
def test_background_read_preserves_dynamic_wiki_and_intended_endpoint(tmp_path, captured, mode):
    inputs = run_inputs(tmp_path)
    world = prepare(inputs, mode).world
    env.step(world, tool_call("web_fetch", PAGES[0][1]))
    wiki = env.step(world, tool_call("web_fetch", wiki_read_url(world.cut.page_name)))
    assert wiki.source == "wiki"
    assert "fixture board" in wiki.result
    world.wiki_save("FixtureIntegrationCheck", "A new live board post.")
    reread = env.step(world, tool_call("web_fetch", wiki_read_url("FixtureIntegrationCheck")))
    assert reread.source == "wiki"
    assert "A new live board post." in reread.result
    endpoint = env.step(world, tool_call("web_fetch", world.table_url))
    if mode == "evil":
        assert endpoint.source == "env-model"
    else:
        assert endpoint.source == "data"
        if mode == "empty_success":
            assert '{"data":[]}' in endpoint.result
        else:
            assert str(next(iter(world.table.values()))[0]) in endpoint.result
