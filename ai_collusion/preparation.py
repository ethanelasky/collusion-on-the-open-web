"""Prepare the exact initial model context and its matching fresh world."""
from __future__ import annotations

import copy
from dataclasses import dataclass, replace

from .client import ModelConfig
from .env import World, make_world
from .web_fixtures import WebFixtures
from .wiki import Cut, Dump, Spec, build_wiki_context, cut_config
from .run_storage import stable_sha256


def context_sha256(context: dict) -> str:
    return stable_sha256({"system": context["system"], "messages": context["messages"]})


def prepare_context(world: World, context: dict) -> dict:
    """Install prefill once on a fresh world, without changing the caller's context."""
    context = copy.deepcopy(context)
    boundary = context.pop("completed_history_insert_index", None)
    world.install(context["messages"], completed_history_insert_index=boundary)
    search = context.get("provenance", {}).get("search")
    if search:
        world.search_fixtures[search["url"]] = search["result_body"]
    world.seed_history(context)
    return context


def prepare_replay_context(spec: Spec, dump: Dump, cut: Cut, variant: str | None,
                           *, hint: str | None = None) -> dict:
    """Build a single-turn replay, installing episode state only when configured."""
    if cut_config(spec, cut, variant).get("episode"):
        return prepare_episode(spec, dump, cut, variant, "neutral", hint=hint).context
    return build_wiki_context(spec, cut, variant, hint=hint)


@dataclass
class PreparedEpisode:
    world: World
    context: dict
    context_sha256: str
    resolved_config: dict


def prepare_episode(spec: Spec, dump: Dump, cut: Cut, variant: str | None, mode: str,
                    env_model: ModelConfig | None = None, seed: int | None = None,
                    num_live_problems: int | None = None, *,
                    wiki_write_instructions: bool = False,
                    hint: str | None = None,
                    web_fixtures: WebFixtures | None = None) -> PreparedEpisode:
    if type(wiki_write_instructions) is not bool:
        raise ValueError("wiki_write_instructions must be a boolean")
    stop = cut_config(spec, cut, variant).get("stop_after_first_reasoning", False)
    if type(stop) is not bool:
        raise ValueError("stop_after_first_reasoning must be a boolean")
    world = make_world(spec, dump, cut, variant, mode, env_model, seed,
                       web_fixtures=web_fixtures)
    world.wiki_write_instructions = wiki_write_instructions
    if num_live_problems is not None:
        if (type(num_live_problems) is not int or num_live_problems < 1
                or num_live_problems > len(world.ep.upcoming)):
            raise ValueError("num_live_problems must be positive and no greater than the existing live schedule")
        world.ep = replace(world.ep, upcoming=world.ep.upcoming[:num_live_problems])
    context = prepare_context(world, build_wiki_context(
        spec, cut, variant, wiki_write_instructions=wiki_write_instructions, hint=hint))
    return PreparedEpisode(world, context, context_sha256(context), {
        "variant": variant or "base", "mode": mode,
        "cut": copy.deepcopy(cut_config(spec, cut, variant)),
        "max_turns": world.ep.max_turns,
        "num_live_problems": len(world.ep.upcoming),
        "wiki_write_instructions": wiki_write_instructions,
        "hint": hint if hint is not None else "historical_forced_wiki_exposure",
        "hint_provenance": copy.deepcopy(context.get("provenance")),
        "web_fixtures": world.web_fixtures.identity(),
    })
