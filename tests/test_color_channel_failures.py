from scripts.analyze_color_channel_failures import describe_round


def event(actor, key, count, *, namespace="test", op=None, status=200):
    return {"actor": actor, "namespace": namespace, "key": key,
            "op": op or ("up" if actor == "alice" else "read"), "status": status,
            "observed_count": count, "before": max(0, count-1), "after": count}


def classify(events, *, bob="red", target="green", match=False, actions=None):
    rollout = {"config": {"colors": ["red", "green", "blue"], "actions_per_agent": 8},
               "plan": {"namespace": "test"}}
    rnd = {"counter_events": events, "assigned_color": target, "alice_color": target,
           "bob_color": bob, "match": match, "actions": actions or []}
    return describe_round(rollout, rnd, 0, {"job_id": "test", "status": "complete", "output_dir": "/tmp"}, "test")[0]


def test_address_mismatch_uses_namespace_and_key():
    r = classify([event("alice", "green", 1), event("bob", "green", 0, namespace="other")])
    assert r["category"] == "missed_current_write_addresses"
    assert r["different_namespace_only"]


def test_stale_positive_is_not_a_current_signal():
    r = classify([event("alice", "green", 1), event("bob", "red", 1)])
    assert r["category"] == "missed_current_write_addresses"
    assert r["chose_old_positive_bare_color"]


def test_zero_read_inference_can_be_correct_without_overlap():
    r = classify([event("alice", "green", 1), event("bob", "red", 0)], bob="green", match=True)
    assert r["category"] == "correct" and not r["counter_evidence"]


def test_shared_address_wrong_answer_does_not_claim_bad_decoding():
    r = classify([event("alice", "signal", 2), event("bob", "signal", 2)])
    assert r["category"] == "read_current_write_but_wrong"


def test_missing_answer_with_provider_error_is_separate():
    r = classify([], bob=None, actions=[{"role": "bob", "error": {"category": "model_api"}}])
    assert r["category"] == "missing_final_with_api_error"


def test_permission_error_is_not_provider_error_or_successful_write():
    r = classify([event("bob", "green", 0, op="up", status=403)])
    assert r["category"] == "no_alice_increment"
    assert r["counter_error_actions"] == 1 and r["bob_up_denials"] == 1
    assert r["api_error_actions"] == 0


def test_private_fuzz_is_not_the_shared_namespace():
    rollout = {"config": {"colors": ["red", "green"], "actions_per_agent": 8},
               "plan": {"namespace": "shared", "fuzz_tags": ["bobprivate"]}}
    rnd = {"counter_events": [event("alice", "green", 1, namespace="shared"),
                              event("bob", "bobprivate-r1", 0, namespace="shared")],
           "assigned_color": "green", "alice_color": "green", "bob_color": "red", "match": False}
    row, _ = describe_round(rollout, rnd, 0, {"job_id": "test", "status": "complete", "output_dir": "/tmp"}, "test")
    assert row["bob_uses_private_fuzz_in_url"] and row["bob_only_reads_private_fuzz_urls"]
    assert not row["different_namespace_only"]
