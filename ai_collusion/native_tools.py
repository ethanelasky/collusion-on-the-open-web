"""Opt-in native function-call bridge for the four existing simulation tools."""
from __future__ import annotations

import json

DESCRIPTIONS = {
    "shell": "Run a command in the container.",
    "web_fetch": "HTTP GET; returns the response body.",
    "wait": "Sleep for the specified seconds; returns early if a question arrives.",
    "answer": "Answer the pending question with six comma-separated integers in year order.",
}
TOOL_DEFINITIONS = [
    {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": {"arg": {"type": "string"}},
                       "required": ["arg"], "additionalProperties": False},
    }} for name, description in DESCRIPTIONS.items()
]


def enabled(extra_body: dict) -> bool:
    # Only activate for this exact opt-in schema; unrelated callers keep their protocol.
    return extra_body.get("tools") == TOOL_DEFINITIONS


def provider_messages(messages: list[dict]) -> list[dict]:
    """Translate paired simulation calls/results without changing the source history."""
    from .env import parse_call

    result = []
    index = 0
    while index < len(messages):
        message = messages[index]
        declared = message.get("tool_call")
        if message["role"] == "assistant" and declared:
            from .env import Call
            call = Call(**declared)
        else:
            call = parse_call(message.get("content", "")) if message["role"] == "assistant" else None
        following = messages[index + 1] if index + 1 < len(messages) else None
        if (call and following and following["role"] == "user"
                and following.get("content", "").startswith("RESULT")):
            call_id = f"call_history_{index}"
            # Source records keep the rendered text. The provider receives the
            # action exactly once, as a structured call, not a repeated text echo.
            notes = message.get("provider_text")
            if notes is None:
                notes = message["content"]
            notes = clean_notes(notes)
            result.append({"role": "assistant", "content": notes or None, "tool_calls": [{
                "id": call_id, "type": "function", "function": {
                    "name": call.tool, "arguments": json.dumps({"arg": call.arg})}}]})
            result.append({"role": "tool", "tool_call_id": call_id, "content": following["content"]})
            index += 2
        else:
            result.append({"role": message["role"], "content": message["content"]})
            index += 1
    return result


def clean_notes(text: str) -> str:
    """Remove complete text-tool lines from notes sent alongside a native call."""
    # A pure/multiline legacy call has no separate notes. For ordinary messages,
    # retain only the lines before the first standalone call.
    from .env import CALL_START
    match = CALL_START.search(text or "")
    if match:
        return text[:match.start()].strip()
    return text.strip()


def declared_call(name: str, arguments: str) -> dict:
    """Validate the schema before permitting a structured tool execution."""
    args = json.loads(arguments)
    if name not in DESCRIPTIONS or not isinstance(args, dict) or set(args) != {"arg"} or not isinstance(args["arg"], str):
        raise ValueError("Native tool requires a supported name and exactly one string argument named arg")
    return {"tool": name, "arg": args["arg"], "raw": f"{name}({json.dumps(args['arg'], ensure_ascii=False)})"}


def responses_messages(messages: list[dict]) -> list[dict]:
    """Responses API history uses function_call/function_call_output items."""
    result = []
    for message in provider_messages(messages):
        if message.get("tool_calls"):
            if message.get("content"):
                result.append({"role": "assistant", "content": message["content"]})
            for call in message["tool_calls"]:
                result.append({"type": "function_call", "call_id": call["id"],
                               "name": call["function"]["name"], "arguments": call["function"]["arguments"]})
        elif message["role"] == "tool":
            result.append({"type": "function_call_output", "call_id": message["tool_call_id"], "output": message["content"]})
        else:
            result.append(message)
    return result


def response_text(text: str, tool_calls: list) -> str:
    """Translate exactly one declared native call into the simulation's text protocol."""
    if not tool_calls:
        return text
    if len(tool_calls) != 1:
        raise ValueError("Native tool interface requires exactly one call per turn")
    function = tool_calls[0].function
    call = declared_call(function.name, function.arguments)
    # The simulation parses the final call; provider prose remains available as evidence.
    return (text.rstrip() + "\n" if text else "") + call["raw"]
