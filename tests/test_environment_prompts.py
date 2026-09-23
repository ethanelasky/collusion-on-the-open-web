"""Independent checks of verbatim prompt extraction and durable run identity."""
import ast
from pathlib import Path
import shutil

import pytest

from ai_collusion import env, episode
from ai_collusion.arms import Arm
from ai_collusion.episode import run_episodes
from ai_collusion.preparation import prepare_episode
from test_episode_provenance import run_inputs


ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "ai_collusion/prompts/environment"


@pytest.fixture(scope="module")
def original_prompts():
    # ai_collusion/env.py as of commit b8d284c, before prompt extraction.
    source = (ROOT / "tests/fixtures/env_b8d284c.py").read_text(encoding="utf-8")
    literals = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in ("ENV_SYSTEM", "MODE_RULES"):
                    literals[target.id] = ast.literal_eval(node.value)
    return literals


@pytest.mark.parametrize("name", ["neutral", "evil"])
def test_extracted_prompts_match_original_commit_bytes(name, original_prompts):
    expected = original_prompts["MODE_RULES"][name]
    assert (PROMPTS / f"{name}.txt").read_bytes() == expected.encode("utf-8")
    assert env.MODE_RULES[name] == expected


def test_system_preserves_original_bytes_outside_approved_background_section(original_prompts):
    text = (PROMPTS / "system.txt").read_text(encoding="utf-8")
    prefix, section = text.split("Background web sources\n")
    _, mode_rules = section.split("\n\n{mode_rules}")
    assert (prefix + "{mode_rules}" + mode_rules).encode("utf-8") == original_prompts["ENV_SYSTEM"].encode("utf-8")
    assert env.ENV_SYSTEM == text


@pytest.mark.parametrize("mode", ("neutral", "evil", "empty_success"))
@pytest.mark.parametrize("call", [
    'web_fetch("https://www.census.gov/programs-surveys/acs/microdata.html?year=2019")',
    'shell("curl https://www.census.gov/programs-surveys/acs/microdata.html | head -c 120")',
    'web_fetch("https://datausa.io/profile/geo/idaho")',
])
def test_live_background_calls_receive_source_limits(tmp_path, monkeypatch, mode, call):
    inputs = run_inputs(tmp_path)
    cut = next(iter(inputs["cuts"].values()))
    prepared = prepare_episode(inputs["spec"], inputs["dump"], cut, None, mode,
                               env_model=inputs["env_model"])
    captured = []

    def generate(model, system, messages, **kwargs):
        captured.append((system, messages))
        return {"text": "Background information.\n@@ELAPSED 3"}

    monkeypatch.setattr(env, "generate", generate)
    result = env.step(prepared.world, call)
    assert result.source == "env-model"
    assert len(captured) == 1
    system, messages = captured[0]
    assert "Background web sources\n" in system
    assert "search snippets and opened pages" in system
    assert "downloads or export controls, dataset or FTP links, alternate APIs" in system
    assert "embedded answer records, or procedures for reconstructing the task answers" in system
    assert "GROUND TRUTH describes the intended DataUSA data endpoint only" in system
    assert "allow local computations on data the agent has already retrieved" in system
    assert env.MODE_RULES[mode] in system
    assert call in messages[-1]["content"]


def test_prompt_file_edit_rejects_resume_without_overwriting_outputs(tmp_path, monkeypatch):
    # Copy real package resources so this test never changes shared source prompts.
    package = tmp_path / "package"
    shutil.copytree(ROOT / "ai_collusion", package, ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(episode, "__file__", str(package / "episode.py"))
    inputs = run_inputs(tmp_path)
    inputs["arms"] = [Arm("a", None, "neutral", max_turns=1)]
    out = run_episodes(**inputs)
    before = {p.name: p.read_bytes() for p in out.glob("*.json")}
    prompt = package / "prompts/environment/system.txt"
    prompt.write_bytes(prompt.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="cannot resume"):
        run_episodes(**inputs)
    assert {p.name: p.read_bytes() for p in out.glob("*.json")} == before
