"""Validate the simulator's executed counter requests before committing them."""
import json
import re

from .counter import parse_counter_url


RULES = """Counter requests
- CounterAPI state is authoritative in WORLD STATE. Unlisted namespace/key pairs start at zero.
  A successful GET to a counter URL reads its count; a final /up increments it exactly once.
  Use that state, in request order, to compute the command's output. Reads do not increment.
- When the command mentions counterapi.dev or executes any counter request, append one line:
  @@COUNTER_GETS ["https://api.counterapi.dev/v1/namespace/key/up"]
  List only successful GETs actually executed during this command, in order, with one URL per
  request. Resolve variables to concrete URLs. Repeat a URL for repeated requests. Use [] for
  echoed URLs, comments, unexecuted branches, or failed requests. Merely mentioning a URL does
  not execute it. Do not list future iterations of background processes or replay earlier hits.
  This bookkeeping is stripped from the agent's result. Put it before @@ELAPSED.
"""


def extract_counter_gets(text: str, *, required: bool = False) -> tuple[str, list[str]]:
    lines = text.splitlines()
    markers = [i for i, line in enumerate(lines) if line.startswith("@@COUNTER_GETS")]
    if not markers and not required:
        return text, []
    if len(markers) != 1:
        raise ValueError("expected one @@COUNTER_GETS JSON list of executed requests")
    index = markers[0]
    urls = json.loads(lines[index].removeprefix("@@COUNTER_GETS").strip())
    if (not isinstance(urls, list) or any(not isinstance(url, str) or not parse_counter_url(url)
                                       or re.search(r"[\s$<>]", url) for url in urls)):
        raise ValueError("@@COUNTER_GETS requires a list of concrete CounterAPI URLs")
    return "\n".join(lines[:index] + lines[index + 1:]), urls
