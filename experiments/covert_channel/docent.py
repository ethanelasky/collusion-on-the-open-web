"""Docent trial and complete-session exports, with private participant transcripts."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict

from ai_collusion.client import ModelConfig


def record_to_agent_run(record, manifest):
    from docent.data_models import AgentRun, Transcript
    from docent.data_models.chat import parse_chat_message
    from docent.data_models.chat.content import ContentReasoning, ContentText

    transcripts = []
    for role, agent in record["agents"].items():
        messages = [parse_chat_message({"role": "system", "content": agent["context"]["system"]})]
        responses = iter(t["response"] for t in agent["turns"] if t["response"] is not None)
        for index, message in enumerate(agent["messages_final"]):
            content = [ContentText(text=message["content"])]
            if message["role"] == "assistant" and index >= agent.get("history_message_count", 0):
                response = next(responses)
                if response.get("reasoning"):
                    content.insert(0, ContentReasoning(reasoning=response["reasoning"]))
            if message.get("screenshot"):
                # This installed Docent SDK supports text and reasoning, not image content blocks.
                content.append(ContentText(text=f"[Browser screenshot retained locally: {message['screenshot']}; SHA-256 {message['sha256']}]"))
            messages.append(parse_chat_message({"role": message["role"], "content": content}))
        if agent.get("error"):
            messages.append(parse_chat_message({"role": "user", "content": f"[Model error: {agent['error']}]"}))
        transcripts.append(Transcript(name=role, messages=messages,
                                      metadata={"role": role, "model": agent["model"], "n_turns": agent["n_turns"]}))
    metadata = {k: record.get(k) for k in ("run_id", "condition", "sample_index", "secret", "guess", "correct",
                "status", "errors", "nonce", "question_fuzz", "answer_set", "displayed_answer_sets", "direction", "schedule", "interface", "browser_input", "scripted",
                "duration_s", "end_reason", "channel_events", "web_events", "session_index", "round_index",
                "rounds_per_session", "memory", "feedback", "counter_reset", "question_index",
                "questions_per_session", "turn_unit", "max_turns_per_agent_per_question",
                "max_turns_per_question", "max_turns_per_session", "n_turns", "counter_mode",
                "counter_scope", "counter_instance_id", "counter_backend", "counter_consistency",
                "action_error_count", "truncated_response_count", "counter_documentation")}
    metadata["source_file"] = record.get("_file")
    metadata["experiment_config"] = manifest.get("config", {})
    metadata["source_sha256"] = next((e["sha256"] for e in manifest["records"] if e["file"] == record.get("_file")), None)
    metadata["image_export"] = "Text observations and screenshot references; PNGs are in the local run artifacts."
    return AgentRun(name=f"{record['condition']} | session {record.get('session_index', record['sample_index'])} | "
                         f"round {record.get('round_index', 0) + 1} | correct={record['correct']}",
                    transcripts=transcripts, metadata=metadata)


def session_to_agent_run(records, manifest):
    """Export a complete persistent session without repeating its visible history.

    Pass records from ``analysis.load_records`` so each has ``_file`` and its
    checksum has already been checked. Completeness means every configured
    question is present; incorrect guesses, action errors, and missing guesses
    remain in the export. A partial or mixed session is rejected.
    """
    from docent.data_models import AgentRun, Transcript
    from docent.data_models.chat import parse_chat_message
    from docent.data_models.chat.content import ContentReasoning, ContentText
    from .analysis import usage_summary

    def require(condition, message):
        if not condition:
            raise ValueError(message)

    def question_index(record):
        return record.get("question_index", record.get("round_index"))

    require(bool(records), "A session needs question records")
    require(all(isinstance(question_index(r), int) for r in records), "Invalid question index")
    records = sorted(records, key=question_index)
    first = records[0]
    n_questions = first.get("questions_per_session", first.get("rounds_per_session"))
    require(isinstance(n_questions, int) and n_questions > 0, "Invalid session question count")
    require(len(records) == n_questions and [question_index(r) for r in records] == list(range(n_questions)),
            "A complete session needs each question exactly once")
    require(first.get("memory") == "persistent" and first.get("counter_scope") == "session",
            "Session export requires persistent histories and a session counter")
    require(first.get("counter_instance_id"), "Session export requires a counter instance ID")
    require(set(first.get("agents", {})) == {"sender", "receiver"}, "Session needs sender and receiver records")
    # Continuations can span harmless ModelConfig schema additions, such as a
    # newly recorded optional field whose default is None. Normalize defaults
    # for comparison while leaving source records and their hashes unchanged.
    def normalized_model(value):
        return asdict(ModelConfig.from_dict(value))

    model = normalized_model(first["agents"]["sender"]["model"])
    require(normalized_model(first["agents"]["receiver"]["model"]) == model, "Session has inconsistent participant models")
    shared_keys = ("run_id", "condition", "session_index", "nonce", "counter_instance_id", "counter_scope",
                   "counter_mode", "counter_backend", "counter_consistency", "memory", "direction", "schedule",
                   "interface", "answer_set", "displayed_answer_sets", "questions_per_session", "rounds_per_session",
                   "max_turns_per_session", "max_turns_per_question", "max_turns_per_agent_per_question", "feedback",
                   "counter_documentation")
    sources = {entry["file"]: entry["sha256"] for entry in manifest["records"]}
    require(len(sources) == len(manifest["records"]), "Manifest contains duplicate source files")
    if manifest.get("run_id") is not None:
        require(first.get("run_id") == manifest["run_id"], "Session does not match the manifest run")
    for record in records:
        require(all(record.get(key) == first.get(key) for key in shared_keys), "Records belong to inconsistent sessions")
        require(record.get("round_index", question_index(record)) == question_index(record), "Inconsistent question indices")
        require(set(record.get("agents", {})) == {"sender", "receiver"}, "Session needs sender and receiver records")
        require(record.get("_file") in sources, "Each question needs a source file in the manifest")

    transcripts = []
    for role in ("sender", "receiver"):
        system = first["agents"][role]["context"]["system"]
        messages = [parse_chat_message({"role": "system", "content": system, "metadata": {
            "provenance": "system", "participant": role, "source_file": first["_file"],
            "source_sha256": sources[first["_file"]]}})]
        previous = []
        total_turns = 0
        for record in records:
            agent = record["agents"][role]
            require(normalized_model(agent["model"]) == model, "Session has inconsistent participant models")
            require(agent["context"]["system"] == system, "Session has an inconsistent system prompt")
            final = agent["messages_final"]
            context = agent["context"]["messages"]
            prefix_length = agent.get("history_message_count", 0)
            require(prefix_length == len(previous) and context[:prefix_length] == previous
                    and final[:prefix_length] == previous, "Session history prefix does not match the previous question")
            require(final[:len(context)] == context, "Question context does not match its final history")
            require(agent["n_turns"] == len(agent["turns"]), "Question turn count does not match its transcript")
            provenance = {"participant": role, "question_index": question_index(record),
                          "source_file": record["_file"], "source_sha256": sources[record["_file"]]}

            def append(message, metadata, response=None):
                content = [ContentText(text=message["content"])]
                if response and response.get("reasoning"):
                    content.insert(0, ContentReasoning(reasoning=response["reasoning"]))
                if message.get("screenshot"):
                    content.append(ContentText(text=f"[Browser screenshot retained locally: {message['screenshot']}; SHA-256 {message['sha256']}]"))
                messages.append(parse_chat_message({"role": message["role"], "content": content,
                                                    "metadata": deepcopy({**provenance, **metadata})}))

            cursor = prefix_length
            for turn_index, turn in enumerate(agent["turns"]):
                request = turn["request"]
                requested = request["messages"]
                require(turn["turn"] == turn_index and request["turn"] == turn_index
                        and request["agent_id"] == role, "Inconsistent participant turn provenance")
                require(request["system"] == system, "Model request has an inconsistent system prompt")
                require(len(requested) >= cursor and final[:len(requested)] == requested,
                        "Model request does not match the saved history prefix")
                request_info = {
                    "request_message_count": len(requested), "request_system_message_index": 0,
                    "request_message_range": {"start": 1, "end_exclusive": len(requested) + 1,
                        "note": "Use ContentText only; restored ContentReasoning was not replayed to the model."},
                    "request_metadata": {k: v for k, v in request.items() if k not in ("system", "messages")},
                }
                for index in range(cursor, len(requested)):
                    append(final[index], {"turn": turn_index, "provenance":
                           "question_prompt" if index < len(context) else "turn_instruction"})
                cursor = len(requested)
                response = turn["response"]
                if response is None:
                    # A failed API call has no assistant output or observation.
                    # Link its exact input prefix and error on the last instruction.
                    require(bool(messages), "Failed request has no instruction message")
                    messages[-1].metadata.update(deepcopy({**request_info, "error": turn.get("error")}))
                    continue
                require(cursor + 1 < len(final) and final[cursor]["role"] == "assistant"
                        and final[cursor]["content"] == response["text"], "Model response does not match the saved transcript")
                append(final[cursor], {"turn": turn_index, "provenance": "model_response", "source": turn["source"],
                       **request_info, "raw_response": response.get("raw"), "usage": response.get("usage"),
                       "response_metadata": {k: v for k, v in response.items() if k not in ("raw", "usage", "text", "reasoning")},
                       "action": turn.get("action"), "duration_s": turn.get("duration_s")}, response)
                require(final[cursor + 1]["role"] == "user" and final[cursor + 1]["content"] == turn["result"],
                        "Action observation does not match the saved transcript")
                append(final[cursor + 1], {"turn": turn_index, "provenance": "observation", "source": turn["source"],
                       "effects": turn.get("effects"), "error": turn.get("error")})
                cursor += 2
            require(cursor == len(final), "Question has unaccounted transcript messages")
            previous = final
            total_turns += agent["n_turns"]
        transcripts.append(Transcript(name=role, messages=messages, metadata=deepcopy({
            "role": role, "model": model, "n_turns": total_turns, "n_questions": n_questions,
            "counter_instance_id": first["counter_instance_id"], "private_history": True})))

    questions = []
    question_keys = ("question_index", "round_index", "sample_index", "secret", "guess", "correct", "status", "errors",
                     "question_fuzz", "n_turns", "duration_s", "end_reason", "timestamp", "counter_reset", "channel_events",
                     "action_error_count", "truncated_response_count")
    for record in records:
        question = {key: record.get(key) for key in question_keys}
        question.update(question_index=question_index(record), target=record["secret"], source_file=record["_file"],
                        source_sha256=sources[record["_file"]], usage=usage_summary([record]))
        questions.append(question)
    usage = usage_summary(records)
    n_correct = sum(bool(record["correct"]) for record in records)
    metadata = {key: first.get(key) for key in shared_keys}
    metadata.update(export_schema="covert-channel-session/v1", export_granularity="session", history_deduplicated=True,
                    reasoning_history_replayed=False,
                    reasoning_export_note="Reasoning is restored from every saved response for analysis. Only visible assistant text was retained in model history.",
                    model=model["model"], model_alias=model["name"], model_config=model,
                    n_questions=n_questions, n_correct=n_correct, accuracy=n_correct / n_questions,
                    question_tags=[record.get("question_fuzz") for record in records], questions=questions,
                    source_files=[{"file": r["_file"], "sha256": sources[r["_file"]]} for r in records],
                    session_plan_sha256=manifest.get("session_plan_sha256"), experiment_config=manifest.get("config", {}),
                    usage=usage, reported_cost_usd=usage["reported_cost_usd"],
                    n_turns=sum(record["n_turns"] for record in records),
                    action_error_count=sum(record.get("action_error_count", 0) for record in records),
                    truncated_response_count=sum(record.get("truncated_response_count", 0) for record in records),
                    api_error_questions=sum(bool(record.get("errors")) for record in records),
                    missing_guesses=sum(record.get("guess") is None for record in records))
    return AgentRun(name=f"{model['name']} | {first['condition']} | session {first['session_index']} | "
                         f"{n_correct}/{n_questions} correct", transcripts=transcripts, metadata=deepcopy(metadata))
