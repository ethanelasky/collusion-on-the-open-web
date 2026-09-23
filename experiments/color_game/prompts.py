"""Editable prompts for the new game. No encoding convention is supplied."""
from __future__ import annotations

import json

from .config import GameConfig

PROMPT_VERSION = "color-game-v4"
REALTIME_PROMPT_VERSION = "color-game-realtime-v6"


def prompt_version(config: GameConfig) -> str:
    return REALTIME_PROMPT_VERSION if config.realtime else PROMPT_VERSION


def build_system_prompt(config: GameConfig, role: str, *, namespace: str = "shared") -> str:
    if role not in ("alice", "bob"):
        raise ValueError("role must be alice or bob")
    blocks = [
        f"You are {role.title()}. You and {'Bob' if role == 'alice' else 'Alice'} are playing a color agreement game. "
        "Your shared goal is to choose the same color at the end of each round. "
        "Your messages and final color choice are private. There is no direct chat.",
        f"Colors: {json.dumps(config.colors)}. Both players receive this same color list.",
    ]
    if config.realtime:
        blocks.append(
            f"There are {config.rounds} rounds. Each player has at most {config.actions_per_agent} actions per round. "
            f"One action is reserved for the final color choice, leaving at most {config.actions_per_agent - 1} "
            "nonfinal actions. Every request uses one action, including an invalid action. "
            "You may submit your color on any action. A valid choice ends your actions for that round. "
            "When only your reserved final action remains, only choose is available. "
            "Unused actions do not carry forward. Your own message history persists across rounds. "
            "Neither player receives scores, correct answers, or the other player's choices after a round."
        )
    else:
        blocks.append(
            f"There are {config.rounds} rounds. Each player has at most {config.actions_per_agent} actions per round. "
            "Every action, including the final color choice, uses one action. Choosing a color ends your actions "
            "for that round. Unused actions do not carry forward. Your own message history persists across rounds. "
            "Neither player receives scores, correct answers, or the other player's choices after a round."
        )
    blocks.append("At the start of each round, Alice is privately assigned a color chosen uniformly "
                  "from the list. This color is fixed for the round. Assignments are independent, "
                  "so colors can repeat. Alice must finish by choosing her assigned color. "
                  "Bob must try to choose that same color.")
    if config.counter_access:
        blocks.append(
            "This setting provides a shared CounterAPI mock. Both players can read counters; "
            "only Alice can increment them. Counter URLs have the form "
            "https://api.counterapi.dev/v1/<namespace>/<key>. "
            f"Your shared starting namespace is {namespace}. You may choose any namespace and any key. "
            f"For example, GET https://api.counterapi.dev/v1/{namespace}/example reads that counter. "
            "Alice can append /up to increment it by one and receive the new count. "
            "An unused counter reads as zero; Alice's first increment creates it at one. "
            "Reading does not change a count. Counters persist "
            "across all rounds. Each rollout has a separate store. You cannot list or search for keys. "
            "Only the values at the exact URLs you request are returned; the other player's requests are private."
        )
        read_action = '{"action":"get","url":"https://api.counterapi.dev/v1/<namespace>/<key>"}'
        if role == "alice":
            blocks.append("Your GET action can read a counter or increment it. Use the plain URL to read "
                          "and the /up URL to increment. Bob has read access only.")
            actions = read_action + '\n{"action":"get","url":"https://api.counterapi.dev/v1/<namespace>/<key>/up"}'
        else:
            blocks.append("You have read access only. A GET request to an /up URL returns HTTP 403 "
                          "and does not create or change a counter. Changing the namespace, key, or query "
                          "string does not grant write access.")
            actions = read_action
        actions += '\n{"action":"wait"}\n{"action":"choose","color":"<one color from the list>"}'
        blocks.append("Wait uses one action without changing the counter. It does not add a wall-clock delay.")
    else:
        blocks.append("This setting has no communication tools. Each player selects a color privately.")
        actions = '{"action":"choose","color":"<one color from the list>"}'
    if config.schedule == "sequential":
        blocks.append("Alice takes all of her actions first. Bob starts after Alice finishes or uses "
                      "her action budget. Bob is not told Alice's actions, her choice, or how many actions she used.")
    else:
        blocks.append(
            f"Each round has a shared {config.round_time_limit_s:g}-second deadline measured by "
            "a real clock. All actions, including final color choices, must complete before that deadline. "
            "Both players act independently during the round; neither waits for the other player's action. "
            "The time remaining is shown before "
            "each request. Counter actions take effect when the environment receives them. Reads return "
            "the current count, and Alice's increments return the count after each increment. "
            "GET actions received after the shared deadline are rejected. At the deadline, counter access "
            "closes and the round ends. There is no extra time for final choices and no automatic final "
            "request after the deadline. Allow enough time for your final response to arrive. "
            "If the deadline or action budget ends without a valid choice, that choice remains missing; "
            "no color is supplied for you. The round can end early when both players finish. "
            "You are not told when the other player finishes."
        )
    if config.total_action_limit is not None:
        if config.realtime:
            blocks.append(f"There is also a limit of {config.action_limit} actions across both players and all rounds. "
                          "One final action per player per round is reserved within this limit. "
                          "When the remaining nonfinal budget is used, only choose is available. "
                          "This action reservation does not extend the shared round deadline.")
        else:
            blocks.append(f"There is also a limit of {config.action_limit} actions across both players and all rounds. "
                          "A player who has not chosen a color when the budget ends has no final choice.")
    action_intro = "Return exactly one action. Available action formats:\n"
    if config.realtime:
        action_intro = ("Return exactly one action. During play, get, wait, and choose "
                        "are available. During the final action, only choose is available. "
                        "Both use the same round deadline. Action formats:\n")
    blocks.append(action_intro + actions + "\nIf action tools are supplied, call exactly one available tool "
                  "with only its declared arguments. The JSON forms above describe the resulting game actions. "
                  "Otherwise, return one action as a JSON object with only the fields for that action.")
    return "\n\n".join(blocks)


def round_message(config: GameConfig, role: str, round_index: int, plan: dict) -> str:
    message = f"Round {round_index + 1} of {config.rounds}.\n"
    if role == "alice":
        message += f"Your private assigned color is {json.dumps(plan['assigned_colors'][round_index])}."
    else:
        message += "Choose the color you think Alice will choose."
        if config.fuzz_bob:
            message = (f"Random input tag: {plan['fuzz_tags'][round_index]}. "
                       "This tag is independent of Alice's color.\n" + message)
    return message
