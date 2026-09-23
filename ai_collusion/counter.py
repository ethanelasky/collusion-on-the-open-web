"""Deterministic, in-memory counterapi.dev.

The public, no-login counter the wiki agents used to coordinate. Real semantics:
  GET .../v1/<namespace>/<key>/up   increments the key and returns the new count
  GET .../v1/<namespace>/<key>      reads the current count (0 if the key was never touched)

This is the single canonical implementation of that behavior, with no dependencies. State is a plain
dict, so a *shared* counter is just a shared `Counter` instance and an *isolated* one is a separate
instance — which is exactly the knob the covert-channel experiment's blocked-channel control needs.
The wiki environment and standalone experiments share this implementation; `coord.CounterMedium`
is a thin adapter over it for the multi-agent scheduler.

Not modeled here (deliberately, until an experiment needs it): background increments over time and a
teardown event that freezes them. `Counter` changes state only when something calls `up`/`fetch`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from urllib.parse import urlparse


def parse_counter_url(url: str) -> tuple[str, str, str] | None:
    """(namespace, key, op) for a counterapi.dev URL, or None if `url` is not one.

    op is "up" (increment) or "read". Trailing "/up" selects increment; a leading "v1" path segment
    is ignored; a query string (e.g. a cache-buster ?x=...) is ignored."""
    u = urlparse(url)
    if "counterapi.dev" not in u.netloc:
        return None
    segs = [s for s in u.path.split("/") if s]          # e.g. ['v1', '<ns>', '<key>', 'up'?]
    op = "read"
    if segs and segs[-1].lower() == "up":
        op, segs = "up", segs[:-1]
    if segs and segs[0].lower() == "v1":
        segs = segs[1:]
    ns = segs[0] if segs else "default"
    key = segs[1] if len(segs) > 1 else "default"
    return ns, key, op


def counter_body(ns: str, key: str, count: int) -> str:
    """The JSON body a counterapi.dev GET returns, wrapped in the same GET envelope the env uses so
    reads look identical whether they come through this counter, the wiki layer, or a mocked page."""
    return json.dumps(
        {"code": 200, "data": {"namespace": ns, "key": key, "count": count, "up_count": count}},
        separators=(",", ":"),
    )


@dataclass
class Counter:
    """A deterministic counter store. `up`/`get` are the direct API; `fetch` handles a full URL and
    returns the response body an HTTP GET would produce (or None if the URL isn't a counter URL)."""

    counts: dict[tuple[str, str], int] = field(default_factory=dict)
    hits: list[dict] = field(default_factory=list)     # append-only log: one entry per fetch()

    def get(self, ns: str, key: str) -> int:
        return self.counts.get((ns, key), 0)

    def up(self, ns: str, key: str) -> int:
        self.counts[(ns, key)] = self.get(ns, key) + 1
        return self.counts[(ns, key)]

    def fetch(self, url: str, *, meta: dict | None = None, read_only: bool = False) -> str | None:
        """Resolve a counterapi.dev GET: increment or read, log the hit (with any caller `meta`, e.g.
        an agent id or a timestamp), and return the response body. None if `url` is not a counter URL,
        so a caller can fall through to other resolvers. With read_only=True, preview the response
        without changing counts or recording a hit."""
        parsed = parse_counter_url(url)
        if parsed is None:
            return None
        ns, key, op = parsed
        count = self.get(ns, key) + (op == "up")
        if not read_only:
            if op == "up":
                count = self.up(ns, key)
            self.hits.append({"url": url, "ns": ns, "key": key, "op": op, "value": count, **(meta or {})})
        return f"--- GET {url} ---\nHTTP 200\n{counter_body(ns, key, count)}\n--- end ---"
