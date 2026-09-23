"""Named selections of existing wiki conditions, with optional live-run budgets."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import re
from typing import Iterable

import yaml

from .wiki import HINTS, Cut, Spec, cut_config
from .environment_modes import SUPPORTED_MODES as MODES


@dataclass(frozen=True)
class Arm:
    id: str
    variant: str | None
    mode: str
    max_tokens: int | None = None
    max_turns: int | None = None
    num_live_problems: int | None = None
    wiki_write_instructions: bool = False
    hint: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", self.id):
            raise ValueError("arm id must start with a letter or digit and contain only letters, digits, '_' or '-'")
        if self.variant == "base":
            object.__setattr__(self, "variant", None)
        if self.variant is not None and (not isinstance(self.variant, str) or not self.variant):
            raise ValueError(f"arm {self.id!r}: variant must be 'base' or an existing variant name")
        if self.mode not in MODES:
            raise ValueError(f"arm {self.id!r}: mode must be one of {', '.join(MODES)}")
        if self.hint is not None and self.hint not in HINTS:
            raise ValueError(f"arm {self.id!r}: hint must be one of {', '.join(HINTS)}")
        if type(self.wiki_write_instructions) is not bool:
            raise ValueError(f"arm {self.id!r}: wiki_write_instructions must be a boolean")
        for field in ("max_tokens", "max_turns", "num_live_problems"):
            value = getattr(self, field)
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError(f"arm {self.id!r}: {field} must be a positive integer")

    def to_dict(self) -> dict:
        return {**asdict(self), "variant": self.variant or "base"}


def load_arms(path: str | Path, only: Iterable[str] | None = None) -> list[Arm]:
    """Read ``arms: [...]`` and optionally select IDs, retaining declaration order."""
    document = yaml.safe_load(Path(path).read_text())
    if not isinstance(document, dict) or set(document) != {"arms"}:
        raise ValueError("arm file must contain exactly one top-level key: 'arms'")
    rows = document["arms"]
    if not isinstance(rows, list) or not rows:
        raise ValueError("'arms' must be a nonempty list")
    required = {"id", "variant", "mode", "hint"}
    allowed = required | {"max_tokens", "max_turns", "num_live_problems", "wiki_write_instructions"}
    arms = []
    seen = set()
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f"arm entry {index} must be a mapping")
        missing, unknown = required - row.keys(), row.keys() - allowed
        if missing or unknown:
            raise ValueError(f"arm entry {index}: missing keys {sorted(missing)}; unknown keys {sorted(map(str, unknown))}")
        if row["hint"] not in HINTS:
            raise ValueError(f"arm entry {index}: hint must be one of {', '.join(HINTS)}")
        arm = Arm(**row)
        if arm.id in seen:
            raise ValueError(f"duplicate arm id: {arm.id!r}")
        seen.add(arm.id)
        arms.append(arm)
    if only is not None:
        selected = set(only)
        if not selected:
            raise ValueError("select at least one arm id")
        unknown = selected - seen
        if unknown:
            raise ValueError(f"unknown arm id(s): {', '.join(sorted(unknown))}")
        arms = [arm for arm in arms if arm.id in selected]
    return arms


def validate_arms(arms: list[Arm], spec: Spec, cuts: dict[int, Cut]) -> None:
    """Validate all selections against every cut before preparing or executing work."""
    if not arms:
        raise ValueError("select at least one arm")
    seen = set()
    for arm in arms:
        if not isinstance(arm, Arm):
            raise ValueError("arms must contain Arm records")
        if arm.id in seen:
            raise ValueError(f"duplicate arm id: {arm.id!r}")
        seen.add(arm.id)
        for cut in cuts.values():
            try:
                config = cut_config(spec, cut, arm.variant)
            except KeyError as exc:
                raise ValueError(f"arm {arm.id!r}: {exc.args[0]}") from exc
            episode = config.get("episode")
            if not isinstance(episode, dict) or not episode:
                raise ValueError(f"arm {arm.id!r}: {cut.page_id}@{cut.seq} has no episode block")
            available = len(episode.get("upcoming", []))
            if arm.num_live_problems is not None and arm.num_live_problems > available:
                raise ValueError(
                    f"arm {arm.id!r}: num_live_problems={arm.num_live_problems} exceeds "
                    f"the {available} available upcoming problems for {cut.page_id}@{cut.seq}"
                )
