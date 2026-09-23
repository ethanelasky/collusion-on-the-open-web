"""ai-collusion-docent: upload a run directory's transcripts to Docent.

  ai-collusion-docent --run runs/frontier-rev4 --name "ai-collusion wiki replay" [--dry-run]
  ai-collusion-docent --run runs/a runs/b --collection-id <id> [--replace]

One transcript JSON -> one Docent agent run holding one transcript: the system prompt, the
context messages sent (prefixed with [PREFILL] for display), and the model's reply (reasoning trace attached as a reasoning
block when the provider returned one). Model, condition, seed, usage, finish reason, error and
the reference (what the real agent did, for wiki cuts) go into run metadata for filtering.
Every target collection is shared publicly (read-only, anyone with the link) before upload.
Needs DOCENT_API_KEY in the environment (or in a .env next to pyproject.toml).
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import os
import sys
from pathlib import Path
from typing import Any

DOCENT_DASHBOARD_URL = "https://docent.transluce.org/dashboard"
DEFAULT_BATCH_SIZE = 20


def load_run_dir(run_dir: Path) -> tuple[dict, list[dict]]:
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"{run_dir}: no manifest.json (not a run directory?)")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema") == "covert-channel/v1":
        from experiments.covert_channel.analysis import load_records
        return load_records(run_dir)
    records = []
    for p in sorted(run_dir.glob("*.json")):
        if p.name == "manifest.json":
            continue
        rec = json.loads(p.read_text())
        rec["_file"] = p.name
        records.append(rec)
    return manifest, records


def record_to_agent_run(rec: dict, manifest: dict, *, render_prefill: bool = True):
    if rec.get("schema") == "covert-channel/v1":
        from experiments.covert_channel.docent import record_to_agent_run as convert
        return convert(rec, manifest)
    from docent.data_models import AgentRun, Transcript
    from docent.data_models.chat import parse_chat_message
    from docent.data_models.chat.content import ContentReasoning, ContentText

    ctx = rec["context"]
    messages: list[Any] = [parse_chat_message({"role": "system", "content": ctx["system"],
                                               "metadata": {"prefill": False, "provenance": "system"}})]
    for m in ctx["messages"]:
        messages.append(parse_chat_message({**m, "metadata": {**(m.get("metadata") or {}),
                                                             "prefill": True, "provenance": "prefill"}}))

    resp = rec.get("response")
    model_name = rec["model"]["name"]
    episode = rec.get("episode")
    if episode:
        # New records save the exact prepared context. Older records need the installed
        # history from messages_final, before live assistant/result pairs were appended.
        final = rec.get("messages_final") or []
        n_ctx = max(0, len(final) - 2 * len(episode["turns"]))
        prefill_msgs = (ctx["messages"] if "live_start_message_index" in rec else
                        final[:n_ctx] if final else ctx["messages"])
        messages = [messages[0]]
        for m in prefill_msgs:
            messages.append(parse_chat_message({
                **m, "metadata": {**(m.get("metadata") or {}), "prefill": True, "provenance": "prefill"},
            }))
        for t in episode["turns"]:
            r = t["response"] or {}
            content: list[Any] = []
            if r.get("reasoning"):
                content.append(ContentReasoning(reasoning=r["reasoning"]))
            content.append(ContentText(text=r.get("text") or ""))
            messages.append(parse_chat_message({"role": "assistant", "content": content, "model": rec["model"]["model_id"],
                                                       "metadata": {"prefill": False, "provenance": "evaluated_model"}}))
            messages.append(parse_chat_message({"role": "user", "content": t["result"],
                                                       "metadata": {"prefill": False, "provenance": "environment",
                                                                    "source": t.get("source")}}))
        if rec.get("error"):
            err = rec["error"]
            messages.append(parse_chat_message({"role": "assistant", "content": f"[no response: {err.get('type')}: {err.get('message')}]",
                                                "model": rec["model"]["model_id"],
                                                "metadata": {"prefill": False, "provenance": "harness_error"}}))
    elif resp is not None:
        content: list[Any] = []
        if resp.get("reasoning"):
            content.append(ContentReasoning(reasoning=resp["reasoning"]))
        content.append(ContentText(text=resp.get("text") or ""))
        messages.append(parse_chat_message({"role": "assistant", "content": content, "model": rec["model"]["model_id"],
                                                       "metadata": {"prefill": False, "provenance": "evaluated_model"}}))
    else:
        err = rec.get("error") or {}
        messages.append(parse_chat_message({
            "role": "assistant",
            "content": f"[no response: {err.get('type')}: {err.get('message')}]",
            "model": rec["model"]["model_id"],
            "metadata": {"prefill": False, "provenance": "harness_error"},
        }))

    metadata = {
        "arm_id": rec.get("arm_id"), "arm": rec.get("arm"),
        "context_sha256": rec.get("context_sha256"), "experiment_sha256": rec.get("experiment_sha256"),
        "resolved_config": rec.get("resolved_config"),
        "live_start_message_index": rec.get("live_start_message_index"),
        "max_turns": rec.get("max_turns"), "num_live_problems": rec.get("num_live_problems"),
        "wiki_write_instructions": rec.get("wiki_write_instructions"),
        "hint": rec.get("hint", (rec.get("resolved_config") or {}).get("hint")),
        "context_provenance": ctx.get("provenance"),
        "model_config": rec.get("model_config"), "env_model_config": rec.get("env_model_config"),
        "agent_group": rec.get("group"),
        "run_id": rec.get("run_id"),
        "task_id": rec.get("task_id"),
        "condition": rec["condition"],
        # Explicit roles, so the evaluated model and the simulator are never confused in Docent filters.
        "model_under_evaluation": model_name,
        "evaluee": model_name,
        "evaluee_model_id": rec["model"]["model_id"],
        "environment_model": (episode or {}).get("env_model") or (rec.get("env_model_config") or {}).get("name"),
        "environment_model_id": (rec.get("env_model_config") or {}).get("model"),
        "model": model_name,
        "model_id": rec["model"]["model_id"],
        "transport": rec["model"]["transport"],
        "thinking": rec["model"].get("thinking"),
        "effort": rec["model"].get("effort"),
        "extra_body": rec["model"].get("extra_body"),
        "sample_index": rec.get("sample_index"),
        "seed": rec.get("seed"),
        "seed_applied": rec.get("seed_applied"),
        "temperature": rec.get("temperature"),
        "max_tokens": rec.get("max_tokens"),
        "timestamp": rec.get("timestamp"),
        "duration_s": rec.get("duration_s"),
        "finish_reason": (resp or {}).get("finish_reason"),
        "usage": (resp or {}).get("usage"),
        "error": rec.get("error") and {k: rec["error"][k] for k in ("type", "message")},
        "reference": rec.get("reference"),
        "episode": episode and {
            "mode": episode.get("mode"), "mode_label": episode.get("mode_label"),
            "shared_wiki_posts": episode.get("shared_wiki_posts"),
            "env_model": episode.get("env_model"), "n_turns": episode.get("n_turns"),
            "end_reason": episode.get("end_reason"), "rounds": episode.get("rounds"),
            "n_wiki_posts": sum(len(v) for v in (episode.get("wiki_posts") or {}).values()),
            "wiki_posts": episode.get("wiki_posts"), "n_env_calls": episode.get("n_env_calls"),
            "requests": episode.get("requests"),
            "request_served": (any(q["served"] for q in episode["requests"]) if episode.get("requests") else None),
            "sources": [t["source"] for t in episode.get("turns", [])],
            "charged_elapsed_s": sum((t.get("elapsed") or {}).get("charged_s") or 0 for t in episode.get("turns", [])) or None,
        },
        "source_file": rec["_file"],
        "spec": manifest.get("spec"),
        "page": manifest.get("page"),
        "agents": manifest.get("agents"),
    }
    metadata = {k: v for k, v in metadata.items() if v is not None}
    if episode:
        from .run_health import health

        metadata.update(health(rec))
    from .docent_presentation import display_label
    label = display_label(rec['condition'], (rec.get('resolved_config') or {}).get('cut'))
    name = f"{model_name} | {label} | n{rec.get('sample_index', 0):02d}"
    if rec.get("group"):
        name += f" | {rec['group']['agent_id']}"
    if episode:
        rr = " ".join(f"R{r['n']}:{'ok' if r['correct'] else ('miss' if r['missed'] else ('wrong' if r['answer'] else '-'))}"
                      for r in episode.get("rounds", []))
        name += f" | {episode.get('n_turns')}t {rr}"
        for q in episode.get("requests") or []:
            name += f" help:{'yes' if q['served'] else 'no'}"
    if rec.get("error"):
        name += " [ERROR]"
    from .docent_prefill import prefill_metadata, render_prefill_message

    # Source metadata cannot assert that this new display has already rendered.
    for message in messages:
        if (message.metadata or {}).get("prefill") is True:
            message.metadata.pop("prefill_rendering", None)
    if render_prefill:
        messages = [render_prefill_message(message) for message in messages]
    run = AgentRun(name=name, transcripts=[Transcript(messages=messages)], metadata=metadata)
    run.metadata.update(prefill_metadata(run))
    if episode:
        from .docent_presentation import presentation

        run.name, fields, _ = presentation(run)
        run.description = fields["summary"]
        run.metadata.update(fields)
    return run


def make_client():
    from .runner import load_repo_env

    load_repo_env()
    key = os.getenv("DOCENT_API_KEY")
    from docent import Docent
    return Docent(api_key=key) if key else Docent()


def ensure_public(client, collection_id: str) -> bool:
    """Share the collection read-only with anyone holding the link.

    Repo policy: every Docent upload is public. Sharing needs admin permission on the
    collection, so on an existing collection we do not own this warns instead of aborting
    the upload. Returns True when the collection is public.
    """
    try:
        client.share_collection_with_public(collection_id, permission="read")
    except Exception as exc:  # noqa: BLE001 - report and keep the upload
        print(f"warning: could not make collection {collection_id} public: {exc}", file=sys.stderr)
        return False
    return True


def resolve_collection_id(client, name: str) -> str:
    matching = [c for c in client.list_collections() if c["name"] == name]
    if not matching:
        return client.create_collection(name=name, description="")
    if len(matching) == 1:
        return matching[0]["id"]
    raise SystemExit(f"multiple collections named {name!r}; pass --collection-id")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ai-collusion-docent", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run", nargs="+", required=True, help="run directory(ies) under runs/")
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--name", help="Docent collection name to create or append to")
    target.add_argument("--collection-id", help="existing Docent collection id")
    p.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    p.add_argument("--group-by", choices=["trial", "session"], default="trial",
                   help="Use session for complete persistent counter sessions with both private histories")
    p.add_argument("--replace", action="store_true", help="delete every existing run in the collection first")
    p.add_argument("--dry-run", action="store_true", help="build everything, print a line per run, no network")
    args = p.parse_args(argv)
    if args.batch_size < 1:
        p.error("--batch-size must be positive")

    runs = []
    for d in args.run:
        run_dir = Path(d)
        manifest, records = load_run_dir(run_dir)
        if args.group_by == "session":
            if manifest.get("schema") != "covert-channel/v1":
                p.error("--group-by session requires a covert-channel run")
            from experiments.covert_channel.docent import session_to_agent_run

            sessions = defaultdict(list)
            for rec in records:
                sessions[(rec["condition"], rec["session_index"])].append(rec)
            runs.extend(session_to_agent_run(rows, manifest) for _, rows in sorted(sessions.items()))
        else:
            for rec in records:
                runs.append(record_to_agent_run(rec, manifest))
        print(f"{run_dir}: {len(records)} transcript(s)", file=sys.stderr)
    for r in runs:
        print(f"  {r.name}  transcripts={len(r.transcripts)} msgs={sum(len(t.messages) for t in r.transcripts)}  finish={r.metadata.get('finish_reason')}", file=sys.stderr)
    if args.dry_run:
        print(f"dry run: {len(runs)} run(s) built, nothing uploaded", file=sys.stderr)
        return 0

    client = make_client()
    collection_id = args.collection_id or resolve_collection_id(client, args.name)
    public = ensure_public(client, collection_id)
    if args.replace:
        existing = client.list_agent_run_ids(collection_id)
        if existing:
            client.delete_agent_runs(collection_id, existing)
            print(f"cleared {len(existing)} existing run(s)", file=sys.stderr)
    for start in range(0, len(runs), args.batch_size):
        chunk = runs[start:start + args.batch_size]
        client.add_agent_runs(collection_id, chunk)
        print(f"uploaded {start + len(chunk)}/{len(runs)}", file=sys.stderr)
    from .docent_prefill import annotate_prefills

    annotated = annotate_prefills(client, collection_id, agent_run_ids=[run.id for run in runs])
    print(f"verified prefill annotations for {len(annotated)} run(s)", file=sys.stderr)
    from .docent_presentation import annotate_presentation

    annotate_presentation(client, collection_id, [run.id for run in runs])
    if args.name:
        print(f"collection: {args.name}")
    print(f"id: {collection_id}")
    print(f"url: {DOCENT_DASHBOARD_URL}/{collection_id}")
    print(f"access: {'public (anyone with the link can read)' if public else 'NOT public; sharing failed'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
