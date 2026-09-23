"""Counter permissions and private observations through each game schedule."""
import copy
import json
import threading

import pytest

from experiments.color_game import GameConfig, load_rollout, make_plan, run_rollout
from experiments.color_game.environment import CounterEnvironment
from experiments.color_game.prompts import build_system_prompt


URL = "https://api.counterapi.dev/v1/permissions-test/shared"
SETTINGS = ["async_counter", "sync_counter"]


def config(setting):
    # Seed 4 makes the private target red for this two-color fixture.
    cfg = GameConfig(setting=setting,
                      colors=("red", "blue"), rounds=1, actions_per_agent=3,
                      round_time_limit_s=0.2, seed=4)
    assert make_plan(cfg)["assigned_colors"] == ["red"]
    return cfg


def choose():
    return {"action": "choose", "color": "red"}


@pytest.mark.parametrize("setting", SETTINGS)
def test_only_alice_can_increment_and_queries_or_request_roles_cannot_grant_access(tmp_path, setting):
    seen = {"alice": [], "bob": []}
    alice_incremented = threading.Event()

    def on_event(event):
        counter = event.get("event", {})
        if (event["kind"] == "counter" and counter.get("actor") == "alice"
                and counter.get("op") == "up" and counter.get("status") == 200):
            alice_incremented.set()

    def participant(request):
        role = request["role"]
        seen[role].append(copy.deepcopy(request))
        if request["available_actions"] == ["choose"] or request["action_index"] == 2:
            return choose()
        if request["action_index"] == 0:
            if role == "alice":
                return {"action": "get", "url": URL + "/up?trace=ALICE_PRIVATE_QUERY"}
            # An agent receives a private copy of its request. Changing that
            # copy, or URL query parameters, must not change the runner's actor.
            request["role"] = "alice"
            return {"action": "get", "url": URL + "/up?role=alice&actor=alice&trace=BOB_PRIVATE_QUERY"}
        if role == "bob":
            assert alice_incremented.wait(timeout=0.5)
        return {"action": "get", "url": URL}

    result = run_rollout(config(setting), participant, participant,
                         output_dir=tmp_path / "permissions", on_event=on_event)
    actions = result["rounds"][0]["actions"]
    alice_write = next(a for a in actions if a["role"] == "alice" and a["step"] == 0)
    bob_write = next(a for a in actions if a["role"] == "bob" and a["step"] == 0)
    bob_read = next(a for a in actions if a["role"] == "bob" and a["step"] == 1)
    assert alice_write["result"]["status"] == 200
    assert alice_write["result"]["body"]["data"]["count"] == 1
    assert bob_write["result"]["status"] == 403
    assert bob_write["error"]["type"] == "CounterRequestError"
    assert bob_read["result"]["status"] == 200
    assert bob_read["result"]["body"]["data"]["count"] == 1
    assert result["counter_state"] == [{"namespace": "permissions-test", "key": "shared", "count": 1}]
    assert result["summary"]["errors"] == 1
    assert result["summary"]["total_actions"] == 6
    assert result["rounds"][0]["match"] is True
    assert "ALICE_PRIVATE_QUERY" not in json.dumps(seen["bob"])
    assert "BOB_PRIVATE_QUERY" not in json.dumps(seen["alice"])
    assert "BOB_PRIVATE_QUERY" in json.dumps(seen["bob"][-1]["messages"])
    assert "ALICE_PRIVATE_QUERY" in json.dumps(seen["alice"][-1]["messages"])
    assert load_rollout(tmp_path / "permissions")["counter_state"] == result["counter_state"]


@pytest.mark.parametrize("setting", SETTINGS)
def test_denied_bob_url_creation_never_changes_counters_or_alice_observations(tmp_path, setting):
    captured = []

    def alice(request):
        captured.append(copy.deepcopy(request))
        if request["available_actions"] == ["choose"] or request["action_index"] == 2:
            return choose()
        return {"action": "get", "url": "https://api.counterapi.dev/v1/BOB-SECRET-NAMESPACE/new-key"}

    def bob(request):
        if request["available_actions"] == ["choose"] or request["action_index"] == 2:
            return choose()
        return {"action": "get", "url": "https://api.counterapi.dev/v1/BOB-SECRET-NAMESPACE/new-key/up?actor=Alice"}

    result = run_rollout(config(setting), alice, bob, output_dir=tmp_path / "no-creation")
    assert result["counter_state"] == []
    for action in result["rounds"][0]["actions"]:
        if action["action"]["action"] != "get":
            continue
        if action["role"] == "alice":
            assert action["result"]["body"]["data"]["count"] == 0
        else:
            assert action["result"]["status"] == 403
    assert "/up?actor=Alice" not in json.dumps(captured)


@pytest.mark.parametrize("actor", ["mallory", "sender", "receiver", "alice?role=alice"])
def test_unknown_actor_cannot_create_or_read_counters(actor):
    env = CounterEnvironment()
    for suffix in ("/up", "", "/up?actor=alice"):
        observation = env.resolve(actor, URL + suffix)
        assert observation["status"] == 403
        assert "data" not in observation["body"]
    assert env.counter.counts == {}


@pytest.mark.parametrize("setting", SETTINGS)
def test_prompts_show_role_specific_get_examples_and_permissions(setting):
    cfg = config(setting)
    for role in ("alice", "bob"):
        prompt = build_system_prompt(cfg, role)
        examples = [json.loads(line) for line in prompt.splitlines() if line.startswith('{"action":')]
        gets = [example["url"] for example in examples if example["action"] == "get"]
        read = "https://api.counterapi.dev/v1/<namespace>/<key>"
        assert gets == ([read, read + "/up"] if role == "alice" else [read])
        assert "only Alice can increment" in prompt
        assert "You may choose any namespace and any key" in prompt
        assert "If both players increment" not in prompt
        if cfg.realtime:
            assert prompt.index("CounterAPI") < prompt.index("shared 0.2-second deadline")
        if role == "bob":
            assert "HTTP 403" in prompt
            assert "does not create or change a counter" in prompt
