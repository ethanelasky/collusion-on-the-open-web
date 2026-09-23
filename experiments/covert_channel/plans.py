"""Target-independent random tags and immutable inputs paired across model groups."""
import random
import re
import secrets


def make_plan(seed, session_index, answers, rounds):
    target_rng = random.Random(f"counter-targets:{seed}:{session_index}")
    order_rng = random.Random(f"counter-orders:{seed}:{session_index}")
    orders = {role: list(answers) for role in ("sender", "receiver")}
    for order in orders.values():
        order_rng.shuffle(order)
    while orders["sender"] == orders["receiver"]:
        order_rng.shuffle(orders["receiver"])
    return {"schema": "counter-session-plan/v1", "session_index": session_index,
            "answer_set": list(answers), "displayed_answer_sets": orders,
            "targets": [target_rng.choice(answers) for _ in range(rounds)],
            "nonce": secrets.token_hex(16),
            "question_tags": [secrets.token_hex(12) for _ in range(rounds)]}


def validate_plan(plan, answers, rounds, question_fuzz):
    if plan["schema"] != "counter-session-plan/v1" or plan["answer_set"] != answers:
        raise ValueError("Plan schema or answer set does not match")
    if len(plan["targets"]) != rounds or any(t not in answers for t in plan["targets"]):
        raise ValueError("Plan targets do not match round count or answer set")
    orders = plan["displayed_answer_sets"]
    if set(orders) != {"sender", "receiver"} or any(
        len(o) != len(answers) or set(o) != set(answers) for o in orders.values()
    ):
        raise ValueError("Each role must have a permutation of the answer set")
    pattern = r"[A-Za-z0-9_-]+"
    if not isinstance(plan["nonce"], str) or not re.fullmatch(pattern, plan["nonce"]):
        raise ValueError("Invalid counter nonce")
    tags = plan["question_tags"]
    if len(tags) != rounds or any(
        not isinstance(t, str) or not re.fullmatch(pattern, t) for t in tags
    ):
        raise ValueError("Each round needs a valid question tag")
    # Tags stay in the plan for pairing; with question fuzz off they are simply not shown to the agents.
