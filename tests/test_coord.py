"""Shared multi-agent core: the counter medium, cross-agent visibility, and global-time ordering.

Fully offline (ScriptedParticipants, no model, no network).
"""
from __future__ import annotations

from ai_collusion.coord import CounterMedium, ScriptedParticipant, resolve, run_episode

NS = "sector61-state5-fast-9417"
UP = f"https://api.counterapi.dev/v1/{NS}/ID/up"
READ = f"https://api.counterapi.dev/v1/{NS}/ID"


def test_counter_increments_reads_and_isolates_keys():
    c = CounterMedium()
    assert '"count":1' in c.resolve("a", UP, 0.0).body
    assert '"count":2' in c.resolve("a", UP, 1.0).body
    assert '"count":2' in c.resolve("a", READ, 2.0).body                 # bare read does not increment
    assert '"count":1' in c.resolve("a", f"https://api.counterapi.dev/v1/{NS}/WV/up", 3.0).body
    assert c.counters[(NS, "ID")] == 2 and c.counters[(NS, "WV")] == 1
    assert c.resolve("a", "https://example.com/x", 4.0) is None          # not this medium's URL


def test_response_shape_matches_stage0_env_counter():
    # Guards against drift between CounterMedium and env.World._counter (same contract, two callers).
    body = CounterMedium().resolve("a", UP, 0.0).body
    assert body.startswith(f"--- GET {UP} ---\nHTTP 200\n") and body.rstrip().endswith("--- end ---")


def test_scheduler_orders_by_global_time_and_shares_state_across_agents():
    counter = CounterMedium()
    # A increments ID at t=0,2; B reads ID at t=1,3. Global-time order: A up, B read(=1), A up, B read(=2).
    a = ScriptedParticipant("ahead", [UP, UP], start_s=0.0, step_s=2.0)
    b = ScriptedParticipant("behind", [READ, READ], start_s=1.0, step_s=2.0)
    res = run_episode([a, b], [counter], max_turns=10)
    assert res.end_reason == "all_done"
    seq = [(t.agent_id, t.at_global_s) for t in res.turns]
    assert seq == [("ahead", 0.0), ("behind", 1.0), ("ahead", 2.0), ("behind", 3.0)]
    reads = [t.result for t in res.turns if t.agent_id == "behind"]
    assert '"count":1' in reads[0] and '"count":2' in reads[1]           # behind sees ahead's writes


def test_resolve_helper_tries_media_in_order():
    counter = CounterMedium()
    assert resolve([counter], "a", "https://example.com", 0.0) is None
    assert resolve([counter], "a", UP, 0.0).source == "counter"
