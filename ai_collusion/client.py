"""Tiny model client.

generate(messages, ...) -> {"text", "reasoning", "finish_reason", "usage", "raw"}

Transports:
  openai     any OpenAI-compatible chat-completions endpoint (OpenRouter, vLLM, llama.cpp, ...).
             Reasoning is taken from `reasoning_content` / `reasoning` on the message when the
             server exposes it, otherwise from a leading <think>...</think> block in the text.
  responses  the OpenAI Responses API. Reasoning models return a summary of their hidden reasoning
             when `reasoning_summary` is set (auto | concise | detailed); it lands in "reasoning".
             Effort comes from `effort`. Usage is normalised to the chat-completions key names.
  anthropic  the Anthropic Messages API via the official SDK, adaptive thinking with summarized display.
  stub       no network; returns a canned reply. For smoke-testing the runner.

Retries: timeouts, connection errors, 429, 5xx, and OpenRouter's transient
in-flight-budget 402 are retried. Server Retry-After delays take precedence.
Rate limits get a longer bounded retry window and a 60-second fallback delay.
HTTP 401 is fatal to the experiment and blocks subsequent model requests.
"""
from __future__ import annotations

import json
import logging
import math
import os
import random
import re
import time
from collections.abc import Mapping
from contextlib import contextmanager, nullcontext, ExitStack
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any

from . import auth_stop

_THINK_RE = re.compile(r"^\s*<think>(.*?)</think>\s*", re.DOTALL)
_LOG = logging.getLogger(__name__)
_REQUEST_CONTROL = ContextVar("model_request_control", default=(None, None))


@contextmanager
def request_control(*, attempt_context=None, on_retry=None):
    """Scope admission/cancellation and retry hooks to this calling context.

    ``attempt_context()`` must return a context manager. It surrounds each actual
    API attempt and releases before backoff. Hook failures propagate directly;
    they are not model failures and must never be retried.
    """
    token = _REQUEST_CONTROL.set((attempt_context, on_retry))
    try:
        yield
    finally:
        _REQUEST_CONTROL.reset(token)


@dataclass
class ModelConfig:
    name: str                       # short label used in file names
    transport: str                  # openai | anthropic | stub
    model: str                      # provider model id
    base_url: str | None = None
    api_key_env: str | None = None
    temperature: float | None = None
    max_tokens: int = 4096
    timeout_s: float = 120.0
    retries: int = 3
    # Episode-level recovery of unusable generations, separate from HTTP retries.
    tool_attempts: int = 4
    tool_retry_max_tokens: int = 16384
    # openai transport: extra JSON merged into the request body (e.g. reasoning settings)
    extra_body: dict[str, Any] = field(default_factory=dict)
    # openai transport: OpenAI's reasoning models reject `max_tokens` and want `max_completion_tokens`
    max_tokens_key: str = "max_tokens"
    # openai transport: normalize exactly one named function call into response text
    response_tool_name: str | None = None
    # responses transport
    reasoning_summary: str | None = None   # auto | concise | detailed; None = no summary requested
    # anthropic transport (effort is shared with the responses transport)
    thinking: str = "adaptive"      # adaptive | none
    effort: str | None = None       # low | medium | high | xhigh | max
    sampling: bool = False          # send temperature (only pre-4.7 Claude models accept it)
    # stub transport
    stub_text: str = 'CALL submit("stub")'
    # stub transport, multi-turn: reply i (counted by assistant turns already in `messages`) cycles
    # through this list; overrides stub_text when set
    stub_texts: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict, defaults: dict | None = None) -> "ModelConfig":
        merged = {**(defaults or {}), **d}
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(merged) - known
        if unknown:
            raise ValueError(f"model {d.get('name')!r}: unknown keys {sorted(unknown)}")
        return cls(**merged)

    def api_key(self) -> str | None:
        if not self.api_key_env:
            return None
        return os.environ.get(self.api_key_env)


def _error_metadata(exc: Exception) -> dict:
    """Accept both the full OpenRouter envelope and SDK-unwrapped error body."""
    body = getattr(exc, "body", None)
    if body is None:
        try:
            body = exc.response.json()
        except (AttributeError, TypeError, ValueError):
            return {}
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            return {}
    if not isinstance(body, Mapping):
        return {}
    error = body.get("error", body)
    metadata = error.get("metadata", {}) if isinstance(error, Mapping) else {}
    return dict(metadata) if isinstance(metadata, Mapping) else {}


def _retry_after_s(exc: Exception) -> float | None:
    metadata = _error_metadata(exc)
    sources = [getattr(getattr(exc, "response", None), "headers", {}), metadata.get("headers", {})]
    for headers in sources:
        if not isinstance(headers, Mapping):
            continue
        value = next((v for k, v in headers.items() if str(k).lower() == "retry-after"), None)
        if value is None:
            continue
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            try:
                date = parsedate_to_datetime(str(value))
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                seconds = max(0.0, date.timestamp() - time.time())
            except (TypeError, ValueError, OverflowError):
                continue
        if math.isfinite(seconds) and seconds >= 0:
            return seconds
    return None


def _retryable(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status is None:
        return True  # timeouts, connection errors, anything without an HTTP status
    return (status == 429 or status >= 500
            or (status == 402 and _error_metadata(exc).get("reason") == "in_flight_budget_exhausted"))


def model_error_details(exc: Exception) -> dict:
    """Structured diagnostics without arbitrary provider bodies or credentials."""
    metadata = _error_metadata(exc)
    details = {"status_code": getattr(exc, "status_code", None), "retryable": _retryable(exc)}
    for key in ("reason", "limit_source", "provider_name", "error_type"):
        if isinstance(metadata.get(key), str):
            details[key] = metadata[key]
    retry_after = _retry_after_s(exc)
    if retry_after is not None:
        details["retry_after_s"] = retry_after
    if isinstance(exc, ProviderResponseError):
        details["response_issue"] = exc.response_issue
        if exc.provider_response_id is not None:
            details["provider_response_id"] = exc.provider_response_id
    details.update(getattr(exc, "_client_retry_details", {}))
    return details


class ProviderResponseError(RuntimeError):
    """An unusable provider envelope, with only safe diagnostics retained."""

    def __init__(self, issue: str, response):
        super().__init__(f"Invalid chat-completion response: {issue}")
        self.response_issue = issue
        response_id = getattr(response, "id", None)
        self.provider_response_id = response_id if isinstance(response_id, str) else None
        error = getattr(response, "error", None)
        error = error if isinstance(error, Mapping) else {}
        code = error.get("code")
        if isinstance(code, str) and code.isdigit():
            code = int(code)
        self.status_code = code if type(code) is int and 400 <= code <= 599 else None
        metadata = error.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        safe = {key: metadata[key] for key in ("reason", "limit_source", "provider_name", "error_type")
                if isinstance(metadata.get(key), str)}
        headers = metadata.get("headers")
        if isinstance(headers, Mapping):
            safe["headers"] = {"Retry-After": value for key, value in headers.items()
                               if str(key).lower() == "retry-after" and isinstance(value, (str, int, float))}
        self.body = {"error": {"code": self.status_code, "metadata": safe}}


def _validate_chat_response(response):
    """Validate transport structure inside admission/retries, before parsing content.

    A model's refusal, truncation, or invalid tool arguments remains a valid
    response for the caller to save and handle. Only an unusable envelope or an
    embedded provider error is retried under the existing status-code policy.
    """
    error = getattr(response, "error", None)
    if isinstance(error, Mapping) and str(error.get("code")) == "401":
        raise ProviderResponseError("embedded_provider_error", response)
    choices = getattr(response, "choices", None)
    if not isinstance(choices, (list, tuple)) or not choices:
        issue = "missing_or_empty_choices"
    elif getattr(choices[0], "message", None) is None:
        issue = "missing_message"
    else:
        # A returned model message must be journaled even if the provider adds
        # an error extension (for example after a partial/truncated output).
        return response
    if getattr(response, "error", None):
        issue = "embedded_provider_error"
    raise ProviderResponseError(issue, response)


_CONNECTION_ATTEMPTS = 7   # transport-level failures (TLS read errors, resets) get a longer window: ~2 min
_MAX_RETRY_WAIT_S = 900.0  # never ignore a longer server delay and retry before it expires
_IN_FLIGHT_RETRY_DELAY_S = 120.0  # fallback if this known OpenRouter error omits its header
_RATE_LIMIT_ATTEMPTS = 8  # allow shared admission to adapt before stopping a question
_RATE_LIMIT_RETRY_DELAY_S = 60.0  # upstream shared-pool 429 often omits Retry-After


def _with_retries(cfg: ModelConfig, fn, *, retry_events: list[dict] | None = None):
    from .rate_limit import wait_for_request
    from .request_pool import configured_pool, exponential_delay

    pool = configured_pool()
    delay = 2.0
    waited = 0.0
    events = retry_events if retry_events is not None else []
    attempt = 0
    attempt_context, on_retry = _REQUEST_CONTROL.get()
    while True:
        auth_stop.check()
        failure = None
        # Let admission observe failed attempts before releasing their slots. Only
        # the exact API exception is retried; admission/cleanup failures propagate.
        try:
            wait_for_request(cfg)
            with ExitStack() as admission:
                if attempt_context is not None:
                    admission.enter_context(attempt_context())
                if pool is not None:
                    admission.enter_context(pool.attempt(cfg.name))
                auth_stop.check()
                try:
                    return fn()
                except Exception as exc:  # noqa: BLE001 - classify after releasing admission
                    if getattr(exc, "status_code", None) == 401:
                        auth_stop.trip()
                    failure = exc
                    raise
        except Exception as exc:
            if exc is not failure:
                raise
        assert failure is not None
        details = model_error_details(failure)
        status = details["status_code"]
        budget = (_CONNECTION_ATTEMPTS if status is None else
                  max(_RATE_LIMIT_ATTEMPTS, cfg.retries + 1) if status == 429 and cfg.retries > 0
                  else cfg.retries + 1)
        attempt += 1
        stop_reason = None
        if not details["retryable"]:
            stop_reason = "not_retryable"
        elif attempt >= budget:
            stop_reason = "attempt_limit"
        retry_after = details.get("retry_after_s")
        fallback = (_RATE_LIMIT_RETRY_DELAY_S if status == 429 else
                    _IN_FLIGHT_RETRY_DELAY_S if details.get("reason") == "in_flight_budget_exhausted" else delay)
        wait = max(delay, retry_after if retry_after is not None else fallback)
        if pool is not None:
            wait = max(exponential_delay(attempt), retry_after or 0.0)
        wait += random.uniform(0.0, min(5.0, wait * 0.1))
        if stop_reason is None and waited + wait > _MAX_RETRY_WAIT_S:
            stop_reason = "wait_limit"
        event = {k: v for k, v in details.items() if k not in {
            "attempts", "retry_exhausted", "retry_stop_reason", "retry_events"}}
        event.update({"attempt": attempt, "time_unix_s": time.time(),
                      "wait_s": wait if stop_reason is None else 0.0})
        if stop_reason is not None:
            event["stop_reason"] = stop_reason
        events.append(event)
        if stop_reason is not None:
            failure._client_retry_details = {
                "attempts": attempt, "retry_exhausted": details["retryable"],
                "retry_stop_reason": stop_reason, "retry_events": list(events),
            }
            raise failure
        _LOG.warning("Model API retry: model=%s status=%s reason=%s attempt=%s/%s wait_s=%.3f",
                     cfg.name, details["status_code"], details.get("reason", type(failure).__name__),
                     attempt, budget, wait)
        if on_retry is not None:
            on_retry(dict(event))
        time.sleep(wait)
        waited += wait
        delay = min(delay * 2, 60.0)


def _split_think(text: str | None) -> tuple[str, str | None]:
    if not text:
        return "", None
    m = _THINK_RE.match(text)
    if m:
        return text[m.end():], m.group(1).strip()
    return text, None


# ---------------------------------------------------------------- transports

def _generate_openai(cfg: ModelConfig, system: str, messages: list[dict], temperature: float | None,
                     seed: int | None) -> dict:
    from openai import OpenAI

    if cfg.response_tool_name is not None and (not isinstance(cfg.response_tool_name, str) or not cfg.response_tool_name):
        raise ValueError("response_tool_name must be a nonempty string or None")
    client = OpenAI(
        api_key=cfg.api_key() or "EMPTY",
        base_url=cfg.base_url,
        timeout=cfg.timeout_s,
        max_retries=0,
    )
    req: dict[str, Any] = {
        "model": cfg.model,
        "messages": [{"role": "system", "content": system}, *messages],
        cfg.max_tokens_key: cfg.max_tokens,
    }
    from .native_tools import enabled, provider_messages

    native = enabled(cfg.extra_body)
    if native:
        req["messages"] = [{"role": "system", "content": system}, *provider_messages(messages)]
    if temperature is not None:
        req["temperature"] = temperature
    if seed is not None:
        req["seed"] = seed
    if cfg.extra_body:
        req["extra_body"] = cfg.extra_body

    def call():
        response = _validate_chat_response(client.chat.completions.create(**req))
        if not response.choices or any(c.finish_reason == "error" or getattr(c, "error", None) for c in response.choices):
            error = RuntimeError("Provider returned an error inside a chat-completions response")
            error.status_code = 502
            raise error
        return response

    retry_events: list[dict] = []
    resp = _with_retries(cfg, call, retry_events=retry_events)
    choice = resp.choices[0]
    msg = choice.message
    extra = getattr(msg, "model_extra", None) or {}
    reasoning = (
        getattr(msg, "reasoning_content", None)
        or getattr(msg, "reasoning", None)
        or extra.get("reasoning_content")
        or extra.get("reasoning")
    )
    if isinstance(reasoning, list):  # some servers return a list of reasoning parts
        reasoning = "\n".join(str(getattr(p, "text", p)) for p in reasoning)
    text, inline_think = _split_think(msg.content)
    provider_text = text
    tool_call, tool_error = None, None
    if native:
        from .native_tools import declared_call, clean_notes
        calls = msg.tool_calls or []
        try:
            if len(calls) != 1:
                raise ValueError(f"Expected one native tool call; received {len(calls)}")
            tool_call = declared_call(calls[0].function.name, calls[0].function.arguments)
            text = (clean_notes(text) + "\n" if clean_notes(text) else "") + tool_call["raw"]
        except (ValueError, TypeError) as exc:
            tool_error = str(exc)
    if reasoning is None:
        reasoning = inline_think
    text_metadata = {}
    if cfg.response_tool_name is not None:
        calls = msg.tool_calls or []
        response_tool_error = None
        if len(calls) != 1:
            response_tool_error = f"Expected exactly one {cfg.response_tool_name!r} tool call; received {len(calls)}"
        else:
            response_call = calls[0]
            function = getattr(response_call, "function", None)
            name = getattr(function, "name", None)
            arguments = getattr(function, "arguments", None)
            if name != cfg.response_tool_name:
                response_tool_error = f"Expected response tool {cfg.response_tool_name!r}; received {name!r}"
            elif not isinstance(arguments, str):
                response_tool_error = f"Tool {cfg.response_tool_name!r} arguments must be a string"
        # Keep the complete provider response below; only normalize the selected
        # function's argument string. A rejected envelope is still a paid response
        # for the caller to journal before recording an action-validation error.
        if response_tool_error:
            text = ""
            text_metadata = {"text_source": "rejected_tool_call", "response_tool_error": response_tool_error}
        else:
            text = arguments
            text_metadata = {"text_source": "tool_call.arguments", "tool_call_id": response_call.id}
    return {
        "text": text,
        "reasoning": reasoning,
        "finish_reason": choice.finish_reason,
        "usage": resp.usage.model_dump() if resp.usage else None,
        "raw": resp.model_dump(),
        "seed_applied": seed is not None,
        "retry_events": retry_events,
        **text_metadata,
        "tool_mode": "native" if native else "text", "tool_call": tool_call,
        "tool_error": tool_error, "provider_text": provider_text,
    }


def _generate_responses(cfg: ModelConfig, system: str, messages: list[dict], temperature: float | None,
                        seed: int | None) -> dict:
    from openai import OpenAI

    client = OpenAI(api_key=cfg.api_key() or "EMPTY", base_url=cfg.base_url, timeout=cfg.timeout_s, max_retries=0)
    req: dict[str, Any] = {
        "model": cfg.model,
        "instructions": system,
        "input": [{"role": m["role"], "content": m["content"]} for m in messages],
        "max_output_tokens": cfg.max_tokens,
        "store": False,
    }
    reasoning: dict[str, Any] = {}
    if cfg.effort:
        reasoning["effort"] = cfg.effort
    if cfg.reasoning_summary:
        reasoning["summary"] = cfg.reasoning_summary
    if reasoning:
        req["reasoning"] = reasoning
    if temperature is not None:
        req["temperature"] = temperature
    if cfg.extra_body:
        req["extra_body"] = dict(cfg.extra_body)
    from .native_tools import enabled, responses_messages, declared_call, clean_notes
    native = enabled(cfg.extra_body)
    if native:
        req["input"] = responses_messages(messages)
        req["tools"] = [{"type": "function", **tool["function"], "strict": True} for tool in cfg.extra_body["tools"]]
        req["extra_body"].pop("tools")

    def call():
        return client.responses.create(**req)

    retry_events: list[dict] = []
    resp = _with_retries(cfg, call, retry_events=retry_events)
    text_parts, summary_parts, calls = [], [], []
    for item in resp.output or []:
        if item.type == "reasoning":
            for s_ in getattr(item, "summary", None) or []:
                t = getattr(s_, "text", None)
                if t:
                    summary_parts.append(t)
        elif item.type == "message":
            for c in item.content or []:
                if getattr(c, "type", None) == "output_text":
                    text_parts.append(c.text)
        elif item.type == "function_call":
            calls.append(item)
    finish = resp.status
    if finish == "incomplete" and getattr(resp, "incomplete_details", None):
        finish = f"incomplete:{getattr(resp.incomplete_details, 'reason', None)}"
    u = resp.usage
    usage = None
    if u:
        usage = {
            "prompt_tokens": u.input_tokens,
            "completion_tokens": u.output_tokens,
            "total_tokens": u.total_tokens,
            "prompt_tokens_details": {"cached_tokens": getattr(u.input_tokens_details, "cached_tokens", None) if u.input_tokens_details else None},
            "completion_tokens_details": {"reasoning_tokens": getattr(u.output_tokens_details, "reasoning_tokens", None) if u.output_tokens_details else None},
        }
    text = provider_text = "\n".join(text_parts)
    tool_call, tool_error = None, None
    if native:
        try:
            if len(calls) != 1:
                raise ValueError(f"Expected one native tool call; received {len(calls)}")
            tool_call = declared_call(calls[0].name, calls[0].arguments)
            text = (clean_notes(text) + "\n" if clean_notes(text) else "") + tool_call["raw"]
        except (ValueError, TypeError) as exc:
            tool_error = str(exc)
    return {
        "text": text,
        "reasoning": "\n\n".join(summary_parts) or None,
        "finish_reason": finish,
        "usage": usage,
        "raw": resp.model_dump(),
        "seed_applied": False,   # the Responses API has no seed parameter
        "retry_events": retry_events,
        "tool_mode": "native" if native else "text", "tool_call": tool_call,
        "tool_error": tool_error, "provider_text": provider_text,
    }


def _generate_anthropic(cfg: ModelConfig, system: str, messages: list[dict], temperature: float | None,
                        seed: int | None) -> dict:
    import anthropic

    client = anthropic.Anthropic(api_key=cfg.api_key(), timeout=cfg.timeout_s, max_retries=0)
    req: dict[str, Any] = {
        "model": cfg.model,
        "max_tokens": cfg.max_tokens,
        "system": system,
        "messages": messages,
    }
    if cfg.thinking == "adaptive":
        req["thinking"] = {"type": "adaptive", "display": "summarized"}
    elif cfg.thinking == "none":
        req["thinking"] = {"type": "disabled"}
    else:
        raise ValueError(f"unknown thinking mode {cfg.thinking!r}")
    if cfg.effort:
        req["output_config"] = {"effort": cfg.effort}
    if cfg.sampling and temperature is not None:
        req["temperature"] = temperature

    def call():
        return client.messages.create(**req)

    retry_events: list[dict] = []
    resp = _with_retries(cfg, call, retry_events=retry_events)
    text_parts, think_parts = [], []
    for block in resp.content:
        if block.type == "text":
            text_parts.append(block.text)
        elif block.type == "thinking" and block.thinking:
            think_parts.append(block.thinking)
    finish = resp.stop_reason
    if finish == "refusal" and getattr(resp, "stop_details", None):
        finish = f"refusal:{getattr(resp.stop_details, 'category', None)}"
    return {
        "text": "\n".join(text_parts),   # separate output items on separate lines, so prose + call stay parseable
        "reasoning": "\n".join(think_parts) or None,
        "finish_reason": finish,
        "usage": resp.usage.model_dump() if resp.usage else None,
        "raw": resp.model_dump(),
        "seed_applied": False,  # the Anthropic API has no seed parameter
        "retry_events": retry_events,
    }


def _generate_stub(cfg: ModelConfig, system: str, messages: list[dict], temperature, seed) -> dict:
    text = cfg.stub_text
    if cfg.stub_texts:
        n = sum(1 for m in messages if m.get("role") == "assistant")
        text = cfg.stub_texts[n % len(cfg.stub_texts)]
    return {
        "text": text,
        "reasoning": "stub transport: no model was called",
        "finish_reason": "stop",
        "usage": None,
        "raw": None,
        "seed_applied": False,
    }


_TRANSPORTS = {"openai": _generate_openai, "responses": _generate_responses, "anthropic": _generate_anthropic, "stub": _generate_stub}


def generate(cfg: ModelConfig, system: str, messages: list[dict], *, temperature: float | None,
             seed: int | None) -> dict:
    """Call the model once. `messages` is the transport-neutral list from context.build_context."""
    auth_stop.check()
    try:
        fn = _TRANSPORTS[cfg.transport]
    except KeyError:
        raise ValueError(f"unknown transport {cfg.transport!r}; choose from {sorted(_TRANSPORTS)}") from None
    return fn(cfg, system, messages, temperature, seed)
