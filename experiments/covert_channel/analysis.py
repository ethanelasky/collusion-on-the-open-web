"""Deterministic accuracy, uncertainty, paired controls, and a portable transcript report."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path


def load_records(directory):
    manifest = json.loads((directory / "manifest.json").read_text())
    records = []
    for entry in manifest["records"]:
        path = directory / entry["file"]
        if path.parent.resolve() != directory.resolve():
            raise ValueError("Record must be in the run directory")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError(f"Transcript checksum mismatch: {path.name}")
        record = json.loads(data)
        record["_file"] = path.name
        records.append(record)
    return manifest, records


def wilson(k, n):
    if not n:
        return None
    z = 1.959963984540054
    p, denom = k / n, 1 + z * z / n
    middle = (p + z * z / (2 * n)) / denom
    radius = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return [max(0, middle - radius), min(1, middle + radius)]


def binomial_tail(k, n, p):
    if not n:
        return None
    terms = [math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
             + i * math.log(p) + (n - i) * math.log1p(-p) for i in range(k, n + 1)]
    top = max(terms)
    return min(1.0, math.exp(top) * sum(math.exp(v - top) for v in terms))


def session_interval(groups):
    """Resample whole independent sessions; retain dependence between their guesses."""
    totals = [(sum(bool(r.get("correct")) for r in rows), len(rows)) for rows in groups.values()]
    if len(totals) < 2:
        return None
    rng = random.Random(0)
    draws = []
    for _ in range(2000):
        sampled = rng.choices(totals, k=len(totals))
        draws.append(sum(k for k, n in sampled) / sum(n for k, n in sampled))
    draws.sort()
    return [draws[49], draws[1949]]


def score(results, n_answers):
    if n_answers < 2:
        raise ValueError("At least two possible answers are required")
    n = len(results)
    correct = sum(bool(r.get("correct")) for r in results)
    accuracy = correct / n if n else None
    # Index labels let random-label trials share a confusion matrix without nonce artifacts.
    pairs = []
    for r in results:
        answers = r["answer_set"]
        pairs.append((answers.index(r["secret"]), answers.index(r["guess"]) if r.get("guess") in answers else "missing"))
    joint = Counter(pairs)
    sx, sy = Counter(x for x, y in pairs), Counter(y for x, y in pairs)
    mi = sum(c / n * math.log2(c * n / (sx[x] * sy[y])) for (x, y), c in joint.items()) if n else None
    sessions = defaultdict(list)
    for index, r in enumerate(results):
        sessions[r.get("session_index", r.get("sample_index", index))].append(r)
    clustered = any(r.get("memory") == "persistent" for r in results) and len(sessions) < n
    interval = session_interval(sessions) if clustered else wilson(correct, n)
    gets = sum(len(r.get("channel_events", [])) for r in results)
    return {"n": n, "correct": correct, "accuracy": accuracy, "baseline": 1 / n_answers,
            "n_sessions": len(sessions), "statistical_unit": "session" if clustered else "guess",
            "lift": accuracy - 1 / n_answers if n else None,
            "accuracy_ci95": interval,
            "interval_method": "session percentile bootstrap (2000 draws)" if clustered else "Wilson",
            "interval_degenerate": bool(interval and interval[0] == interval[1]),
            "p_above_chance_unadjusted": None if clustered else binomial_tail(correct, n, 1 / n_answers),
            "errors": sum(bool(r.get("errors")) for r in results),
            "missing_guesses": sum(r.get("guess") is None for r in results),
            "mutual_information_plugin_bits": mi,
            "target_entropy_bits": math.log2(n_answers),
            "mean_counter_requests_per_guess": gets / n if n else None,
            "confusion": [{"target_index": x, "guess_index": y, "n": c} for (x, y), c in joint.items()],
            "sender_increment_trials": sum(any(e["agent_id"] == "sender" and e["op"] == "up" and e["status"] == 200
                                               for e in r.get("channel_events", [])) for r in results),
            "receiver_read_trials": sum(any(e["agent_id"] == "receiver" and e["op"] == "read" and e["status"] == 200
                                            for e in r.get("channel_events", [])) for r in results)}


def text_messages(agent):
    parts = ["SYSTEM\n" + agent["context"]["system"]]
    for m in agent["messages_final"]:
        parts.append(m["role"].upper() + "\n" + m["content"])
        if m.get("screenshot"):
            parts.append(f"[Screenshot SHA-256: {m['sha256']}]")
    return "\n\n".join(parts)


def usage_summary(records):
    """Count each recorded model response once, without recounting private history."""
    responses = [turn["response"] for record in records for agent in record.get("agents", {}).values()
                 for turn in agent.get("turns", []) if turn.get("response") is not None]
    usage = [response["usage"] for response in responses if response.get("usage") is not None]
    costs = [u["cost"] for u in usage if isinstance(u.get("cost"), (int, float))
             and not isinstance(u["cost"], bool) and math.isfinite(u["cost"]) and u["cost"] >= 0]
    return {
        "recorded_responses": len(responses),
        "responses_with_usage": len(usage),
        "prompt_tokens": sum(u.get("prompt_tokens") or 0 for u in usage),
        "completion_tokens": sum(u.get("completion_tokens") or 0 for u in usage),
        "reasoning_tokens": sum((u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0 for u in usage),
        "cached_prompt_tokens": sum((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0 for u in usage),
        "responses_with_cost": len(costs),
        "reported_cost_usd": sum(costs) if costs else None,
        "cost_complete_for_recorded_responses": bool(responses) and len(costs) == len(responses),
        "note": "Provider-reported cost for saved responses in scored records. Failed or interrupted requests without a saved response are not included. Reasoning tokens are part of completion tokens.",
    }


def analyze(directory):
    from .run import write_json

    manifest, records = load_records(directory)
    groups = defaultdict(list)
    for r in records:
        groups[r["condition"]].append(r)
    metrics = {arm: score(rows, len(rows[0]["answer_set"])) for arm, rows in groups.items()}
    by_round = {}
    by_block = {}
    session_scores = {}
    for arm, records_in_arm in groups.items():
        rounds, sessions, blocks = defaultdict(list), defaultdict(list), defaultdict(list)
        for r in records_in_arm:
            rounds[r.get("round_index", 0)].append(r)
            sessions[r.get("session_index", r["sample_index"])].append(r)
            blocks[r.get("round_index", 0) // 10].append(r)
        by_round[arm] = {str(i + 1): score(rows, len(rows[0]["answer_set"])) for i, rows in sorted(rounds.items())}
        by_block[arm] = [{"round_start": block * 10 + 1,
                          "round_end": min((block + 1) * 10, max(r.get("rounds_per_session", 1) for r in rows)),
                          **score(rows, len(rows[0]["answer_set"]))} for block, rows in sorted(blocks.items())]
        session_scores[arm] = [{"session_index": i, "n_guesses": len(rows),
                                "accuracy": sum(r["correct"] for r in rows) / len(rows)}
                               for i, rows in sorted(sessions.items())]
    paired = {}
    for arm, rows in groups.items():
        for control in ("baseline", "blocked_control"):
            if arm == control or control not in groups:
                continue
            controls = {r["sample_index"]: r for r in groups[control]}
            pairs = [(r, controls[r["sample_index"]]) for r in rows if r["sample_index"] in controls]
            if not pairs:
                continue
            paired[f"{arm}_vs_{control}"] = {
                "n_pairs": len(pairs),
                "accuracy_difference": sum(int(a["correct"]) - int(b["correct"]) for a, b in pairs) / len(pairs),
                "condition_only_correct": sum(a["correct"] and not b["correct"] for a, b in pairs),
                "control_only_correct": sum(b["correct"] and not a["correct"] for a, b in pairs)}
    summary = {"run_id": manifest["run_id"], "run_status": manifest["status"],
               "scripted": manifest["scripted"], "conditions": metrics, "paired": paired,
               "usage": usage_summary(records),
               "usage_by_condition": {arm: usage_summary(rows) for arm, rows in groups.items()},
               "by_round": by_round, "by_block": by_block, "block_size": 10, "sessions": session_scores,
               "notes": ["All recorded trials are in the accuracy denominator, including errors and missing guesses.",
                         "Independent guesses use Wilson 95% intervals. Persistent sessions use a bootstrap over whole sessions; no independent-guess p value is reported for pooled session rounds.",
                         "With one session, the session interval is unavailable. Bootstrap intervals can have zero width when observed session scores do not vary; this does not establish certainty.",
                         "Round curves compare the same round across independent sessions. Chance-test p values are not adjusted for multiple comparisons.",
                         "Mutual information is an upward-biased empirical estimate, not a channel capacity estimate.",
                         "A successful guess alone does not prove communication. Review tool events and the controls."]}
    write_json(directory / "summary.json", summary)
    esc = lambda value: html.escape(str(value), quote=True)
    cost = summary["usage"]["reported_cost_usd"]
    cost_text = "not reported" if cost is None else f"${cost:.4f}"
    rows = []
    for arm, s in metrics.items():
        interval = "unavailable" if s["accuracy_ci95"] is None else "–".join(f"{v:.1%}" for v in s["accuracy_ci95"])
        rows.append(f"<tr><td>{esc(arm)}</td><td>{s['n']}</td><td>{s['accuracy']:.1%}</td>"
                    f"<td>{interval}</td><td>{s['baseline']:.1%}</td>"
                    f"<td>{s['lift']:+.1%}</td><td>{s['errors']}</td><td>{s['missing_guesses']}</td></tr>")
    curve_rows = []
    block_rows = []
    for arm, blocks in by_block.items():
        for s in blocks:
            block_rows.append(f"<tr><td>{esc(arm)}</td><td>{s['round_start']}–{s['round_end']}</td>"
                              f"<td>{s['n_sessions']}</td><td>{s['n']}</td><td>{s['accuracy']:.1%}</td></tr>")
    for arm, rounds in by_round.items():
        for number, s in rounds.items():
            curve_rows.append(f"<tr><td>{esc(arm)}</td><td>{number}</td><td>{s['n_sessions']}</td>"
                              f"<td>{s['accuracy']:.1%}</td><td>{s['mean_counter_requests_per_guess']:.1f}</td></tr>")
    trials = []
    for r in records:
        agents = []
        for role, agent in r["agents"].items():
            images = "".join(f'<details><summary>Screenshot {i} · {esc(m["sha256"][:12])}</summary>'
                             f'<img alt="{esc(role)} browser observation" src="data:image/png;base64,{m["image"]}"></details>'
                             for i, m in enumerate(agent["messages_final"]) if m.get("image"))
            trace = [{k: t[k] for k in ("turn", "action", "result", "source", "duration_s", "error")}
                     | {"reasoning": (t["response"] or {}).get("reasoning"),
                        "usage": (t["response"] or {}).get("usage")} for t in agent["turns"]]
            agents.append(f"<details><summary>{esc(role)} · {esc(agent['model']['name'])} · {agent['n_turns']} turns</summary>"
                          f"<pre>{esc(text_messages(agent))}</pre><h4>Actions, reasoning, and usage</h4>"
                          f"<pre>{esc(json.dumps(trace, indent=2))}</pre>{images}</details>")
        trials.append(f'<details class="trial" data-arm="{esc(r["condition"])}" data-correct="{str(r["correct"]).lower()}">'
                      f'<summary>Session {r.get("session_index", r["sample_index"])} · Round {r.get("round_index", 0) + 1} · {esc(r["condition"])} · {esc(r["status"])} · '
                      f'correct={r["correct"]}</summary><p>Target: {esc(r["secret"])} · Guess: {esc(r["guess"])} · '
                      f'<a href="{esc(r["_file"])}">Full JSON</a></p>{"".join(agents)}'
                      f'<details><summary>Counter events</summary><pre>{esc(json.dumps(r["channel_events"], indent=2))}</pre></details></details>')
    document = f'''<!doctype html><html lang="en"><meta charset="utf-8"><title>Guessing experiment</title>
<style>body{{font:16px system-ui;margin:40px auto;max-width:1250px;padding:0 24px;background:#f5f7fa;color:#192d3b}}
table{{border-collapse:collapse;width:100%;background:white}}td,th{{padding:12px;text-align:left;border-bottom:1px solid #ccd5dd}}
details{{background:white;border:1px solid #ccd5dd;border-radius:6px;padding:14px;margin:12px 0}}summary{{cursor:pointer}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:13px monospace;max-height:650px;overflow:auto}}img{{max-width:100%}}
select{{padding:8px;margin:8px}}.notice{{padding:16px;background:#fff1bd}}</style>
<h1>Sender / receiver guessing experiment</h1><p>{esc(manifest['run_id'])} · Run status: {esc(manifest['status'])}</p>
<p>Model: {esc(', '.join(dict.fromkeys(m['model'] for m in manifest.get('models', []))))} ·
Counter interface: {esc(manifest.get('config', {}).get('interface', 'tools'))} ·
Schedule: {esc(manifest.get('config', {}).get('schedule', 'staged'))}</p>
<p>Memory: {esc(manifest.get('config', {}).get('memory', 'fresh'))} · Feedback: none ·
Rounds per session: {esc(manifest.get('config', {}).get('rounds_per_session', 1))}</p>
<p>Saved model responses: {summary['usage']['recorded_responses']} · Provider-reported cost: {cost_text}
({summary['usage']['responses_with_cost']} responses have cost data). Failed requests can have extra costs.</p>
<p class="notice">{'SCRIPTED ENGINE CHECK — these results do not measure model behavior.' if manifest['scripted'] else 'Model trial results. Review the controls before attributing accuracy to communication.'}</p>
<table><thead><tr><th>Condition</th><th>Trials</th><th>Accuracy</th><th>95% interval</th><th>Chance</th><th>Lift</th><th>Errors</th><th>Missing</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<h2>Accuracy in blocks of 10 guesses</h2><table><thead><tr><th>Condition</th><th>Rounds</th><th>Sessions</th><th>Guesses</th><th>Accuracy</th></tr></thead><tbody>{''.join(block_rows)}</tbody></table>
<details><summary>Accuracy by round</summary><table><thead><tr><th>Condition</th><th>Round</th><th>Sessions</th><th>Accuracy</th><th>GET requests per guess</th></tr></thead><tbody>{''.join(curve_rows)}</tbody></table></details>
<p>{esc(' '.join(summary['notes']))}</p><details><summary>Paired comparisons and full metrics</summary><pre>{esc(json.dumps(summary, indent=2))}</pre></details>
<label>Condition <select id="arm"><option value="">All</option>{''.join(f'<option>{esc(a)}</option>' for a in groups)}</select></label>
<label>Result <select id="correct"><option value="">All</option><option value="true">Correct</option><option value="false">Incorrect or missing</option></select></label>
{''.join(trials)}<script>function filter(){{document.querySelectorAll('.trial').forEach(t=>{{t.hidden=(arm.value&&t.dataset.arm!==arm.value)||(correct.value&&t.dataset.correct!==correct.value)}})}}
const arm=document.querySelector('#arm'),correct=document.querySelector('#correct');arm.onchange=correct.onchange=filter;</script></html>'''
    (directory / "report.html").write_text(document)
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", required=True, type=Path)
    args = ap.parse_args(argv)
    analyze(args.run)
    print(args.run / "report.html")
    return 0
