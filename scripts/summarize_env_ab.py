#!/usr/bin/env python3
"""Simulator-quality and behaviour summary for environment-model A/B runs.

Groups saved episodes by the environment model that simulated them and reports, per group:
condition violations / env errors, side-channel coverage (@@ELAPSED parsed, wiki saves),
env-call volume and token usage (with an optional $/M price table), and the evaluee-side
outcomes (end reasons, rounds, board reads, posts, requests).

  python scripts/summarize_env_ab.py runs/search2x2-100-20260909-gpt-5.6 runs/envab-*-gpt-5.6 \
      --arm empty-success-write-off --model gpt-5.6 [--prices prices.json] [--json out.json]
"""
from __future__ import annotations
import argparse, glob, json, statistics
from collections import Counter, defaultdict
from pathlib import Path

from ai_collusion.wiki_metrics import wiki_activity


def load_episodes(run_dirs, arm=None, model=None):
    for d in run_dirs:
        for f in sorted(glob.glob(str(Path(d) / "*__*.json"))):
            rec = json.load(open(f))
            if arm and rec.get("arm_id") != arm:
                continue
            m = rec.get("model"); m = m.get("name") if isinstance(m, dict) else m
            if model and m != model:
                continue
            rec["_file"] = f
            yield rec


def summarize(records):
    groups = defaultdict(list)
    for r in records:
        ep = r.get("episode") or {}
        groups[ep.get("env_model") or "?"].append(r)
    out = {}
    for env, recs in sorted(groups.items()):
        s = Counter(); lens = []; charged = []; turns = []; dur = []
        ev = Counter(); en = Counter()
        for r in recs:
            ep = r["episode"]; s["episodes"] += 1
            activity = wiki_activity(r)
            s["board_reads"] += len(activity["read_turns"])
            s["board_writes"] += len(activity["write_turns"])
            s["wiki_saves"] += activity["shell_saves"]
            s["wiki_posts"] += activity["posts"]
            s[f"end:{ep.get('end_reason')}"] += 1
            turns.append(ep.get("n_turns", len(ep.get("turns", [])))); dur.append(r.get("duration_s", 0))
            err = r.get("error") or {}
            if err:
                s["episode_errors"] += 1
                if "condition_violation" in json.dumps(err): s["condition_violations"] += 1
            for t in ep.get("turns", []):
                u = (t.get("response") or {}).get("usage") or {}
                ev["prompt"] += u.get("prompt_tokens", 0); ev["completion"] += u.get("completion_tokens", 0)
                ev["cached"] += (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
                for n in t.get("notices") or []:
                    if "condition_violation" in json.dumps(n): s["violation_notices"] += 1
                ec = t.get("env_call")
                if not ec: continue
                s["env_calls"] += 1
                if ec.get("error"): s["env_call_errors"] += 1
                eu = ec.get("usage") or {}
                en["prompt"] += eu.get("prompt_tokens", 0); en["completion"] += eu.get("completion_tokens", 0)
                en["cached"] += (eu.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
                lens.append(len(ec.get("response") or ""))
                el = t.get("elapsed")
                if el and el.get("reported_s") is not None:
                    s["elapsed_reported"] += 1; charged.append(el.get("charged_s") or 0)
            rounds = ep.get("rounds") or []
            s["rounds"] += len(rounds); s["rounds_correct"] += sum(1 for x in rounds if x.get("correct"))
            s["rounds_missed"] += sum(1 for x in rounds if x.get("missed"))
            s["requests"] += len(ep.get("requests") or [])
            s["episodes_with_post"] += bool(ep.get("wiki_posts"))
        n = s["episodes"]; calls = s["env_calls"] or 1
        out[env] = {
            "episodes": n, "end_reasons": {k[4:]: v for k, v in s.items() if k.startswith("end:")},
            "episode_errors": s["episode_errors"], "condition_violations": s["condition_violations"],
            "violation_notices": s["violation_notices"], "env_call_errors": s["env_call_errors"],
            "mean_turns": statistics.mean(turns) if turns else 0, "mean_duration_min": statistics.mean(dur) / 60 if dur else 0,
            "env_calls_per_ep": s["env_calls"] / n, "elapsed_parsed_rate": s["elapsed_reported"] / calls,
            "charged_s_median": statistics.median(charged) if charged else None,
            "charged_s_max": max(charged) if charged else None,
            "wiki_saves": s["wiki_saves"], "env_result_chars_mean": statistics.mean(lens) if lens else 0,
            "env_tokens_per_ep": {k: v / n for k, v in en.items()}, "evaluee_tokens_per_ep": {k: v / n for k, v in ev.items()},
            "rounds": s["rounds"], "rounds_correct": s["rounds_correct"], "rounds_missed": s["rounds_missed"],
            "board_reads_per_ep": s["board_reads"] / n, "board_writes_per_ep": s["board_writes"] / n,
            "wiki_posts": s["wiki_posts"], "episodes_with_post": s["episodes_with_post"], "requests": s["requests"],
            "files": [r["_file"] for r in recs],
        }
    return out


def add_costs(summary, prices):
    for env, s in summary.items():
        p = prices.get(env)
        if not p: continue
        t = s["env_tokens_per_ep"]; unc = t.get("prompt", 0) - t.get("cached", 0)
        s["env_cost_per_ep_usd"] = (unc * p["prompt"] + t.get("cached", 0) * p.get("cached", p["prompt"]) + t.get("completion", 0) * p["completion"]) / 1e6


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+"); ap.add_argument("--arm"); ap.add_argument("--model")
    ap.add_argument("--prices", help="JSON {env_model: {prompt, completion, cached}} in $/M tokens")
    ap.add_argument("--json", help="write full summary here")
    a = ap.parse_args()
    summary = summarize(load_episodes(a.runs, a.arm, a.model))
    if a.prices: add_costs(summary, json.load(open(a.prices)))
    cols = ["episodes", "condition_violations", "env_call_errors", "env_calls_per_ep", "elapsed_parsed_rate",
            "charged_s_median", "wiki_saves", "env_result_chars_mean", "mean_turns", "rounds_correct", "rounds_missed",
            "board_reads_per_ep", "wiki_posts", "episodes_with_post", "requests", "env_cost_per_ep_usd"]
    print("metric".ljust(24) + "".join(e.rjust(18) for e in summary))
    for c in cols:
        row = []
        for e in summary:
            v = summary[e].get(c)
            row.append(("-" if v is None else f"{v:.2f}" if isinstance(v, float) else str(v)).rjust(18))
        print(c.ljust(24) + "".join(row))
    for e in summary: print(f"{e}: end_reasons={summary[e]['end_reasons']}")
    if a.json:
        json.dump(summary, open(a.json, "w"), indent=2)


if __name__ == "__main__":
    main()
