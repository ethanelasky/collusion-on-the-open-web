"""No-network checks for config-bound notebook prompts and preview updates."""
import json
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest

from experiments.color_game.config import GameConfig, make_plan
from experiments.color_game.notebook import PromptEditor, normalize_config


@pytest.fixture
def display_backend(monkeypatch):
    # These tests do not require the optional IPython installation.
    package = ModuleType("IPython")
    package.__path__ = []
    display = ModuleType("IPython.display")
    display.HTML = lambda text: text
    display.display = Mock()
    display.update_display = Mock()
    monkeypatch.setitem(sys.modules, "IPython", package)
    monkeypatch.setitem(sys.modules, "IPython.display", display)
    return display


def test_preview_keeps_one_plan_until_a_new_run_plan_is_supplied(display_backend):
    config = GameConfig(setting="sync_counter", round_time_limit_s=0.1)
    editor = PromptEditor()
    editor.configure(config)
    first_plan = dict(editor.plan)
    first_prompts = editor.resolve()
    editor.display()
    assert editor.resolve(config) == first_prompts
    assert editor.plan == first_plan

    next_plan = make_plan(config)
    assert next_plan["namespace"] != first_plan["namespace"]
    next_prompts = editor.resolve(config, plan=next_plan)
    assert editor.plan == next_plan
    assert editor.resolve(config) == next_prompts
    for prompt in next_prompts.values():
        assert next_plan["namespace"] in prompt
        assert first_plan["namespace"] not in prompt
    assert next_plan["namespace"] in display_backend.update_display.call_args.args[0]
    next_plan["fuzz_tags"][0] = "outside-edit"
    assert editor.plan["fuzz_tags"][0] != "outside-edit"


@pytest.mark.parametrize("initial,next_setting", [("async_counter", "sync_counter"),
                                                   ("sync_counter", "async_counter")])
def test_config_switch_updates_existing_preview_and_preserves_edits(display_backend, initial, next_setting):
    additions = {"alice": "Use short labels.", "bob": "Keep your notes brief."}
    transform = lambda role, text: text + f"\nCustom transform for {role}."
    editor = PromptEditor(additions=additions, transform=transform)
    config = editor.configure(GameConfig(setting=initial))
    editor.display()
    original_id = editor.display_id
    editor.configure(replace(config, setting=next_setting))
    prompts = editor.resolve()
    for role in ("alice", "bob"):
        assert additions[role] in prompts[role]
        assert f"Custom transform for {role}." in prompts[role]
        assert "Alice chooses her own color." not in prompts[role]
        assert "Alice is privately assigned a color" in prompts[role]
        assert ("shared 180-second deadline" in prompts[role]) == (next_setting == "sync_counter")
    assert editor.additions is additions
    assert editor.transform is transform
    assert display_backend.display.call_count == 1
    assert display_backend.update_display.call_count == 2
    for call in display_backend.update_display.call_args_list:
        assert call.kwargs["display_id"] == original_id
        assert next_setting in call.args[0]
        assert "Color assignment: uniform and independent each round; repetitions allowed" in call.args[0]
        assert "choice=" not in call.args[0]
    updated = display_backend.update_display.call_args.args[0]
    assert "Use short labels." in updated and "Custom transform for bob." in updated


def test_resolve_rebuilds_directly_from_new_config_without_preview_cell(display_backend):
    editor = PromptEditor()
    editor.display(GameConfig(setting="guessing_only"))
    result = editor.resolve(GameConfig(setting="sync_counter"))
    assert editor.config.choice == "predetermined"
    assert "Alice chooses her own color." not in result["bob"]
    assert "Alice is privately assigned a color" in result["bob"]
    assert "shared 180-second deadline" in result["bob"]
    assert "sync_counter" in display_backend.update_display.call_args.args[0]


def test_mutated_additions_and_transform_apply_on_next_resolve():
    editor = PromptEditor()
    editor.configure(GameConfig())
    editor.additions["alice"] = "New Alice instruction."
    editor.transform = lambda role, text: text.replace("color agreement game", "color matching task")
    prompts = editor.resolve()
    assert "New Alice instruction." in prompts["alice"]
    assert "New Alice instruction." not in prompts["bob"]
    assert all("color matching task" in text for text in prompts.values())


@pytest.mark.parametrize("setting", ["async_counter", "sync_counter"])
def test_current_permissions_and_role_specific_up_example(setting, display_backend):
    editor = PromptEditor()
    editor.display(GameConfig(setting=setting))
    prompts = editor.resolve()
    assert "only Alice can increment" in prompts["alice"]
    assert "You have read access only." in prompts["bob"]
    assert "returns HTTP 403" in prompts["bob"]
    for role in ("alice", "bob"):
        actions = [json.loads(line) for line in prompts[role].splitlines() if line.startswith('{"action":')]
        has_increment = any(action.get("url", "").endswith("/up") for action in actions)
        assert has_increment == (role == "alice")
    assert "Alice: read and increment. Bob: read only." in display_backend.display.call_args.args[0]


def test_switching_to_baseline_removes_tool_examples_and_updates_permissions(display_backend):
    editor = PromptEditor()
    editor.display(GameConfig(setting="sync_counter"))
    editor.configure(GameConfig(setting="guessing_only"))
    prompts = editor.resolve()
    assert all('"action":"get"' not in text and '/up' not in text for text in prompts.values())
    assert "Neither player has counter access." in display_backend.update_display.call_args.args[0]


def test_realtime_preview_keeps_clock_and_final_permissions(display_backend):
    editor = PromptEditor()
    editor.display(GameConfig(setting="sync_counter", rounds=1))
    prompts = editor.resolve()
    assert all("180-second deadline" in text for text in prompts.values())
    assert all("During the final action, only choose is available." in text for text in prompts.values())
    assert all("During play, get, wait, and choose are available." in text for text in prompts.values())
    assert all("All actions, including final color choices, must complete before that deadline." in text
               for text in prompts.values())
    assert "sync_counter" in display_backend.display.call_args.args[0]
    assert "mode=" not in display_backend.display.call_args.args[0]


def test_selecting_sync_counter_updates_preview_to_three_minutes_including_answers(display_backend):
    editor = PromptEditor()
    config = editor.configure(GameConfig(setting="async_counter"))
    editor.display()
    editor.configure(replace(config, setting="sync_counter"))
    prompts = editor.resolve()
    assert all("shared 180-second deadline" in text for text in prompts.values())
    assert all("There is no extra time for final choices" in text for text in prompts.values())
    markup = display_backend.update_display.call_args.args[0]
    assert "sync_counter" in markup
    assert "mode=" not in markup
    assert "180 seconds total, including final answers" in markup
    editor.configure(replace(config, setting="guessing_only"))
    assert "Round limit:" not in display_backend.update_display.call_args.args[0]


def test_old_config_gets_new_defaults_and_keeps_user_settings():
    @dataclass(frozen=True)
    class OldConfig:
        setting: str = "async_counter"
        choice: str = "free"
        rounds: int = 2
        seed: int = 77

    old = OldConfig()
    current = normalize_config(old)
    assert isinstance(current, GameConfig)
    assert current.choice == "predetermined" and current.rounds == 2 and current.seed == 77
    assert not hasattr(current, "simultaneous_mode") and not current.realtime
    assert current.round_time_limit_s == 180
    assert old == OldConfig()
    assert "Alice is privately assigned a color" in PromptEditor().resolve(old)["alice"]
    assert "Alice chooses her own color." not in PromptEditor().resolve(old)["alice"]


def test_saved_config_mapping_ignores_only_derived_fields():
    config = GameConfig()
    assert normalize_config(config.to_dict()).to_dict() == config.to_dict()
    with pytest.raises(TypeError):
        normalize_config({**config.to_dict(), "typo_rounds": 4})
    with pytest.raises(TypeError):
        normalize_config(object())


def test_legacy_final_timeout_is_removed_without_losing_other_settings():
    @dataclass(frozen=True)
    class LegacyTimedConfig:
        setting: str = "sync_counter"
        simultaneous_mode: str = "realtime"
        choice: str = "free"
        rounds: int = 2
        round_time_limit_s: float = 17
        final_timeout_s: float = 60

    old = LegacyTimedConfig()
    current = normalize_config(old)
    assert isinstance(current, GameConfig)
    assert current.realtime and current.rounds == 2 and current.round_time_limit_s == 17
    assert not hasattr(current, "final_timeout_s")
    assert not hasattr(current, "simultaneous_mode")
    assert old.final_timeout_s == 60
    editor = PromptEditor(additions={"alice": "Keep this instruction."})
    prompts = editor.resolve(old)
    assert "shared 17-second deadline" in prompts["alice"]
    assert "Keep this instruction." in prompts["alice"]
    assert "There is no extra time for final choices" in prompts["alice"]


def test_realtime_saved_config_roundtrip_and_legacy_mapping_conversion():
    config = GameConfig(setting="sync_counter", rounds=2)
    saved = config.to_dict()
    assert saved["round_deadline_includes_final"] is True
    assert normalize_config(saved) == config
    assert normalize_config({**saved, "final_timeout_s": 60}) == config


@pytest.mark.parametrize("legacy_mode", ["auto", "steps", "realtime"])
def test_legacy_mode_is_removed_and_sync_uses_the_same_clock(legacy_mode, display_backend):
    legacy = {"setting": "sync_counter", "simultaneous_mode": legacy_mode,
              "round_time_limit_s": 17, "rounds": 2}
    current = normalize_config(legacy)
    assert current.realtime and current.round_time_limit_s == 17
    assert not hasattr(current, "simultaneous_mode")
    assert "simultaneous_mode" not in current.to_dict()
    editor = PromptEditor()
    editor.display(legacy)
    assert all("shared 17-second deadline" in prompt for prompt in editor.resolve().values())
    assert "mode=" not in display_backend.display.call_args.args[0]


def test_preview_escapes_custom_text_and_reuses_display_id(display_backend):
    editor = PromptEditor(additions={"alice": '<script>alert("x")</script>'})
    editor.display(GameConfig())
    editor.display()
    assert display_backend.display.call_count == 2
    assert display_backend.update_display.call_count == 0
    rendered = display_backend.display.call_args.args[0]
    assert '<script>' not in rendered and '&lt;script&gt;' in rendered
    assert all(call.kwargs['display_id'] == editor.display_id
               for call in display_backend.display.call_args_list)


@pytest.mark.parametrize("transform", [lambda role, text: None, lambda role, text: "", lambda role, text: "  "])
def test_empty_or_nonstring_transforms_fail_before_run(transform):
    with pytest.raises(ValueError, match="nonempty string"):
        PromptEditor(transform=transform).resolve(GameConfig())


def test_invalid_editor_inputs_are_rejected():
    with pytest.raises(ValueError, match="Configure the editor"):
        PromptEditor().resolve()
    with pytest.raises(ValueError, match="only alice and bob"):
        PromptEditor(additions={"charlie": "x"}).configure(GameConfig())
    with pytest.raises(TypeError, match="must be a string"):
        PromptEditor(additions={"alice": 3}).configure(GameConfig())
    with pytest.raises(TypeError, match="callable"):
        PromptEditor(transform="text").configure(GameConfig())


def test_reload_rebinds_package_and_normalizes_old_objects_in_isolated_process():
    # Module reload changes class identities. Keep it out of the pytest process,
    # where other test modules have already imported their GameConfig class.
    code = r'''
from experiments.color_game import GameConfig as OldConfig
from experiments.color_game.notebook import PromptEditor, normalize_config, reload_game
import experiments.color_game.notebook as helper
import experiments.color_game.game as game
import experiments.color_game.prompts as prompts
import experiments.color_game.environment as environment
old = OldConfig(rounds=2, seed=91)
editor = PromptEditor(additions={"alice": "Retain this edit."})
editor.configure(old)
original_type = type(editor)
original_display_id = editor.display_id
api = reload_game()
assert api.GameConfig is not OldConfig
assert game.GameConfig is api.GameConfig
assert prompts.GameConfig is api.GameConfig
assert game.CounterEnvironment is environment.CounterEnvironment
assert api.run_rollout is game.run_rollout
assert helper.PromptEditor is original_type
current = normalize_config(old)
assert type(current) is api.GameConfig
assert current.choice == "predetermined" and current.rounds == 2 and current.seed == 91
assert type(editor.configure(old)) is api.GameConfig
assert editor.display_id == original_display_id
assert "Retain this edit." in editor.resolve()["alice"]
'''
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                            text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
