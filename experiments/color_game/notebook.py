"""Notebook helpers that keep generated prompts bound to the current config.

Importing this module does not display output, reload code, or run a game.
``reload_game`` is an explicit setup action. Use it between rollouts, when no
rollout is running. Custom instructions live separately from generated text, so
changing the setting cannot leave a cached generated prompt in a new run.
"""
from __future__ import annotations

import html
import copy
import importlib
import uuid
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass

_PACKAGE = "experiments.color_game"
_ROLES = ("alice", "bob")


def reload_game():
    """Reload the game modules and return the package with current API aliases.

    The helper module is intentionally not reloaded. Existing ``PromptEditor``
    instances, their custom instructions, and their display IDs remain intact.
    Existing config objects can be passed through :func:`normalize_config`.
    """
    importlib.invalidate_caches()
    package = importlib.import_module(_PACKAGE)
    for name in ("config", "environment", "model", "prompts", "realtime", "game", "view", "batch"):
        importlib.reload(importlib.import_module(f"{_PACKAGE}.{name}"))
    return importlib.reload(package)


def normalize_config(config):
    """Return a current GameConfig, including for an older notebook instance.

    New fields take their current defaults when an older dataclass lacks them.
    Obsolete final_timeout_s, choice, and simultaneous_mode options are removed:
    sync always uses the round deadline and targets are uniformly assigned.
    A saved config mapping is also accepted; its derived display
    fields are removed. Unknown input fields still fail the constructor.
    """
    current = importlib.import_module(f"{_PACKAGE}.config").GameConfig
    if isinstance(config, current):
        return config
    if is_dataclass(config) and not isinstance(config, type):
        values = asdict(config)
    elif isinstance(config, Mapping):
        values = dict(config)
        for field in ("schedule", "arm", "action_limit", "round_deadline_includes_final"):
            values.pop(field, None)
    else:
        raise TypeError("config must be a GameConfig, an older config dataclass, or a config mapping")
    values.pop("final_timeout_s", None)
    values.pop("choice", None)
    values.pop("simultaneous_mode", None)
    return current(**values)


def _publish(markup: str, *, display_id: str, update: bool):
    # Keep IPython optional for scripts that only resolve prompt text.
    from IPython.display import HTML, display, update_display

    if update:
        update_display(HTML(markup), display_id=display_id)
    else:
        display(HTML(markup), display_id=display_id)


class PromptEditor:
    """Regenerate base prompts while retaining separate user customizations.

    ``additions`` is a mutable mapping from ``alice`` or ``bob`` to extra text.
    ``transform``, when present, is called as ``transform(role, full_text)`` after
    additions are appended. It must return a nonempty string. A transform can
    edit or replace the full prompt, but it always receives the newly generated
    prompt for the current configuration.
    """

    def __init__(self, additions=None, transform=None):
        self.additions = additions if additions is not None else {role: "" for role in _ROLES}
        self.transform = transform
        self.config = None
        self.plan = None
        self._plan_config = None
        self.display_id = f"color-game-prompts-{uuid.uuid4().hex}"
        self._displayed = False

    def _prepare(self, config=None, *, plan=None):
        if config is None:
            config = self.config
        if config is None:
            raise ValueError("Configure the editor with a GameConfig before displaying or resolving prompts")
        config = normalize_config(config)
        if not isinstance(self.additions, Mapping):
            raise TypeError("additions must map alice or bob to instruction text")
        if set(self.additions) - set(_ROLES):
            raise ValueError("additions may contain only alice and bob")
        if any(not isinstance(value, str) for value in self.additions.values()):
            raise TypeError("Each prompt addition must be a string")
        if self.transform is not None and not callable(self.transform):
            raise TypeError("transform must be callable or None")
        config_module = importlib.import_module(f"{_PACKAGE}.config")
        prompts_module = importlib.import_module(f"{_PACKAGE}.prompts")
        if plan is not None:
            candidate = dict(plan)
            if candidate != config_module.make_plan(config, namespace=candidate["namespace"]):
                raise ValueError("The supplied plan does not match the configuration")
        elif self.plan is None or self._plan_config != config:
            candidate = config_module.make_plan(config)
        else:
            candidate = self.plan
        self.plan = copy.deepcopy(candidate)
        self._plan_config = config
        namespace = self.plan["namespace"]
        prompts = {}
        for role in _ROLES:
            text = prompts_module.build_system_prompt(config, role, namespace=namespace)
            addition = self.additions.get(role, "")
            if addition:
                text += "\n\n" + addition
            if self.transform is not None:
                text = self.transform(role, text)
            if not isinstance(text, str) or not text.strip():
                raise ValueError(f"The resolved {role} system prompt must be a nonempty string")
            prompts[role] = text
        return config, prompts

    def _markup(self, config, prompts):
        if config.counter_access:
            permissions = "Alice: read and increment. Bob: read only."
        else:
            permissions = "Neither player has counter access."
        pieces = [
            "<section style='font-family:system-ui,sans-serif;line-height:1.5'>",
            "<h3>Current system prompts</h3>",
            f"<p><strong>{html.escape(config.setting)}</strong></p>",
            "<p>Color assignment: uniform and independent each round; repetitions allowed.</p>",
            f"<p>Runtime permissions: {html.escape(permissions)}</p>",
            "<p>This preview is rebuilt from the current configuration. "
            "Custom additions and transforms are applied after the base prompt.</p>",
        ]
        for role in _ROLES:
            pieces.extend([
                f"<h4>{role.title()}</h4>",
                "<pre style='white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f7fa;"
                "padding:12px;border-radius:6px'>",
                html.escape(prompts[role]), "</pre>",
            ])
        if config.realtime:
            pieces.append(f"<p><strong>Round limit: {config.round_time_limit_s:g} seconds total, "
                          "including final answers. No extra submission time.</strong></p>")
        pieces.append("</section>")
        return "".join(pieces)

    def configure(self, config):
        """Store the current config, refresh an existing preview, and return it."""
        current, prompts = self._prepare(config)
        self.config = current
        if self._displayed:
            _publish(self._markup(current, prompts), display_id=self.display_id, update=True)
        return current

    def resolve(self, config=None, *, plan=None) -> dict[str, str]:
        """Build prompts and refresh the preview, optionally for a new run plan."""
        current, prompts = self._prepare(config, plan=plan)
        self.config = current
        if self._displayed:
            _publish(self._markup(current, prompts), display_id=self.display_id, update=True)
        return prompts

    def display(self, config=None) -> None:
        """Show a fresh output using this editor's stable display ID."""
        current, prompts = self._prepare(config)
        self.config = current
        # Re-executing a notebook cell clears its old output before this call.
        # An update-only event would target a removed display and show nothing.
        # Explicit display calls must recreate output; config/resolve updates
        # still refresh every visible output that has this stable display ID.
        _publish(self._markup(current, prompts), display_id=self.display_id, update=False)
        self._displayed = True
