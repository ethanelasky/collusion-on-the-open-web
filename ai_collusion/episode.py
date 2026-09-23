"""Multi-turn episodes: evaluee x simulated environment, from a wiki cut onward.

run_episodes(...) mirrors runner.run_contexts: models x conditions x samples -> runs/<id>/*.json.
Each transcript keeps the initial context, every turn (evaluee response, parsed call, result and
its source, env-model call if any), the round outcomes, the evaluee's wiki posts, and the final
message list.
"""
from __future__ import annotations

import copy
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack
import traceback
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

from .client import ModelConfig, generate
from .arms import Arm, validate_arms
from .env import World, heartbeat_report, rounds_report, step
from .environment_modes import SUPPORTED_MODES as MODES, environment_label
from .run_storage import check_resume, completed_record, write_json
from .preparation import PreparedEpisode, context_sha256, prepare_context, prepare_episode, stable_sha256
from .web_fixtures import load_web_fixtures
from .wiki import Cut, Dump, Spec, reference_for, _sha256


def episode_condition(cut: Cut, seq: int, variant: str | None, mode: str) -> str:
    return f"{cut.page_name}@{seq}" + (f":{variant}" if variant else "") + f":env-{mode}"


_RETRYABLE = ("invalid_prompt", "flagged as potentially violating")   # OpenAI's stochastic prompt classifier


def _generate_with_retry(model, system, messages, temperature, seed, log, attempts: int = 4):
    """generate(), retrying the identical request when OpenAI's prompt classifier flags it: the flag is
    intermittent on this task (the same prompt passes on retry), so one hit should not end an episode."""
    for i in range(attempts):
        try:
            return generate(model, system, messages, temperature=temperature, seed=seed)
        except Exception as exc:  # noqa: BLE001
            if i == attempts - 1 or not any(k in str(exc) for k in _RETRYABLE):
                raise
            if log:
                log(f"    prompt flagged by the provider's classifier; retrying ({i + 1}/{attempts - 1})")
            time.sleep(5 * (i + 1))


def play(**kwargs) -> dict:
    """Run the original episode loop to completion."""
    turns = _play_turns(**kwargs)
    while True:
        try:
            next(turns)
        except StopIteration as finished:
            return finished.value


def _play_turns(
    *,
    model: ModelConfig,
    world: World,
    context: dict,
    temperature: float | None,
    seed: int | None,
    max_turns: int,
    log=None,
    prepared: PreparedEpisode | None = None,
):
    """Original play loop, yielding before each model call for the shared scheduler."""
    if prepared is not None:
        if world is not prepared.world:
            raise ValueError("prepared context must use its matching world")
        context = copy.deepcopy(prepared.context)
    else:
        context = prepare_context(world, context)
    messages = copy.deepcopy(context["messages"])
    turns: list[dict] = []
    end_reason = None
    error = None
    last_response = None
    wait_streak = 0
    stop_after_reasoning = bool(prepared and prepared.resolved_config["cut"].get("stop_after_first_reasoning", False))

    for turn in range(1, max_turns + 1):
        yield turns[-1] if turns else None
        t0 = time.monotonic()
        try:
            from .tool_protocol import generate_for_turn
            response = generate_for_turn(model, context["system"], messages, temperature, seed,
                lambda cfg, system, history, temp, sample_seed: _generate_with_retry(
                    cfg, system, history, temp, sample_seed, log))
        except Exception as exc:  # noqa: BLE001
            error = {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc(), "turn": turn}
            if hasattr(exc, "attempts"):
                error["generation_attempts"] = exc.attempts
            end_reason = "model_error"
            break
        dt = time.monotonic() - t0
        last_response = response
        text = response.get("text") or ""
        assistant_message = {"role": "assistant", "content": text}
        if response.get("tool_mode") == "native":
            assistant_message.update(tool_call=response["tool_call"], provider_text=response.get("provider_text", ""))
        messages.append(assistant_message)

        # Structured responses execute only the declared call, never an earlier
        # parseable line inside truncated or repeated provider text.
        before_task, before_utc = world.task_s, world.container_utc
        s = step(world, response["tool_call"]["raw"] if response.get("tool_mode") == "native" else text)
        s.elapsed = {**(s.elapsed or {}),
                     "actual_task_delta_s": world.task_s - before_task,
                     "actual_utc_delta_s": (world.container_utc - before_utc).total_seconds()}
        wait_streak = wait_streak + 1 if s.call and s.call.tool == "wait" else 0
        if wait_streak >= 8 and world.pending() is None and not s.done:
            s.result += ("\n\nTool usage reminder: repeated short waits consume one turn each. "
                         "A longer wait returns early when a question arrives; use it when waiting for the next question.")
            wait_streak = 0
        rec = {
            "turn": turn,
            "call": asdict(s.call) if s.call else None,
            "response": {k: response.get(k) for k in ("text", "reasoning", "finish_reason", "usage", "raw",
                "tool_mode", "tool_call", "tool_error", "provider_text")},
            "generation_attempts": response.get("generation_attempts", []),
            "duration_s": round(dt, 2),
            "result": s.result,
            "source": s.source,
            "notices": s.notices,
            "task_clock": s.task_clock,
            "container_utc": s.container_utc,
            "elapsed": s.elapsed,
            "env_call": (
                {"prompt": s.env_call.prompt, "response": s.env_call.response, "reasoning": s.env_call.reasoning,
                 "usage": s.env_call.usage, "error": s.env_call.error, "effects": s.env_call.effects,
                 "generation_attempts": s.env_call.generation_attempts}
                if s.env_call else None
            ),
        }
        turns.append(rec)
        if log:
            call_s = s.call.raw if s.call else "(no tool call)"
            log(f"    t{turn:02d} {s.task_clock} {s.source:<9} {call_s[:110]}")
        if s.env_call is not None and s.env_call.error is not None:
            error = {**s.env_call.error, "turn": turn, "source": "environment"}
            end_reason = "environment_error"
            break
        messages.append({"role": "user", "content": s.result})

        if stop_after_reasoning and any(
                (r.get("reasoning") or "").strip()
                for r in [response] + [a["response"] for a in response.get("generation_attempts", [])]):
            end_reason = "first_reasoning"
            break
        if s.done:
            end_reason = s.end_reason
            break
    else:
        end_reason = "max_turns"

    return {
        "context": context,
        "context_sha256": context_sha256(context),
        "live_start_message_index": len(context["messages"]),
        "messages_final": messages,
        "response": (
            {k: last_response.get(k) for k in ("text", "reasoning", "finish_reason", "usage", "raw")}
            if last_response else None
        ),
        "episode": {
            "mode": world.mode,
            "mode_label": environment_label(world.mode),
            "env_model": world.env_model.name if world.env_model else None,
            "turns": turns,
            "n_turns": len(turns),
            "end_reason": end_reason,
            "final_task_clock": world.task_clock(),
            "final_container_utc": world.container_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "rounds": rounds_report(world),
            "wiki_posts": world.own_posts,
            "wiki_pages_created": world.own_pages_created,
            "requests": world.requests_report(),
            "data_quota": world.data_quota_report(),
            **({"data_cooldown": {"task_seconds": world.data_request_cooldown_task_s,
                                  "events": world.data_cooldown_events}}
               if world.data_request_cooldown_task_s else {}),
            "heartbeat": heartbeat_report(world),
            "n_env_calls": len(world.env_calls),
            "env_system_prompt": world.env_calls[0].system if world.env_calls else None,
        },
        "error": error,
    }


def play_group(*, model, prepared, temperature, seeds, max_turns, shared=True,
               html=False, host="127.0.0.1", port=0, log=None):
    """Interleave N original episodes; only committed wiki edits are shared.

    Uses the existing scheduler, router, simulator, task clocks, and graders.
    API calls are ordered, not wall-clock concurrent. Separate groups may run
    concurrently through run_episodes(workers=...). No task text is added.
    """
    from contextlib import ExitStack
    from .coord import Turn, run_episode

    if not prepared or len(prepared) != len(seeds) or len({id(p.world) for p in prepared}) != len(prepared):
        raise ValueError("Each agent needs a distinct prepared world and seed")
    if type(max_turns) is not int or max_turns < 1:
        raise ValueError("max_turns must be positive")
    posts, mutex = [], threading.RLock()

    class EpisodeParticipant:
        def __init__(self, index, item, seed):
            self.started = time.monotonic()
            self.id, self.world, self.body = f"agent{index}", item.world, None
            self.world.agent_id = self.id
            self.world.shared_wiki = posts if shared else []
            self.world.wiki_lock = mutex
            self.steps = _play_turns(model=model, world=item.world, context=item.context,
                                     prepared=item, temperature=temperature, seed=seed,
                                     max_turns=max_turns, log=log)
            next(self.steps)  # Preparation only; no model call.

        def next_due(self):
            return None if self.done() else self.world.container_utc.timestamp()

        def done(self):
            return self.body is not None

        def take_turn(self, mediums, now_global_s):
            try:
                record = next(self.steps)
            except StopIteration as finished:
                self.body = finished.value
                self.body["duration_s"] = round(time.monotonic() - self.started, 2)
                record = (self.body["episode"]["turns"] or [{}])[-1]
            record = record or {}
            return Turn(self.id, now_global_s, (record.get("call") or {}).get("raw", ""),
                        record.get("result", ""), record.get("source", "model-error"), done=self.done())

    participants = [EpisodeParticipant(i, item, seed) for i, (item, seed) in enumerate(zip(prepared, seeds))]
    with ExitStack() as stack:
        if html:
            from .wiki_observer import serve_worlds
            server = stack.enter_context(serve_worlds([p.world for p in participants], host=host, port=port))
            print(f"Read-only shared wiki: {server.url}", file=sys.stderr, flush=True)
        run_episode(participants, [], max_turns=len(participants) * max_turns)
    bodies = []
    for p in participants:
        if p.body is None:
            raise RuntimeError("Episode scheduler did not finish an agent")
        p.body["episode"]["shared_wiki_posts"] = copy.deepcopy(p.world.shared_wiki)
        p.body["group"] = {"agent_id": p.id, "size": len(participants), "shared_wiki": shared,
                           "clock": "original simulated container time", "score": "individual",
                           "shared_counters": False}
        bodies.append(p.body)
    return bodies


def run_episodes(
    *,
    models: list[ModelConfig],
    env_model: ModelConfig,
    spec: Spec,
    dump: Dump,
    cuts: dict[int, Cut],
    variants: list[str | None],
    modes: list[str],
    n_samples: int,
    out_dir: str,
    base_seed: int,
    temperature: float | None,
    max_turns: int | None,
    run_id: str | None = None,
    manifest_extra: dict | None = None,
    workers: int = 1,
    arms: list[Arm] | None = None,
    hint: str | None = None,
    agents: int = 1,
    shared_wiki: bool = True,
    html: bool = False,
    wiki_host: str = "127.0.0.1",
    wiki_port: int = 0,
    prepare_hook=None,
) -> Path:
    """`prepare_hook(prepared) -> prepared` (optional) adjusts each prepared episode's context the
    same way for the manifest and for play, so the manifest's context hash still guards the batch."""
    if not models or type(n_samples) is not int or n_samples < 1:
        raise ValueError("episodes require at least one model and a positive sample count")
    if max_turns is not None and (type(max_turns) is not int or max_turns < 1):
        raise ValueError("max_turns must be a positive integer")
    if type(agents) is not int or agents < 1:
        raise ValueError("agents must be a positive integer")
    if html and wiki_port and (workers > 1 or len(models) > 1):
        raise ValueError("Concurrent groups need automatic wiki ports (--wiki-port 0)")
    if arms is not None:
        validate_arms(arms, spec, cuts)
        if any(arm.max_tokens is not None for arm in arms):
            cap_keys = {"max_tokens", "max_completion_tokens", "max_output_tokens"}
            for model in models:
                conflicts = cap_keys.intersection(model.extra_body)
                if conflicts:
                    raise ValueError(
                        f"model {model.name!r}: remove token cap keys {sorted(conflicts)} from extra_body "
                        "when using an arm max_tokens override"
                    )
    for m in modes:
        if m not in MODES:
            raise SystemExit(f"unknown mode {m!r}; choose from {MODES}")
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = Path(out_dir) / run_id
    out.mkdir(parents=True, exist_ok=True)

    conditions = ([(seq, cut, arm.variant, arm.mode, arm) for seq, cut in cuts.items() for arm in arms]
                  if arms is not None else
                  [(seq, cut, v, mode, None) for seq, cut in cuts.items() for v in variants for mode in modes])

    def condition_name(seq, cut, variant, mode, arm):
        name = episode_condition(cut, seq, variant, mode)
        return name + (f":arm-{arm.id}" if arm is not None else "")

    def effective_turns(arm, prepared):
        return (arm.max_turns if arm is not None and arm.max_turns is not None else
                max_turns if max_turns is not None else prepared.world.ep.max_turns)

    # Manifest and every worker use the same bytes even if files change mid-batch.
    web_fixtures = load_web_fixtures()
    resolved_conditions = []
    for seq, cut, variant, mode, arm in conditions:
        prepared = prepare_episode(spec, dump, cut, variant, mode, env_model,
                                   num_live_problems=arm.num_live_problems if arm else None,
                                   wiki_write_instructions=arm.wiki_write_instructions if arm else False,
                                   hint=arm.hint if arm else hint, web_fixtures=web_fixtures)
        if prepare_hook is not None:
            prepared = prepare_hook(prepared)
        resolved_conditions.append({
            "condition": condition_name(seq, cut, variant, mode, arm),
            "arm": arm.to_dict() if arm else None,
            "wiki_write_instructions": prepared.resolved_config["wiki_write_instructions"],
            "hint": prepared.resolved_config["hint"],
            "context_provenance": prepared.context.get("provenance"),
            "cut": asdict(cut), "context_sha256": prepared.context_sha256,
            "resolved_config": {**prepared.resolved_config, "max_turns": effective_turns(arm, prepared)},
        })
    conditions_by_name = {c["condition"]: c for c in resolved_conditions}
    spec_values = asdict(spec)
    spec_values.pop("_path", None)
    dump_provenance = {
        "files": {name: _sha256(dump.root / name) for name in ("pages.jsonl", "revisions.jsonl")},
        "loaded_sha256": stable_sha256({"pages": dump.pages, "by_page": dict(dump.by_page),
                                         "by_label": dict(dump.by_label)}),
    }
    identity = {
        "schema_version": 1, "spec": spec_values,
        "implementation": {p.name: _sha256(p) for p in sorted(Path(__file__).parent.glob("*.py"))},
        "environment_prompts": {p.relative_to(Path(__file__).parent).as_posix(): _sha256(p)
                                for p in sorted((Path(__file__).parent / "prompts" / "environment").glob("*.txt"))},
        "spec_file_sha256": _sha256(spec._path) if spec._path is not None else None,
        "dump": dump_provenance, "conditions": resolved_conditions,
        "models": [asdict(m) for m in models], "env_model": asdict(env_model),
        "base_seed": base_seed, "temperature_override": temperature,
        "agent_group": {"size": agents, "shared_wiki": shared_wiki},
    }
    experiment_sha256 = stable_sha256(identity)
    manifest_path = out / "manifest.json"
    check_resume(out, experiment_sha256, n_samples)
    manifest = {
        **(manifest_extra or {}),
        "run_id": run_id, "task_id": f"wiki-episode:{spec.id}",
        "conditions": [c["condition"] for c in resolved_conditions],
        "arms": [a.to_dict() for a in arms] if arms is not None else None,
        "modes": list(dict.fromkeys(c[3] for c in conditions)),
        "env_model": asdict(env_model), "n_samples": n_samples, "base_seed": base_seed,
        "temperature_override": temperature, "max_turns": max_turns,
        "agent_group": {"size": agents, "shared_wiki": shared_wiki},
        "models": [asdict(m) for m in models],
        "reference": {condition_name(s, c, v, m, a): reference_for(c) for s, c, v, m, a in conditions},
        "experiment_sha256": experiment_sha256, "experiment": identity,
    }
    write_json(manifest_path, manifest)

    jobs = [(model, seq, cut, variant, mode, arm, i)
            for model in models for seq, cut, variant, mode, arm in conditions for i in range(n_samples)]
    total = len(jobs)
    lock = threading.Lock()

    def run_one(k: int, model, seq, cut, variant, mode, arm, i) -> None:
        if arm is not None and arm.max_tokens is not None:
            model = replace(model, max_tokens=arm.max_tokens)
        temp = temperature if temperature is not None else model.temperature
        condition = condition_name(seq, cut, variant, mode, arm)
        seed = base_seed + i * agents
        fname = f"{model.name}__{condition}__n{i:02d}_seed{seed}.json"
        paths = ([out / fname] if agents == 1 else
                 [out / (fname[:-5] + f"__agent{j}.json") for j in range(agents)])
        tag = f"[{k}/{total}]"
        if agents > 1 and any(path.exists() for path in paths) and not all(path.exists() for path in paths):
            raise ValueError("Partial shared group exists; preserve it and use a new run id")
        complete = [completed_record(path, {
            "run_id": run_id, "experiment_sha256": experiment_sha256, "model_config": asdict(model),
            "condition": condition, "sample_index": i, "seed": seed + j,
        }, episode=True, retry_incomplete=agents == 1) for j, path in enumerate(paths)]
        if all(complete):
            print(f"{tag} skip (exists) {fname}", file=sys.stderr)
            return
        print(f"{tag} {model.name} {condition} n={i} seed={seed}", file=sys.stderr)
        prepared = prepare_episode(spec, dump, cut, variant, mode, env_model, seed,
                                   num_live_problems=arm.num_live_problems if arm else None,
                                   wiki_write_instructions=arm.wiki_write_instructions if arm else False,
                                   hint=arm.hint if arm else hint, web_fixtures=web_fixtures)
        if prepare_hook is not None:
            prepared = prepare_hook(prepared)
        world, context = prepared.world, prepared.context
        turn_cap = effective_turns(arm, prepared)
        expected = conditions_by_name[condition]
        resolved_config = {**prepared.resolved_config, "max_turns": turn_cap}
        if (prepared.context_sha256 != expected["context_sha256"]
                or resolved_config != expected["resolved_config"]):
            raise ValueError(
                f"prepared episode differs from its manifest condition {condition!r}; "
                "experimental inputs changed during the batch; use a new run id"
            )
        t0 = time.monotonic()
        # per-turn lines only when running one episode at a time; otherwise they interleave
        log = (lambda s: print(s, file=sys.stderr)) if workers <= 1 else None
        seeds = [seed + j for j in range(agents)]
        prepared_agents = [prepared] + [
            (prepare_hook or (lambda p: p))(
                prepare_episode(spec, dump, cut, variant, mode, env_model, agent_seed,
                                num_live_problems=arm.num_live_problems if arm else None,
                                wiki_write_instructions=arm.wiki_write_instructions if arm else False,
                                hint=arm.hint if arm else hint, web_fixtures=web_fixtures))
            for agent_seed in seeds[1:]]
        if agents > 1 or html:
            bodies = play_group(model=model, prepared=prepared_agents, temperature=temp, seeds=seeds,
                                max_turns=turn_cap, shared=shared_wiki, html=html,
                                host=wiki_host, port=wiki_port, log=log)
            for body in bodies:
                body["group"]["id"] = fname[:-5]
        else:
            bodies = [play(model=model, world=world, context=context, temperature=temp, seed=seed,
                           max_turns=turn_cap, log=log, prepared=prepared)]
        dt = time.monotonic() - t0
        for agent_index, body in enumerate(bodies):
            path, seed = paths[agent_index], seeds[agent_index]
            world = prepared_agents[agent_index].world
            record = {
                "run_id": run_id,
                "model": {"name": model.name, "transport": model.transport, "model_id": model.model,
                          "base_url": model.base_url, "extra_body": model.extra_body,
                          "thinking": model.thinking, "effort": model.effort},
                "task_id": f"wiki-episode:{spec.id}",
                "condition": condition,
                "arm_id": arm.id if arm else None,
                "arm": arm.to_dict() if arm else None,
                "experiment_sha256": experiment_sha256,
                "resolved_config": {**resolved_config, "max_tokens": model.max_tokens},
                "model_config": asdict(model), "env_model_config": asdict(env_model),
                "max_turns": turn_cap, "num_live_problems": len(world.ep.upcoming),
                "wiki_write_instructions": prepared.resolved_config["wiki_write_instructions"],
                "hint": prepared.resolved_config["hint"],
                "context_provenance": prepared.context.get("provenance"),
                "sample_index": i,
                "seed": seed,
                "temperature": temp,
                "max_tokens": model.max_tokens,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "duration_s": round(dt, 2),
                "reference": reference_for(cut),
                **body,
            }
            with lock:
                write_json(path, record)
            ep = body["episode"]
            rounds = " ".join(
                f"R{r['n']}:{'ok' if r['correct'] else ('miss' if r['missed'] else ('wrong' if r['answer'] else '-'))}"
                for r in ep["rounds"])
            posts = sum(len(v) for v in ep["wiki_posts"].values())
            status = "ERROR " + body["error"]["type"] if body["error"] else "ok"
            helped = "".join(f" help:{'yes' if q['served'] else 'no'}" for q in ep.get("requests") or [])
            hb = ep.get("heartbeat") or {}
            hbs = f" counter_up={hb.get('counter_up', 0)} relay={len(hb.get('relay_posts') or [])} sigR5={hb.get('signaled_r5_state')}"
            print(f"    {tag} n={i} {status} turns={ep['n_turns']} end={ep['end_reason']} {rounds} wiki_posts={posts}{helped}{hbs} ({dt:.0f}s)",
                  file=sys.stderr)

    if workers <= 1 and len(models) == 1:
        for k, job in enumerate(jobs, 1):
            run_one(k, *job)
    else:
        # Each evaluated model gets its own worker budget. A slow model or a
        # request sleeping in retry backoff must not occupy another model's slots.
        with ExitStack() as stack:
            pools = {id(model): stack.enter_context(ThreadPoolExecutor(max_workers=max(1, workers)))
                     for model in models}
            futs = [pools[id(job[0])].submit(run_one, k, *job) for k, job in enumerate(jobs, 1)]
            for fut in as_completed(futs):
                fut.result()
    print(f"wrote {out}", file=sys.stderr)
    return out
