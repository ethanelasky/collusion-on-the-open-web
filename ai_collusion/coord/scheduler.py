"""Global-time-ordered interleaving of participants over shared media.

At each step the participant whose next intended action is earliest — smallest `next_due`, in
global container seconds — takes a turn, and its writes to the shared media are immediately visible
to every later turn. That single rule gives both regimes we need:

  - round-robin (a symmetric game): every participant advances `next_due` by the same step, so turns
    alternate A, B, A, B — ties are broken by declaration order.
  - staggered cohorts (the relay): participants on offset task-clocks map to different global
    `next_due` times, so an 'ahead' agent reaches R5 — and can post/relay it — strictly before a
    'behind' agent's next read. The self-sacrificial-relay setup falls out of the ordering; nothing
    special-cases it.

The scheduler knows nothing about clocks, tasks, models, or media contents; all of that lives in
the participants and the media. It just drives whoever is due next until everyone is done or a cap
is hit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .medium import Medium
from .participant import Participant, Turn


@dataclass
class EpisodeResult:
    turns: list[Turn] = field(default_factory=list)
    end_reason: str = ""


def run_episode(
    participants: Sequence[Participant],
    mediums: Sequence[Medium],
    *,
    max_turns: int = 200,
    max_global_s: float | None = None,
) -> EpisodeResult:
    out = EpisodeResult()
    order = {p.id: i for i, p in enumerate(participants)}    # stable tie-break for equal next_due
    while True:
        active = [p for p in participants if not p.done() and p.next_due() is not None]
        if not active:
            out.end_reason = "all_done"
            return out
        p = min(active, key=lambda part: (part.next_due(), order[part.id]))
        now = p.next_due()
        assert now is not None                              # guaranteed by the `active` filter
        if max_global_s is not None and now > max_global_s:
            out.end_reason = "max_global_s"
            return out
        out.turns.append(p.take_turn(mediums, now))
        if len(out.turns) >= max_turns:
            out.end_reason = "max_turns"
            return out
