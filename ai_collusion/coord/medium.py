"""Shared media for multi-agent episodes.

A *Medium* is a stateful resource that several agents touch through tool calls, and its state is
GLOBAL: one instance is shared by every participant, so a write by one agent is visible to the next
agent that reads. That is the whole difference from the single-agent env, where each `World` owns its
own state. The agents' only side channel is an HTTP GET, so a medium answers a URL; `resolve()`
returns a `MediumResult`, or `None` if this medium does not own that URL (the caller then tries the
next medium, or an experiment-specific handler such as an env model).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence, runtime_checkable

from ..counter import Counter, parse_counter_url


@dataclass
class MediumResult:
    body: str                                   # the raw text the GET returns to the agent
    source: str                                 # "counter" | "wiki" | ...
    effects: list[dict] = field(default_factory=list)   # structured record of any state change, for metrics


@runtime_checkable
class Medium(Protocol):
    name: str
    def resolve(self, agent_id: str, url: str, at_global_s: float) -> MediumResult | None: ...


def resolve(mediums: Sequence[Medium], agent_id: str, url: str, at_global_s: float) -> MediumResult | None:
    """First medium that owns `url` wins; None if none do."""
    for m in mediums:
        r = m.resolve(agent_id, url, at_global_s)
        if r is not None:
            return r
    return None


class CounterMedium:
    """Adapter exposing the canonical `ai_collusion.counter.Counter` as a shared Medium. All state
    lives in the wrapped `Counter`, so a shared counter is a shared instance and an isolated one is a
    separate instance. Pass an existing `Counter` to share it across several media/agents."""

    name = "counter"

    def __init__(self, counter: Counter | None = None) -> None:
        self.counter = counter or Counter()

    # convenience views used by callers/tests
    @property
    def counts(self) -> dict[tuple[str, str], int]:
        return self.counter.counts

    @property
    def counters(self) -> dict[tuple[str, str], int]:   # alias
        return self.counter.counts

    @property
    def hits(self) -> list[dict]:
        return self.counter.hits

    def resolve(self, agent_id: str, url: str, at_global_s: float) -> MediumResult | None:
        body = self.counter.fetch(url, meta={"agent_id": agent_id, "at_global_s": at_global_s})
        if body is None:
            return None
        ns, key, op = parse_counter_url(url)            # not None: fetch returned a body
        return MediumResult(body, "counter",
                            [{"counter": {"ns": ns, "key": key, "op": op, "value": self.counter.get(ns, key)}}])
