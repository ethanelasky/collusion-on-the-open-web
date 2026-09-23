"""Participants: one per model instance in a multi-agent episode.

The scheduler stays deliberately dumb. It asks each participant only two things: when it next wants
to act (`next_due`, in global container seconds) and to take one turn. Everything task-specific —
how the context is built, how a model response maps to a tool call, how local state (a task clock,
a secret, a guess) advances, whether to call a model at all — lives inside the participant. So the
two tracks implement their own participants against this one protocol and can be built in parallel:

  - replication (this repo): a participant wrapping the sector61 `World` (task clock, questions,
    grading) that reads/writes the SHARED wiki + counter media, so cohorts see each other. (Stage 2.)
  - covert_channel (experiments/covert_channel): a Sender that encodes a secret into the counter and
    a Receiver that reads it and guesses.

`take_turn` receives the shared media so a participant's tool calls resolve against global state
(use `coord.resolve(mediums, ...)` for URL calls); calls no medium owns are the participant's own
business (defer to an env model, treat as a local action, etc.).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol, Sequence, runtime_checkable

from .medium import Medium, resolve

_URL_RE = re.compile(r"https?://[^\s'\"<>)\]]+")


@dataclass
class Turn:
    agent_id: str
    at_global_s: float
    action: str                 # the raw action the participant took (a URL, a guess, a wait, ...)
    result: str                 # what it observed back
    source: str                 # "counter" | "wiki" | "local" | "model" | "model-error" | ...
    done: bool = False
    meta: dict = field(default_factory=dict)    # per-experiment extras: round outcome, guess, effects


@runtime_checkable
class Participant(Protocol):
    id: str
    def next_due(self) -> float | None: ...          # global seconds of next intended action; None = done/blocked
    def take_turn(self, mediums: Sequence[Medium], now_global_s: float) -> Turn: ...
    def done(self) -> bool: ...


@dataclass
class ScriptedParticipant:
    """A participant with a fixed action list and a fixed cadence; no model call.

    For tests, and for scripted cohorts in the real experiments (e.g. an 'ahead' cohort that relays
    R5 on a schedule, or a scripted receiver). Each action is a string: if it contains a URL it is
    resolved against the shared media (so a scripted agent's writes are seen by live agents); other
    actions are recorded as local (e.g. 'GUESS:red')."""

    id: str
    actions: list[str]
    start_s: float = 0.0
    step_s: float = 1.0
    i: int = 0

    def next_due(self) -> float | None:
        return self.start_s + self.i * self.step_s if self.i < len(self.actions) else None

    def done(self) -> bool:
        return self.i >= len(self.actions)

    def take_turn(self, mediums: Sequence[Medium], now_global_s: float) -> Turn:
        action = self.actions[self.i]
        self.i += 1
        m = _URL_RE.search(action)
        if m:
            r = resolve(mediums, self.id, m.group(0), now_global_s)
            result, source = (r.body, r.source) if r else ("HTTP 404 (no medium owns this URL)", "local")
            meta = {"effects": r.effects} if r else {}
        else:
            result, source, meta = "ok", "local", {}
        return Turn(self.id, now_global_s, action, result, source, done=self.done(), meta=meta)
