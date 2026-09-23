"""Normalize provider outcome flags independently of a consumer's retry policy."""


def response_failure(response: dict) -> str | None:
    finish = str(response.get("finish_reason") or "")
    raw = response.get("raw") or {}
    choices = raw.get("choices") or []
    if (finish.startswith(("content_filter", "refusal", "incomplete:content_filter"))
            or response.get("refusal")
            or any((c.get("message") or {}).get("refusal") for c in choices if isinstance(c, dict))):
        return "provider_blocked"
    if (finish in {"error", "failed", "cancelled"} or raw.get("error")
            or any(c.get("error") for c in choices if isinstance(c, dict))):
        return "provider_error"
    if finish in {"length", "max_tokens", "incomplete:max_output_tokens"}:
        return "truncated"
    if finish.startswith("incomplete"):
        return "provider_error"
    return None
