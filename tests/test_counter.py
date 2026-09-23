"""The canonical deterministic counter (ai_collusion.counter). Offline; no deps."""
from __future__ import annotations

from ai_collusion.counter import Counter, counter_body, parse_counter_url

NS = "sector61-state5-fast-9417"
UP = f"https://api.counterapi.dev/v1/{NS}/ID/up"
READ = f"https://api.counterapi.dev/v1/{NS}/ID"


def test_direct_api_increment_read_isolation():
    c = Counter()
    assert c.get(NS, "ID") == 0
    assert c.up(NS, "ID") == 1 and c.up(NS, "ID") == 2
    assert c.get(NS, "ID") == 2
    assert c.get(NS, "WV") == 0 and c.up(NS, "WV") == 1        # keys independent
    assert c.counts[(NS, "ID")] == 2


def test_url_parsing():
    assert parse_counter_url(UP) == (NS, "ID", "up")
    assert parse_counter_url(READ) == (NS, "ID", "read")
    assert parse_counter_url(f"{UP}?x=cachebust") == (NS, "ID", "up")     # query ignored
    assert parse_counter_url("https://example.com/v1/x/y/up") is None     # wrong host


def test_fetch_increments_reads_and_logs():
    c = Counter()
    b1 = c.fetch(UP, meta={"agent_id": "a", "t": 0})
    assert '"count":1' in b1 and b1.startswith(f"--- GET {UP} ---\nHTTP 200\n") and b1.rstrip().endswith("--- end ---")
    assert '"count":2' in c.fetch(UP)                                    # second up -> 2
    assert '"count":2' in c.fetch(READ)                                  # bare read does not increment
    assert c.fetch("https://example.com/x") is None                      # not a counter URL -> caller falls through
    assert c.hits[0]["op"] == "up" and c.hits[0]["agent_id"] == "a"      # meta recorded
    assert [h["op"] for h in c.hits] == ["up", "up", "read"]             # None fetch not logged


def test_shared_vs_isolated_instances():
    shared = Counter()
    shared.up(NS, "ID")
    assert shared.get(NS, "ID") == 1                                     # a shared instance carries state across callers
    other = Counter()
    assert other.get(NS, "ID") == 0                                     # a separate instance is isolated (blocked-channel control)


def test_body_shape_is_stable():
    assert counter_body(NS, "ID", 3) == '{"code":200,"data":{"namespace":"%s","key":"ID","count":3,"up_count":3}}' % NS
