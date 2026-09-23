"""Prompt previews: exactly what each model receives, without calling any model.

One preview per evaluee condition (the assembled context as `play` sends it) and one
per sample call. Simulated calls show the env model's system and per-turn prompt;
registered static-page calls show the call and actual tool result. Print them,
or push each as a Docent agent run. The
Docent display marks supplied history with [PREFILL]; model inputs are unchanged.
"""
from __future__ import annotations

import sys
from typing import Any

from .context import format_context
from .arms import Arm, validate_arms
from .env import parse_call, preview_env_prompt, step
from .preparation import prepare_episode
from .web_fixtures import load_web_fixtures
from .episode import episode_condition
from .wiki import Cut, Dump, Spec, reference_for


def build_previews(spec: Spec, dump: Dump, cuts: dict[int, Cut], variants: list[str | None],
                   modes: list[str], sample_calls: list[str], arms: list[Arm] | None = None,
                   max_turns: int | None = None, hint: str | None = None) -> list[dict]:
    if arms is not None:
        validate_arms(arms, spec, cuts)
    web_fixtures = load_web_fixtures()
    out: list[dict] = []
    for seq, cut in cuts.items():
        selections = ([(a.variant, [a.mode], a) for a in arms] if arms is not None else
                      [(v, modes, None) for v in variants])
        for variant, selected_modes, arm in selections:
            def prepare(mode):
                return prepare_episode(spec, dump, cut, variant, mode,
                                       num_live_problems=arm.num_live_problems if arm else None,
                                       wiki_write_instructions=arm.wiki_write_instructions if arm else False,
                                       hint=arm.hint if arm else hint, web_fixtures=web_fixtures)

            prepared = prepare(selected_modes[0])
            ctx = prepared.context
            cond = f"{cut.page_name}@{seq}" + (f":{variant}" if variant else "")
            if arm is not None:
                cond = episode_condition(cut, seq, variant, arm.mode) + f":arm-{arm.id}"
            resolved = {**prepared.resolved_config,
                        "max_turns": (arm.max_turns if arm and arm.max_turns is not None else
                                      max_turns if max_turns is not None else prepared.world.ep.max_turns),
                        "max_tokens": arm.max_tokens if arm else None}
            common = {"arm_id": arm.id if arm else None, "arm": arm.to_dict() if arm else None,
                      "context_sha256": prepared.context_sha256, "resolved_config": resolved,
                      "live_start_message_index": len(ctx["messages"]),
                      "max_turns": resolved["max_turns"], "max_tokens": resolved["max_tokens"],
                      "num_live_problems": resolved["num_live_problems"],
                      "wiki_write_instructions": resolved["wiki_write_instructions"],
                      "hint": resolved["hint"], "context_provenance": ctx.get("provenance")}
            out.append({
                **common,
                "role": "evaluee", "name": f"evaluee | {cond}", "condition": cond, "variant": variant or "base",
                "mode": arm.mode if arm else None, "system": ctx["system"], "messages": ctx["messages"], "agent": cut.label,
                "reference": reference_for(cut),
            })
            for mode in selected_modes:
                for k, call_text in enumerate(sample_calls):
                    # URL inspection can apply a wiki save: every sample receives an isolated world.
                    env_prepared = prepare(mode)
                    parsed_call = parse_call(call_text)
                    static_response = (env_prepared.world.static_page_response(parsed_call)
                                       if parsed_call is not None else None)
                    if static_response is not None:
                        result = step(env_prepared.world, call_text)
                        role = "static-page"
                        name = "Static page response preview"
                        system = ""
                        messages = [{"role": "assistant", "content": call_text},
                                    {"role": "user", "content": result.result}]
                    else:
                        system, prompt = preview_env_prompt(env_prepared.world, call_text)
                        role = "env-model"
                        name = "env-model"
                        messages = [{"role": "user", "content": prompt}]
                    econd = episode_condition(cut, seq, variant, mode) + (f":arm-{arm.id}" if arm else "")
                    out.append({
                        **common, "context_sha256": env_prepared.context_sha256,
                        "resolved_config": {**resolved, "mode": mode},
                        "role": role, "name": f"{name} | {econd}" + (f" | call {k + 1}" if len(sample_calls) > 1 else ""),
                        "condition": econd, "variant": variant or "base", "mode": mode,
                        "system": system, "messages": messages, "agent": cut.label,
                        "sample_call": call_text, "reference": None,
                    })
    return out


def print_previews(previews: list[dict]) -> None:
    for p in previews:
        print("#" * 78 + f"\n# {p['name']}\n" + "#" * 78)
        print(f"# context_sha256: {p['context_sha256']}")
        print(f"# arm: {p.get('arm_id') or '(legacy)'}; max_tokens: {p.get('max_tokens')} (None = model config); "
              f"max_turns: {p.get('max_turns')}; num_live_problems: {p.get('num_live_problems')}; "
              f"wiki_write_instructions: {p.get('wiki_write_instructions')}; hint: {p.get('hint')}")
        print(format_context({"system": p["system"], "messages": p["messages"]}))
        print()


def to_agent_run(p: dict):
    from docent.data_models import AgentRun, Transcript
    from docent.data_models.chat import parse_chat_message

    msgs: list[Any] = []
    if p["role"] != "static-page":
        msgs.append(parse_chat_message({"role": "system", "content": p["system"],
                                        "metadata": {"prefill": False, "provenance": "system"}}))
    for m in p["messages"]:
        is_prefill = p["role"] == "evaluee"
        msgs.append(parse_chat_message({
            **m, "metadata": {**(m.get("metadata") or {}), "prefill": is_prefill,
                               "provenance": ("prefill" if is_prefill else
                                              "static_page_preview" if p["role"] == "static-page" else
                                              "environment_preview")},
        }))
    meta = {k: v for k, v in {
        "arm_id": p.get("arm_id"), "arm": p.get("arm"), "context_sha256": p.get("context_sha256"),
        "resolved_config": p.get("resolved_config"), "live_start_message_index": p.get("live_start_message_index"),
        "max_tokens": p.get("max_tokens"), "max_turns": p.get("max_turns"),
        "num_live_problems": p.get("num_live_problems"),
        "wiki_write_instructions": p.get("wiki_write_instructions"),
        "hint": p.get("hint"), "context_provenance": p.get("context_provenance"),
        "preview": True, "role": p["role"], "condition": p["condition"], "variant": p["variant"], "mode": p["mode"],
        "agent": p["agent"], "sample_call": p.get("sample_call"), "reference": p.get("reference"),
        "n_messages": len(p["messages"]), "system_chars": len(p["system"]),
    }.items() if v is not None}
    from .docent_prefill import prefill_metadata, render_prefill_message

    for message in msgs:
        if (message.metadata or {}).get("prefill") is True:
            message.metadata.pop("prefill_rendering", None)
    msgs = [render_prefill_message(message) for message in msgs]
    from .docent_presentation import display_label
    run = AgentRun(name=display_label(p["name"], (p.get("resolved_config") or {}).get("cut")),
                   transcripts=[Transcript(messages=msgs)], metadata=meta)
    run.metadata.update(prefill_metadata(run))
    return run


def upload_previews(previews: list[dict], collection_name: str, *, replace: bool = False) -> str:
    from .docent_cli import DOCENT_DASHBOARD_URL, ensure_public, make_client, resolve_collection_id

    runs = [to_agent_run(p) for p in previews]
    client = make_client()
    cid = resolve_collection_id(client, collection_name)
    public = ensure_public(client, cid)
    if replace:
        existing = client.list_agent_run_ids(cid)
        if existing:
            client.delete_agent_runs(cid, existing)
            print(f"cleared {len(existing)} existing run(s)", file=sys.stderr)
    client.add_agent_runs(cid, runs)
    from .docent_prefill import annotate_prefills

    annotate_prefills(client, cid, [str(run.id) for run in runs])
    for r in runs:
        print(f"  {r.name}  msgs={len(r.transcripts[0].messages)}", file=sys.stderr)
    print(f"collection: {collection_name}\nid: {cid}\nurl: {DOCENT_DASHBOARD_URL}/{cid}")
    print(f"access: {'public (anyone with the link can read)' if public else 'NOT public; sharing failed'}")
    return cid
