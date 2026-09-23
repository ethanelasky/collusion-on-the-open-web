"""Bounded recovery before execution; failed generations remain in the record."""
from collections import Counter
from dataclasses import asdict, replace
import copy
import math

from .env import parse_call
from .native_tools import enabled
from .preparation import stable_sha256
from .response_status import response_failure


class ToolProtocolError(ValueError):
    def __init__(self, message, attempts):
        super().__init__(message)
        self.attempts = attempts


def issue(response, native):
    raw_text = response.get("provider_text", response.get("text")) or ""
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    words = raw_text.split()
    repeated_words = len(words) >= 80 and len(set(words)) < len(words) / 10
    if (lines and max(Counter(lines).values()) >= 8) or repeated_words:
        return "repetition", "Do not repeat tool calls or simulate tool results. Emit exactly one native function call."
    failure = response_failure(response)
    if failure == "truncated":
        return "truncated", "The response exhausted its output budget before completion. Keep reasoning brief and emit one complete call."
    if failure in {"provider_error", "provider_blocked"}:
        return "provider_error", "The provider did not produce a usable response. Retry one complete call."
    if native:
        call = response.get("tool_call")
        if response.get("tool_error") or not call:
            return "native_call_missing", (
                'Emit exactly one native function call, not multiple calls or text/XML. '
                'Its arguments must be a JSON object with exactly one key, "arg", whose value is a string. '
                'For example, wait uses {"arg":"120"}, not {"arg":120}. '
                'No rejected call has been executed; choose just the next action.'
            )
    else:
        parsed = parse_call(response.get("text") or "")
        call = asdict(parsed) if parsed else None
    if call and call["tool"] == "wait":
        try:
            seconds = float(call["arg"])
            valid = math.isfinite(seconds) and seconds >= 1
        except (ValueError, TypeError):
            valid = False
        if not valid:
            return "invalid_wait", "wait requires a finite duration of at least one second. A longer wait returns early when a question arrives."
    return None


def generate_for_turn(model, system, messages, temperature, seed, generate):
    if type(model.tool_attempts) is not int or not 1 <= model.tool_attempts <= 16:
        raise ValueError("tool_attempts must be an integer from 1 through 16")
    if type(model.tool_retry_max_tokens) is not int or model.tool_retry_max_tokens < 1:
        raise ValueError("tool_retry_max_tokens must be a positive integer")
    native = enabled(model.extra_body)
    config = model
    repairs = []
    attempts = []
    for number in range(model.tool_attempts):
        supplied = [*messages, *repairs]
        response = generate(config, system, supplied, temperature, seed)
        problem = issue(response, native)
        attempts.append({"model_config": asdict(config), "input_sha256": stable_sha256({"system": system, "messages": supplied}),
                         "recovery_messages": copy.deepcopy(repairs), "response": copy.deepcopy(response),
                         "issue": problem[0] if problem else None})
        if problem is None:
            return {**response, "generation_attempts": attempts}
        if number == model.tool_attempts - 1:
            raise ToolProtocolError(f"Tool response failed after {model.tool_attempts} attempts: {problem[0]}", attempts)
        if problem[0] == "truncated":
            config = replace(config, max_tokens=min(max(config.max_tokens * 2, 8192),
                                                    max(model.max_tokens, model.tool_retry_max_tokens)))
        # Do not feed malformed or repetitive output back into the next request.
        # It remains verbatim in the attempt ledger above. The feedback contains
        # no task answers, wiki hints, or new timing information.
        repairs = [{"role": "user", "content": "Tool interface feedback: " + problem[1]}]
