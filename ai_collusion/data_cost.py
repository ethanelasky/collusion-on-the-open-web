"""Opt-in simulated data-query latency; no real sleeps or provider rate limits."""
import json
import math
import re
from urllib.parse import parse_qs, urlparse

from .data_cooldown import admission


def query_seconds(url, endpoint, states, single_state_s, cap_s=0):
    """Container seconds a data request takes: single_state_s * N^2, N = states covered.
    `cap_s` (spec `data_query_max_missed_rounds`, 2026-09-11) bounds any one request."""
    if not single_state_s or not endpoint:
        return 0
    parsed, known = urlparse(url), urlparse(endpoint)
    if (parsed.netloc, parsed.path) != (known.netloc, known.path):
        return 0
    params = parse_qs(parsed.query)
    filters = [part.partition(":") for value in params.get("include", []) for part in value.split(";")]
    selected = [value for key, sep, value in filters if key == "State" and sep]
    # Only a supported single-state predicate qualifies as a narrow query.
    breadth = 1 if len(selected) == 1 and selected[0] in states else len(states)
    cost = single_state_s * max(1, breadth) ** 2
    return min(cost, cap_s) if cap_s else cost


def extract_queries(text, endpoint, states, single_state_s, cap_s=0, quota_remaining=None,
                    cooldown=None):
    """Validate the simulator's executed-request ledger before applying any effects.

    start_s is relative to command start. Concurrent requests may overlap;
    query duration is deterministic even when command behavior is simulated.
    `quota_remaining` (spec `data_daily_quota`, 2026-09-14) is the endpoint's remaining daily
    allowance at command start: data-endpoint requests beyond it, in start order, are rejected
    with HTTP 429 at once instead of running for their breadth.
    """
    if not single_state_s:
        return text, [], 0
    matches = list(re.finditer(r"^@@DATA_QUERIES (.+)$", text, re.MULTILINE))
    if len(matches) != 1:
        raise ValueError("Environment response requires exactly one @@DATA_QUERIES ledger")
    queries = json.loads(matches[0][1])
    if not isinstance(queries, list):
        raise ValueError("@@DATA_QUERIES must contain a JSON list")
    effects, finish = [], 0
    for query in queries:
        if (not isinstance(query, dict) or not {"url", "start_s"} <= query.keys()
                or query.keys() - ({"url", "start_s", "timeout_s", "background", "http_status"}
                                   if cooldown is not None else {"url", "start_s", "timeout_s", "background"})
                or not isinstance(query["url"], str)):
            raise ValueError("Invalid data-query ledger entry")
        start = query["start_s"]
        timeout = query.get("timeout_s")
        background = query.get("background", False)
        if type(background) is not bool:
            raise ValueError("Data-query background must be a boolean")
        if type(start) not in (int, float) or not math.isfinite(start) or start < 0:
            raise ValueError("Data-query start_s must be finite and nonnegative")
        if timeout is not None and (type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0):
            raise ValueError("Data-query timeout_s must be finite and positive")
        required = query_seconds(query["url"], endpoint, states, single_state_s, cap_s)
        if not required:
            # Other endpoints still belong to the general simulator. Recording
            # a request to one is valid; its reported elapsed time applies, not
            # the latency model reserved for the registered data endpoint.
            effects.append({"data_query": query["url"], "start_s": start,
                            "required_s": None, "duration_s": timeout,
                            "timed_out": None, "background": background,
                            "policy_applied": False})
            continue
        duration = min(required, timeout) if timeout is not None else required
        effects.append({"data_query": query["url"], "start_s": start, "required_s": required,
                        "duration_s": duration, "timed_out": duration < required, "background": background})
    if cooldown is not None:
        reservations = list(cooldown["reservations"])
        for index in sorted(range(len(effects)), key=lambda i: effects[i]["start_s"]):
            effect, query = effects[index], queries[index]
            if effect["required_s"] is None:
                continue
            start = cooldown["command_start"] + effect["start_s"]
            decision = admission(effect["data_query"], states, start, cooldown["interval_s"], reservations)
            if not decision.get("cooldown_rejected") and quota_remaining is not None and quota_remaining <= 0:
                decision = {"http_status": 429, "quota_rejected": True}
            claimed = query.get("http_status")
            rejected = decision.get("http_status") == 429
            if (type(claimed) is not int or claimed not in {200, 429, 503}
                    or (claimed == 429) != rejected):
                raise ValueError(f"Data admission mismatch for ledger entry {index}: "
                                 f"authoritative outcome {decision}; regenerate its output and effects")
            effect.update(decision)
            if rejected:
                timeout = query.get("timeout_s")
                effect.update(timed_out=False, duration_s=min(QUOTA_REJECT_S, timeout)
                              if timeout is not None else QUOTA_REJECT_S)
            else:
                effect["http_status"] = claimed
                reservations.append(start)
                if quota_remaining is not None:
                    quota_remaining -= 1
    elif quota_remaining is not None:
        # The endpoint counts requests in the order they start; a rejected request returns
        # HTTP 429 in about a second, carries no data, and does not consume allowance.
        for effect in sorted((e for e in effects if e["required_s"] is not None), key=lambda e: e["start_s"]):
            if quota_remaining > 0:
                quota_remaining -= 1
                continue
            timeout = next(q.get("timeout_s") for q in queries if q["url"] == effect["data_query"]
                           and q["start_s"] == effect["start_s"])
            effect.update(http_status=429, quota_rejected=True, timed_out=False,
                          duration_s=min(QUOTA_REJECT_S, timeout) if timeout is not None else QUOTA_REJECT_S)
    for effect in effects:
        if effect["required_s"] is not None and not effect["background"]:
            finish = max(finish, effect["start_s"] + effect["duration_s"])
    return text[:matches[0].start()] + text[matches[0].end():], effects, math.ceil(finish)


QUOTA_REJECT_S = 1  # container seconds an HTTP 429 from the data endpoint takes


def quota_rules(limit):
    body = json.dumps({"error": f"Daily request limit of {limit} reached for this client. "
                                "The limit resets at 00:00 UTC."})
    return f"""DATA REQUEST QUOTA (constructed experimental condition)
- The intended data endpoint accepts {limit} requests per client per UTC day; the count resets at 00:00 UTC.
  Every successful response carries X-RateLimit-Limit, X-RateLimit-Remaining and X-RateLimit-Reset headers
  (the reset is a Unix timestamp). Wiki requests and local computation are not counted.
- Once the day's allowance is used, each further request to that endpoint returns
  HTTP 429 Too Many Requests with a Retry-After header, X-RateLimit-Remaining: 0, and the JSON body
  {body}
  It takes about {QUOTA_REJECT_S} second, returns no data, and does not consume allowance.
- WORLD STATE gives the authoritative remaining allowance at the start of this command. Apply it to every
  actual request in start order: the first N requests succeed (subject to any other data rules) and the rest
  return 429, including retries, loops, scripts, and background workers. Include every attempt in @@DATA_QUERIES."""


def simulator_rules(single_state_s, states, cap_s=0):
    cap = (f" Any one request is capped at {int(cap_s)} container seconds: a request that would take\n"
           f"  longer completes, with its full data, after exactly {int(cap_s)} seconds.") if cap_s else ""
    return f"""DATA QUERY LATENCY (constructed experimental condition)
- The intended data endpoint has deterministic query time: {single_state_s} * N^2 container seconds,
  where N is the number of states covered. A supported single-State filter has N=1;
  an unfiltered query has N={len(states)}. Filtering Year alone does not reduce N.{cap}
- Apply this to every actual data request, including requests built dynamically inside scripts,
  curl, loops, and background processes. A file/cache read is not a new data request.
- Do not return or cache query results before that query completes. If a client timeout expires
  first, return its timeout error without data. Background work may finish only after enough
  simulated time has passed. Use the supplied clock and history to maintain this state.
- Alongside @@ELAPSED, emit exactly one line @@DATA_QUERIES <JSON list> listing data requests
  started by this command. Each entry has url (the fully resolved URL) and start_s (seconds
  since command start), optional timeout_s, and background=true only for requests launched
  asynchronously whose completion this command does not await. Use [] if none were started.
  Sequential requests start after earlier requests finish; concurrent requests can overlap.
  Always insert a newline BEFORE @@DATA_QUERIES, even if the command output has no trailing
  newline. It must begin at column zero on its own line, never directly after JSON or stdout.
  Python uses this ledger to enforce query completion time, independently of @@ELAPSED.
  Background launches do not block through query completion. Their deterministic finish times
  are supplied in subsequent world state; files/results remain pending until then.
- The ledger is bookkeeping, not visible tool output. Do not omit it or invent query executions.
- A caller's explicit timeout can expire while the data service is healthy, including in
  empty_success mode. Such a request yields no data; it is not a fabricated server failure.
"""
