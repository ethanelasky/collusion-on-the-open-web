"""Validate a simulated shell command's prefix before a question cancels it."""
import re

_CANCEL = re.compile(r'^@@SHELL_CANCELLED\s*$', re.MULTILINE)
_ELAPSED = re.compile(r'^[ \t]*@@ELAPSED[ \t]+(\d+(?:\.\d+)?)[ \t]*s?[ \t]*$', re.MULTILINE)

RULES = """SHELL CANCELLATION
- When NEW CALL supplies a SHELL EXECUTION BOUNDARY, a new question cancels this
  shell call at that boundary. Simulate only the command prefix before then.
- If the command is still running at the boundary, emit @@SHELL_CANCELLED on its
  own line and set @@ELAPSED to the supplied limit (round to the nearest second).
  Return only stdout/stderr already produced. Do not execute trailing commands,
  publish later wiki/counter effects, or claim that the command completed.
- Cancellation terminates this call and its unfinished child processes, including
  downloads it started and is waiting for. They never resume or produce results.
  Preserve files and effects already completed. Background jobs from earlier,
  already-returned shell calls are unaffected.
- List only data requests actually started before cancellation in @@DATA_QUERIES;
  omit future queue entries. Keep their original client timeout, if any. The
  harness truncates unfinished requests at the boundary and records cancellation.
- A short command, including a background launch that returns before the boundary,
  completes normally: no cancellation marker. Its background jobs can continue.
"""


def validate_prefix(text, queries, query_elapsed, boundary):
    """Reject future results before committing effects; return a cancelled prefix.

    Request outcomes are derived from the ledger's timings, not the simulator's
    claims about which downloads finished. No new ledger fields are required.
    """
    markers = list(_CANCEL.finditer(text))
    if boundary is None:
        if markers:
            raise ValueError('Unexpected @@SHELL_CANCELLED without an execution boundary')
        return text, queries, query_elapsed, False
    elapsed = list(_ELAPSED.finditer(text))
    if len(elapsed) != 1 or len(markers) > 1:
        raise ValueError('Shell execution requires one @@ELAPSED and at most one @@SHELL_CANCELLED')
    reported = float(elapsed[0][1])
    limit = boundary['limit_s']
    if not markers:
        # @@ELAPSED is rounded to integer seconds; the data ledger is exact.
        if reported > round(limit) or query_elapsed > limit + 1:
            raise ValueError('Shell command crosses the new-question boundary; return its cancelled prefix with @@SHELL_CANCELLED')
        return text, queries, query_elapsed, False
    if abs(reported - limit) > 1:
        raise ValueError('Cancelled shell @@ELAPSED must equal the execution boundary')
    effects = []
    for query in queries:
        effect = dict(query)
        if effect['start_s'] >= limit:
            raise ValueError('A cancelled shell cannot start requests at or after its boundary')
        duration = effect['duration_s']
        remaining = limit - effect['start_s']
        # Unknown endpoint durations cannot establish completion without an
        # observed timeout; keep them explicitly cancelled at the boundary.
        if duration is None or duration > remaining:
            effect.update(duration_s=remaining, cancelled=True, timed_out=False)
        effects.append(effect)
    return _CANCEL.sub('', text), effects, min(query_elapsed, limit), True
