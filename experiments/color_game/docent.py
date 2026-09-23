"""Lossless private-history export of color-game rollouts to Docent.

This module does no I/O. Call ``load_rollout`` first to hydrate response files.
Research outcomes and late responses are marked separately from what a player
actually saw. The installed Docent SDK assigns IDs when constructing objects; export keys
in metadata provide stable identities for an incremental uploader.
"""
from __future__ import annotations

from copy import deepcopy
import json


EXPORT_SCHEMA = "color-game-docent/v1"
ROLES = ("alice", "bob")
TERMINAL_STATUSES = {
    "complete", "complete_with_errors", "complete_pending_responses", "failed", "interrupted",
}
UNOBSERVED_STATUSES = {"late", "received_unapplied", "pending", "pending_late"}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _response_text(response):
    text = response.get("text")
    if isinstance(text, str):
        return text
    return json.dumps(response["action"]) if isinstance(response.get("action"), dict) else ""


def rollout_to_agent_run(rollout: dict, *, campaign_metadata: dict | None = None):
    """Convert a terminal rollout, retaining failures and exact private inputs.

    Complete records must contain every round. Interrupted and failed records
    are exported with ``[PARTIAL]`` in the name and explicit completeness fields.
    Pending late responses are flagged; a caller should defer upload until they
    settle, or update that export later. No histories, outcomes, or source data
    are modified. SDK-generated IDs must be retained by the uploader on retry.
    """
    from docent.data_models import AgentRun, Transcript
    from docent.data_models.chat import parse_chat_message
    from docent.data_models.chat.content import ContentReasoning, ContentText

    campaign_metadata = campaign_metadata or {}
    _require(rollout.get("schema") == "color-game/v1", "Not a color-game/v1 rollout")
    status = rollout.get("status")
    _require(status in TERMINAL_STATUSES, "Docent export requires a terminal rollout")
    rollout_id = rollout.get("rollout_id")
    _require(isinstance(rollout_id, str) and bool(rollout_id), "Missing rollout identity")
    config = rollout.get("config", {})
    expected = config.get("rounds")
    _require(type(expected) is int and expected > 0, "Invalid configured round count")
    rounds = rollout.get("rounds", [])
    _require(isinstance(rounds, list) and len(rounds) <= expected
             and [r.get("round_index") for r in rounds] == list(range(len(rounds))),
             "Round indices must form an ordered, unique prefix")
    complete = status.startswith("complete")
    completed_rounds = sum("actions_used" in rnd for rnd in rounds)
    _require(not complete or (len(rounds) == expected and completed_rounds == expected),
             "A complete rollout must contain every completed round")
    _require(set(rollout.get("system_prompts", {})) == set(ROLES)
             and set(rollout.get("agents", {})) == set(ROLES),
             "Export requires both private histories and system prompts")
    actions = [(rnd["round_index"], action) for rnd in rounds for action in rnd.get("actions", [])]
    _require(all(action.get("role") in ROLES for _, action in actions), "Unknown action participant")
    export_key = f"{EXPORT_SCHEMA}:{rollout_id}"
    pending = sum(str(action.get("response_status", "")).startswith("pending") for _, action in actions)
    transcripts = []
    unobserved_response_count = 0

    for role in ROLES:
        system = rollout["system_prompts"][role]
        history = rollout["agents"][role].get("messages")
        _require(isinstance(system, str) and isinstance(history, list), "Invalid private history")
        _require(all(isinstance(message, dict) and message.get("role") in {"user", "assistant"}
                     and isinstance(message.get("content"), str) for message in history),
                 "Private history contains an invalid message")
        model = rollout.get("models", {}).get(role, {})
        source = {"participant": role, "rollout_id": rollout_id,
                  "source_file": rollout.get("_file") or campaign_metadata.get("source_file")
                  or rollout.get("artifact_paths", {}).get("json"),
                  "source_sha256": rollout.get("_sha256") or rollout.get("source_sha256")
                  or campaign_metadata.get("source_sha256")}

        def message(value, metadata, response=None):
            content = [ContentText(text=value["content"])]
            if response and response.get("reasoning"):
                reasoning = response["reasoning"]
                _require(isinstance(reasoning, str), "Response reasoning must be text")
                content.insert(0, ContentReasoning(reasoning=reasoning))
            fields = {"role": value["role"], "content": content,
                      "metadata": deepcopy({**source, **metadata})}
            if value["role"] == "assistant" and model.get("model"):
                fields["model"] = model["model"]
            return parse_chat_message(fields)

        messages = [message({"role": "system", "content": system}, {
            "provenance": "system", "observed_by_agent": True,
            "prompt_sha256": rollout.get("prompt_sha256", {}).get(role)})]
        cursor = 0
        audit = []
        attempts = 0
        for round_index, action in actions:
            if action["role"] != role:
                continue
            attempts += 1
            requested = action.get("request_messages")
            _require(action.get("request_system") == system, "Action has an inconsistent system prompt")
            _require(isinstance(requested, list) and len(requested) >= cursor
                     and history[:len(requested)] == requested,
                     "Action request does not match the saved private history prefix")
            context = {"round_index": round_index, "step": action.get("step"),
                       "request_id": action.get("request_id")}
            for index in range(cursor, len(requested)):
                _require(history[index]["role"] == "user", "Unmapped assistant response in private history")
                messages.append(message(history[index], {
                    **context, "provenance": "instruction", "observed_by_agent": True,
                    "history_message_index": index}))
            cursor = len(requested)
            _require(cursor > 0, "Action request has no instruction message")
            request_info = {
                **context, "request_system_message_index": 0,
                "request_message_count": len(requested),
                "request_message_range": {"start": 1, "end_exclusive": len(requested) + 1,
                    "note": "Use ContentText only; restored ContentReasoning was not replayed to the model."},
                # Preserve all timing, errors, action/result, native request IDs,
                # persistence state, and unknown future provenance fields.
                "action_record": {key: value for key, value in action.items()
                                  if key not in {"request_system", "request_messages", "response"}},
            }
            messages[-1].metadata.setdefault("action_attempts", []).append(deepcopy(request_info))
            response = action.get("response")
            _require(response is None or isinstance(response, dict), "Invalid response record")
            unobserved = (action.get("response_status") in UNOBSERVED_STATUSES
                          or bool(action.get("response_error")))
            # An async interrupt can occur after the durable response write
            # but before appending its visible answer. Unlike realtime late
            # responses, this boundary has no response_status field.
            if not complete and response is not None and cursor == len(history):
                unobserved = True
                request_info["unobserved_reason"] = "terminal_partial_history_ended_at_request"
            response_meta = {key: value for key, value in (response or {}).items()
                             if key not in {"text", "reasoning"}}
            if response is None or unobserved:
                if response is not None:
                    audit.append((response, {**request_info, "response_metadata": response_meta}))
                continue
            expected_response = {"role": "assistant", "content": _response_text(response)}
            _require(cursor < len(history) and history[cursor] == expected_response,
                     "Model response does not match the saved private history")
            messages.append(message(history[cursor], {
                **request_info, "provenance": "model_response", "observed_by_agent": True,
                "history_message_index": cursor, "response_metadata": response_meta}, response))
            cursor += 1
            if action.get("result") is not None:
                expected_result = {"role": "user", "content": "Action result: " + json.dumps(action["result"])}
                _require(cursor < len(history) and history[cursor] == expected_result,
                         "Action result does not match the saved private history")
                messages.append(message(history[cursor], {
                    **context, "provenance": "observation", "observed_by_agent": True,
                    "history_message_index": cursor, "result": action["result"], "error": action.get("error")}))
                cursor += 1
        # A partial round may have its initial prompt but no admitted action.
        for index in range(cursor, len(history)):
            _require(history[index]["role"] == "user", "Unmapped assistant response in private history")
            messages.append(message(history[index], {
                "provenance": "instruction", "observed_by_agent": True, "history_message_index": index}))
        observed_message_count = len(messages)
        for response, metadata in audit:
            record = metadata["action_record"]
            applied_note = ("It was not applied to the game and did not change the score. "
                            if record.get("accepted") is False or
                            (record.get("action") is None and record.get("result") is None)
                            else "See the saved action record for any game state changes. ")
            marker = ("[Audit only: this response was not added to the player's history. "
                      + applied_note +
                      f"Round {metadata['round_index'] + 1}, action {metadata['step']}.]")
            messages.append(message({"role": "user", "content": marker}, {
                "provenance": "audit_annotation", "observed_by_agent": False,
                "round_index": metadata["round_index"], "step": metadata["step"]}))
            messages.append(message({"role": "assistant", "content": _response_text(response)}, {
                **metadata, "provenance": "unobserved_model_response", "observed_by_agent": False}, response))
        unobserved_response_count += len(audit)
        transcripts.append(Transcript(
            name=role, messages=messages,
            description="Exact private history, followed by clearly marked unobserved responses when present.",
            metadata=deepcopy({"export_key": f"{export_key}:{role}", "role": role,
                "private_history": True, "model": model, "n_actions": attempts,
                "observed_message_count": observed_message_count,
                "unobserved_response_count": len(audit), "history_deduplicated": True,
                "reasoning_history_replayed": False})))

    summary = rollout.get("summary", {})
    matched = sum(bool(rnd.get("match")) for rnd in rounds)
    model_names = list(dict.fromkeys(rollout.get("models", {}).get(role, {}).get("name")
                                    or rollout.get("models", {}).get(role, {}).get("model")
                                    or "unknown model" for role in ROLES))
    metadata = {key: value for key, value in rollout.items()
                if key not in {"system_prompts", "agents", "rounds"}}
    metadata.update({
        "export_schema": EXPORT_SCHEMA, "export_key": export_key,
        "export_granularity": "rollout", "history_deduplicated": True,
        "reasoning_history_replayed": False,
        "reasoning_export_note": "Reasoning is restored for analysis. Only visible assistant text was retained in player history.",
        "research_metadata_note": "Round outcomes and all-player counter events are audit data, not player observations.",
        "campaign_metadata": campaign_metadata, "complete_rollout": complete,
        "rollout_index": campaign_metadata.get("rollout_index", rollout.get("rollout_index")),
        "expected_rounds": expected, "recorded_rounds": len(rounds), "completed_rounds": completed_rounds,
        "pending_responses": pending, "transcripts_settled": pending == 0,
        "unobserved_response_count": unobserved_response_count,
        "n_correct": matched, "accuracy": summary.get("accuracy"),
        "setting": config.get("setting"), "model_alias": " / ".join(model_names),
        "model": rollout.get("models", {}).get("alice", {}).get("model"),
        "rounds": [{key: value for key, value in rnd.items() if key != "actions"} for rnd in rounds],
    })
    label = "" if complete else "[PARTIAL] "
    return AgentRun(
        name=f"{label}{' / '.join(model_names)} | {config.get('setting')} | {rollout_id[:8]} | {matched}/{expected} correct",
        description="Alice and Bob have separate private transcripts. Outcomes are research metadata; no score feedback was sent to the players.",
        transcripts=transcripts, metadata=deepcopy(metadata))
