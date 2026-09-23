"""Observed interface and provider failures, kept separate from behavior labels."""
from __future__ import annotations

from .response_status import response_failure


def health(record: dict) -> dict:
    episode = record.get("episode") or {}
    turns = episode.get("turns") or []
    responses = [record.get("response") or {}, *((t.get("response") or {}) for t in turns)]
    # Simulator attempts matter even in historical records that accepted a failed
    # response. Ignore rejected attempts when a later simulator attempt succeeded.
    for turn in turns:
        env = turn.get("env_call") or {}
        attempts = env.get("generation_attempts") or []
        if attempts:
            responses.append(attempts[-1].get("response") or {})
    failures = [response_failure(r) for r in responses]
    provider_error = "provider_error" in failures
    provider_blocked = "provider_blocked" in failures
    provider_truncated = "truncated" in failures
    tail = turns[-2:] if episode.get("end_reason") == "no_call" else []
    unparsed_native = any(t.get("call") is None and "<|open|>tools" in
                         ((t.get("response") or {}).get("text") or "") for t in tail)
    claims = ["tool_choice=none", "tool_choice = none", "must not call", "tools are disabled",
              "can't call", "cannot call", "not allowed to call"]
    claims_disabled = any(any(s in "\n".join(str((t.get("response") or {}).get(k) or "")
                          for k in ["text", "reasoning"]).lower() for s in claims) for t in tail)
    cause = ("provider error" if provider_error or record.get("error") else
             "provider refusal" if provider_blocked else
             "provider truncation" if provider_truncated else
             "native tool tokens unparsed; model also reports tools disabled" if unparsed_native and claims_disabled else
             "native tool tokens unparsed" if unparsed_native else
             "model reports tools disabled" if claims_disabled else
             "two consecutive messages without a recognized tool call" if tail else
             episode.get("end_reason", "unknown"))
    return {"provider_response_error": provider_error, "provider_blocked": provider_blocked,
            "provider_truncated": provider_truncated,
            "halt_reason": cause, "unparsed_native_tool_tokens": unparsed_native,
            "model_reports_tools_disabled": claims_disabled}


def source_validity(record: dict) -> dict:
    episode = record.get("episode") or {}
    quality = health(record)
    invalid = bool(record.get("error")
                   or episode.get("end_reason") in {"model_error", "environment_error", "env_error"}
                   or quality["provider_response_error"] or quality["provider_blocked"]
                   or quality["provider_truncated"]
                   or any((t.get("env_call") or {}).get("error") or t.get("source") == "env-error"
                          for t in episode.get("turns") or []))
    status = "invalid" if invalid else ("valid" if "error" in record and record["error"] is None else "unknown")
    return {**quality, "source_transport_error": invalid, "source_transport_status": status}
