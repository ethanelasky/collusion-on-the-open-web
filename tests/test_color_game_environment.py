"""Counter state, private observations, and simultaneous-barrier guarantees."""

import json

import pytest

from experiments.color_game.environment import CounterEnvironment


BASE = "https://api.counterapi.dev/v1"


def count(observation):
    assert observation["status"] == 200
    return observation["body"]["data"]["count"]


def test_rollouts_are_isolated_and_state_persists_across_rounds():
    first, second = CounterEnvironment(), CounterEnvironment()
    url = f"{BASE}/shared/color-red"
    assert count(first.resolve("Alice", f"{url}/up", tick=1)) == 1
    assert count(first.resolve("Bob", url, tick=20)) == 1
    assert count(second.resolve("Bob", url, tick=20)) == 0
    assert first.counter is not second.counter
    assert second.counter.counts == {}


def test_reads_do_not_create_keys_or_offer_a_directory_listing():
    env = CounterEnvironment()
    assert count(env.resolve("Alice", f"{BASE}/anything/new-key")) == 0
    assert env.counter.counts == {}
    env.resolve("Alice", f"{BASE}/anything/known-key/up")
    # The canonical parser treats a namespace-only URL as key "default".
    result = env.resolve("Bob", f"{BASE}/anything")
    assert result["body"]["data"] == {
        "namespace": "anything", "key": "default", "count": 0, "up_count": 0
    }
    assert env.counter.counts == {("anything", "known-key"): 1}


def test_arbitrary_namespaces_and_keys_use_canonical_parser_semantics():
    env = CounterEnvironment()
    assert count(env.resolve("Alice", f"{BASE}/CA5/round_12-brown/up?nonce=abc")) == 1
    assert count(env.resolve("Bob", "https://api.counterapi.dev/CA5/round_12-brown?x=2")) == 1
    assert count(env.resolve("Bob", f"{BASE}/another_namespace/round_12-brown")) == 0
    assert count(env.resolve("Alice", f"{BASE}/CA5/RED/up")) == 1
    assert count(env.resolve("Alice", f"{BASE}/CA5/red")) == 0


def test_simultaneous_bob_increment_is_rejected_while_alice_increment_persists():
    env = CounterEnvironment()
    url = f"{BASE}/common/red"
    results = env.resolve_batch({"Bob": f"{url}/up", "Alice": f"{url}/up"}, tick=4)
    assert count(results["Alice"]) == 1
    assert results["Bob"]["status"] == 403
    assert "data" not in results["Bob"]["body"]
    assert env.counter.get("common", "red") == 1
    assert count(env.resolve("Bob", url, tick=5)) == 1
    assert [(event["actor"], event["before"], event["after"])
            for event in env.events[:2]] == [("Alice", 0, 1), ("Bob", 1, 1)]
    assert [event["observed_count"] for event in env.events[:2]] == [1, None]
    assert [event["status"] for event in env.events[:2]] == [200, 403]


def test_simultaneous_bob_read_does_not_observe_same_tick_alice_write():
    env = CounterEnvironment()
    url = f"{BASE}/common/signal"
    env.resolve("Alice", f"{url}/up")
    results = env.resolve_batch({"Alice": f"{url}/up", "Bob": url}, tick=8)
    assert count(results["Alice"]) == 2
    assert count(results["Bob"]) == 1
    assert [(event["actor"], event["op"], event["before"], event["after"])
            for event in env.events[-2:]] == [("Bob", "read", 1, 1), ("Alice", "up", 1, 2)]
    assert count(env.resolve("Bob", url, tick=9)) == 2


def test_sequential_reads_observe_prior_writes_and_logs_stay_private():
    env = CounterEnvironment()
    url = f"{BASE}/common/signal"
    env.resolve("Alice", f"{url}/up", tick=0)
    observation = env.resolve("Bob", url, tick=1)
    assert count(observation) == 1
    assert set(observation) == {"url", "status", "body"}
    assert env.events == [
        {"actor": "Alice", "url": f"{url}/up", "tick": 0, "simultaneous": False,
         "status": 200, "namespace": "common", "key": "signal", "op": "up",
         "before": 0, "after": 1, "pre_tick_count": 0, "observed_count": 1},
        {"actor": "Bob", "url": url, "tick": 1, "simultaneous": False,
         "status": 200, "namespace": "common", "key": "signal", "op": "read",
         "before": 1, "after": 1, "pre_tick_count": 1, "observed_count": 1},
    ]
    json.dumps({"observations": observation, "events": env.events})


@pytest.mark.parametrize("url", [
    "http://api.counterapi.dev/v1/ns/key/up",
    "https://counterapi.dev/v1/ns/key/up",
    "https://api.counterapi.dev.evil.example/v1/ns/key/up",
    "https://evil-counterapi.dev/v1/ns/key/up",
    "https://api.counterapi.dev@evil.example/v1/ns/key/up",
    "https://user@api.counterapi.dev/v1/ns/key/up",
    "https://api.counterapi.dev:443/v1/ns/key/up",
    "https://API.COUNTERAPI.DEV/v1/ns/key/up",
    "https://api.counterapi.dev\n/v1/ns/key/up",
    "https://[invalid/v1/ns/key/up",
    "/v1/ns/key/up",
])
def test_only_exact_counter_origin_is_available(url):
    env = CounterEnvironment()
    result = env.resolve("Alice", url, tick=2)
    assert result["status"] == 400
    assert env.counter.counts == {}
    assert env.events[0]["status"] == 400
    assert env.events[0]["url"] == url


def test_bad_request_does_not_cancel_peer_action_in_simultaneous_tick():
    env = CounterEnvironment()
    results = env.resolve_batch({
        "Bob": "https://example.com/",
        "Alice": f"{BASE}/fresh/free-choice/up",
    }, tick=3)
    assert results["Bob"]["status"] == 400
    assert count(results["Alice"]) == 1
    assert env.counter.counts == {("fresh", "free-choice"): 1}


def test_empty_batch_has_no_effect():
    env = CounterEnvironment()
    assert env.resolve_batch({}, tick=1) == {}
    assert env.events == []
    assert env.counter.counts == {}


@pytest.mark.parametrize("suffix", ["up", "UP", "Up/", "up/?fuzz=one", "UP?x=two#fragment"])
@pytest.mark.parametrize("prefix", ["/v1", "", "/V1"])
def test_bob_cannot_increment_through_canonical_url_variants(prefix, suffix):
    env = CounterEnvironment()
    url = f"https://api.counterapi.dev{prefix}/new-namespace/CA5/{suffix}"
    result = env.resolve("bob", url, tick=7)
    assert result["status"] == 403
    assert env.counter.counts == {}
    assert env.events == [{
        "actor": "bob", "url": url, "tick": 7, "simultaneous": False,
        "status": 403, "namespace": "new-namespace", "key": "CA5", "op": "up",
        "before": 0, "after": 0, "pre_tick_count": 0, "observed_count": None,
    }]


@pytest.mark.parametrize("batched", [False, True])
def test_bob_rejection_does_not_modify_an_existing_counter(batched):
    env = CounterEnvironment()
    url = f"{BASE}/existing/signal"
    env.resolve("alice", f"{url}/up")
    if batched:
        result = env.resolve_batch({"bob": f"{url}/up"})["bob"]
    else:
        result = env.resolve("bob", f"{url}/up")
    assert result["status"] == 403
    assert env.counter.counts == {("existing", "signal"): 1}
    assert env.events[-1]["before"] == env.events[-1]["after"] == 1
    assert count(env.resolve("bob", url)) == 1


@pytest.mark.parametrize("actor", ["Mallory", "sender", "receiver", "", " alice", "bob "])
@pytest.mark.parametrize("operation", ["", "/up"])
def test_unknown_actors_cannot_read_or_increment(actor, operation):
    env = CounterEnvironment()
    url = f"{BASE}/private/key{operation}"
    result = env.resolve(actor, url)
    assert result["status"] == 403
    assert "data" not in result["body"]
    assert env.counter.counts == {}
    assert env.events[-1]["before"] == env.events[-1]["after"] == 0


def test_bob_plain_get_only_reads_and_never_creates_counters():
    env = CounterEnvironment()
    url = f"{BASE}/fresh/read-only"
    assert count(env.resolve("BOB", url)) == 0
    assert count(env.resolve("bob", url + "?up=true")) == 0
    assert env.counter.counts == {}
    assert count(env.resolve("ALICE", url + "/UP")) == 1
    assert count(env.resolve("bob", url)) == 1


def test_unknown_actor_in_batch_cannot_mutate_or_cancel_alice_request():
    env = CounterEnvironment()
    results = env.resolve_batch({
        "alice": f"{BASE}/valid/alice/up",
        "unknown": f"{BASE}/forbidden/new-key/up",
    })
    assert count(results["alice"]) == 1
    assert results["unknown"]["status"] == 403
    assert env.counter.counts == {("valid", "alice"): 1}
