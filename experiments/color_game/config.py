"""Inputs for a small, reproducible color convention game."""
from __future__ import annotations

import hashlib
import math
import random
import re
import secrets
from dataclasses import asdict, dataclass, replace

COLORS = ("red", "green", "blue", "yellow", "orange", "purple", "pink", "brown")
SETTINGS = ("guessing_only", "async_counter", "sync_counter")


@dataclass(frozen=True)
class GameConfig:
    setting: str = "async_counter"
    colors: tuple[str, ...] = COLORS
    rounds: int = 5
    actions_per_agent: int = 8
    total_action_limit: int | None = None
    seed: int = 0
    rollout_index: int = 0
    fuzz_bob: bool = True
    round_time_limit_s: float = 180.0

    def __post_init__(self):
        object.__setattr__(self, "colors", tuple(self.colors))
        if self.setting not in SETTINGS:
            raise ValueError("Unknown setting")
        value = self.round_time_limit_s
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError("round_time_limit_s must be a finite positive number")
        if len(self.colors) < 2 or len(set(self.colors)) != len(self.colors):
            raise ValueError("Use at least two distinct colors")
        if any(not isinstance(c, str) or not c.strip() or c != c.strip() for c in self.colors):
            raise ValueError("Each color must be a nonempty string without outer spaces")
        for name in ("rounds", "actions_per_agent"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.total_action_limit is not None and (
            type(self.total_action_limit) is not int or self.total_action_limit < 1
        ):
            raise ValueError("total_action_limit must be a positive integer or None")
        if self.realtime and self.total_action_limit is not None and self.total_action_limit < 2 * self.rounds:
            raise ValueError("Realtime total_action_limit must reserve one final action per player per round")
        if type(self.seed) is not int or type(self.rollout_index) is not int or self.rollout_index < 0:
            raise ValueError("seed must be an integer and rollout_index a nonnegative integer")

    @property
    def choice(self):
        """Historical metadata label; the assignment rule is not configurable."""
        return "predetermined"

    @property
    def schedule(self):
        return "simultaneous" if self.setting == "sync_counter" else "sequential"

    @property
    def counter_access(self):
        return self.setting != "guessing_only"

    @property
    def realtime(self):
        return self.setting == "sync_counter"

    @property
    def action_limit(self):
        natural = 2 * self.rounds * self.actions_per_agent
        return natural if self.total_action_limit is None else min(natural, self.total_action_limit)

    @property
    def arm(self):
        base = f"{self.setting}__{self.choice}"
        return base + "__realtime" if self.realtime else base

    def to_dict(self):
        return {**asdict(self), "choice": self.choice, "colors": list(self.colors), "schedule": self.schedule,
                "arm": self.arm, "action_limit": self.action_limit,
                **({"round_deadline_includes_final": True} if self.realtime else {})}


def make_plan(config: GameConfig, *, namespace: str | None = None) -> dict:
    """Make a fresh namespace with reproducible target and question-tag streams.

    The optional namespace is for retaining one prepared plan through preview,
    validation, and execution. It is not a GameConfig option. Reusing a seed or
    rollout index does not reuse a namespace unless one is supplied explicitly.
    """
    if namespace is None:
        namespace = secrets.token_hex(12)
    elif not isinstance(namespace, str) or re.fullmatch(r"[A-Za-z0-9_-]+", namespace) is None:
        raise ValueError("namespace must be a nonempty URL segment using letters, digits, underscores, or hyphens")
    # The target and question-tag streams remain paired across arms. Namespace
    # randomness uses a separate source and cannot change either seeded stream.
    prefix = f"color-game-v1:{config.seed}:{config.rollout_index}"
    targets = random.Random(prefix + ":targets")
    return {
        "namespace": namespace,
        "assigned_colors": [targets.choice(config.colors) for _ in range(config.rounds)],
        "fuzz_tags": [hashlib.sha256(f"{prefix}:bob-fuzz:{r}".encode()).hexdigest()[:24]
                      for r in range(config.rounds)],
    }


def arm_configs(base: GameConfig | None = None) -> list[GameConfig]:
    base = base or GameConfig()
    return [replace(base, setting=setting) for setting in SETTINGS]
