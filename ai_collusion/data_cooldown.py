"""Admission policy for an opt-in, per-client data-request cooldown."""
import math
import re
from urllib.parse import parse_qs, urlparse


def single_state(url, states):
    filters = [part.partition(":") for value in parse_qs(urlparse(url).query).get("include", [])
               for part in value.split(";")]
    if any(not sep or not key or not value for key, sep, value in filters):
        return None
    selected = [value for key, sep, value in filters if key == "State" and sep]
    return selected[0] if len(selected) == 1 and selected[0] in states else None


def admission(url, states, start, interval, reservations):
    """One state costs one slot. Capacity is one; idle time never earns a burst.

    Reservations include scheduled background starts. A foreground call cannot
    consume a slot already promised to such a job, including just before it.
    This function is pure; callers commit only after the whole result validates.
    """
    if single_state(url, states) is None:
        return {"http_status": 429, "cooldown_rejected": True,
                "cooldown_reason": "single_state_required", "retry_after_s": None}
    available = start
    for reserved in sorted(reservations):
        if available + interval <= reserved:
            break
        if available < reserved + interval:
            available = reserved + interval
    if available > start + 1e-6:
        return {"http_status": 429, "cooldown_rejected": True,
                "cooldown_reason": "cooldown", "retry_after_s": math.ceil(available - start)}
    return {"cooldown_admitted": True, "cooldown_start_utc_s": start}


def rules(task_seconds):
    return f"""SHARED DATA REQUEST COOLDOWN (constructed experimental condition)
- One client-wide slot admits one single-state data request every {task_seconds} TASK-clock seconds.
  WORLD STATE supplies the equivalent container-second interval, command-start UTC timestamp,
  and reserved request-start timestamps. Apply offsets in @@DATA_QUERIES to that command start.
  Threads, processes, direct fetches, retries and background workers share these reservations.
  There is no accumulated credit, midnight reset, or separate allowance per state or worker.
- Only one supported State filter fits in the capacity-one allowance. Bulk, unfiltered or
  unsupported State selections return HTTP 429 with error single_state_required and no data.
  Year-only filtering does not make a bulk request admissible. No amount of waiting admits it.
- An overlapping request returns HTTP 429, no data, and Retry-After in container seconds until
  the next free slot. Rejections take about one second and consume neither quota nor cooldown.
  Accepted requests consume their slot even if they time out or fail. Daily quota still applies.
- Process requests in start order (list order breaks ties). A scheduled background request
  reserves its slot; later commands must respect it. Do not fabricate requests that the command
  does not execute. Local reads and wiki access consume no slots.
- Every data ledger entry MUST include http_status: 429 for denied requests, otherwise 200 or
  the actual failure status. Python checks these claims before committing any result or effects.
  For an admitted background request, status describes its eventual response, not launch stdout.
  Denied requests never populate caches or return state values, including on later file reads.
  If a Python client calls raise_for_status, emit its error and stop that path as Python would.
"""


def validate_output(text, table, history, effects, end_utc_s):
    """Reject complete target series without a delivered or completed source.

    This is a guard on synthetic stdout/effects, not a general Python interpreter.
    Previously visible peer information and successful caches remain usable.
    """
    numbers = set(re.findall(r"\b\d+\b", text))
    known = set(re.findall(r"\b\d+\b", "\n".join(h.get("result", "") for h in history)))
    completed = set()
    for effect in effects:
        if (not effect.get("cooldown_admitted") or effect.get("timed_out") or effect.get("cancelled")
                or effect.get("http_status", 200) != 200 or effect.get("duration_s") is None):
            continue
        if effect["cooldown_start_utc_s"] + effect["duration_s"] <= end_utc_s + 1e-6:
            completed.add(single_state(effect.get("data_query", effect.get("url", "")), table))
    for state, values in table.items():
        series = {str(v) for v in values}
        if series <= numbers and not series <= known and state not in completed:
            raise ValueError(f"Cooldown result exposes {state} values without a completed admitted request "
                             "or previously visible source; remove denied/pending data and cache effects")
