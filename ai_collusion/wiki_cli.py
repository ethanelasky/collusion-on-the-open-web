"""ai-collusion-wiki: replay a collusion.wiki revision as a model turn.

  ai-collusion-wiki fetch  [--dump data/collusion-wiki]
  ai-collusion-wiki show   --page dse/DataUSAStateSequenceCollab2027 --rev 4 [--spec ...]
  ai-collusion-wiki agent  --label ParallelSectorAgentApr2            # list an agent's edits
  ai-collusion-wiki run    --page ... --rev 4 10 --variant base notable -n 3 --only kimi-k2-openrouter
  ai-collusion-wiki play   --page ... --rev 4 --variant notable --env-model kimi-k2-openrouter --mode neutral broken
  ai-collusion-wiki preview --page ... --rev 4 --variant base notable --mode neutral broken --docent "prompt preview"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .context import format_context
from .environment_modes import SUPPORTED_MODES as MODES
from .arms import HINTS
from .wiki import Dump, fetch_dump, load_spec, reconstruct, reference_for
from .preparation import prepare_replay_context

DEFAULT_DUMP = "data/collusion-wiki"
DEFAULT_SPEC = "wikitasks/sector61_state.yaml"


def _page_id(s: str) -> str:
    """Accept 'dse/Name', 'dse~Name', or a bare 'Name' (assumes dse)."""
    s = s.strip().removesuffix(".html")
    if "/" in s:
        return s
    if "~" in s:
        return s.replace("~", "/", 1)
    return f"dse/{s}"


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="ai-collusion-wiki", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dump", default=DEFAULT_DUMP, help=f"expanded dump directory (default: {DEFAULT_DUMP})")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("fetch", help="download + expand + checksum the dump")

    for name in ("show", "run", "play"):
        helps = {"show": "print the assembled context", "run": "run models on the cut(s), one turn each",
                 "play": "multi-turn episodes: models keep acting after the cut against a simulated environment"}
        sp = sub.add_parser(name, help=helps[name])
        sp.add_argument("--page", required=True, help="page id, e.g. dse/DataUSAStateSequenceCollab2027")
        sp.add_argument("--rev", type=int, nargs="+", required=True, help="revision seq(s) on that page; each becomes a condition")
        sp.add_argument("--spec", default=DEFAULT_SPEC, help="task-family spec YAML")
        sp.add_argument("--variant", nargs="+", default=None, help="cut variants to run (default: base); 'base' = the cut entry itself, others come from its variants: map")
        sp.add_argument("--hint", choices=HINTS, default=None, help="planted discovery condition (default: none)")
        if name == "show":
            sp.add_argument("--json", action="store_true", help="emit {system, messages, reference} as JSON")
        else:
            sp.add_argument("--models", default="models.yaml")
            sp.add_argument("--only", nargs="+", default=None)
            sp.add_argument("-n", "--samples", type=int, default=1)
            sp.add_argument("--seed", type=int, default=0)
            sp.add_argument("--workers", type=int, default=1, help="concurrent episodes per evaluated model (play only)")
            sp.add_argument("--temperature", type=float, default=None)
            sp.add_argument("--out", default="runs")
            sp.add_argument("--run-id", default=None)
        if name == "play":
            sp.add_argument("--agents", type=int, default=1, help="agents sharing each sample's wiki (default: 1)")
            sp.add_argument("--isolated-wiki", action="store_true", help="keep each agent's wiki edits separate")
            sp.add_argument("--html", action="store_true", help="read-only view of live wiki state")
            sp.add_argument("--wiki-host", default="127.0.0.1", help="exact private IPv4 bind address")
            sp.add_argument("--wiki-port", type=int, default=0, help="observer port; 0 selects a free port")
            sp.add_argument("--env-model", required=True, help="models.yaml entry that simulates the environment")
            sp.add_argument("--mode", nargs="+", default=None, choices=MODES,
                            help="environment mode(s); each becomes a condition")
            sp.add_argument("--max-turns", type=int, default=None, help="override the spec's episode.max_turns")
            sp.add_argument("--arms", metavar="FILE", help="YAML file selecting named existing conditions")
            sp.add_argument("--arm", nargs="+", action="extend", metavar="ID", help="select arm IDs from --arms (default: all)")

    pv = sub.add_parser("preview", help="what each model receives: evaluee context per variant, env-model prompts per mode; print or push to Docent")
    pv.add_argument("--page", required=True)
    pv.add_argument("--rev", type=int, nargs="+", required=True)
    pv.add_argument("--spec", default=DEFAULT_SPEC)
    pv.add_argument("--variant", nargs="+", default=None)
    pv.add_argument("--hint", choices=HINTS, default=None, help="planted discovery condition (default: none)")
    pv.add_argument("--mode", nargs="+", default=None, choices=MODES)
    pv.add_argument("--arms", metavar="FILE", help="YAML file selecting named existing conditions")
    pv.add_argument("--arm", nargs="+", action="extend", metavar="ID", help="select arm IDs from --arms (default: all)")
    pv.add_argument("--max-turns", type=int, default=None, help="fallback live-turn cap; an explicit arm cap takes precedence")
    pv.add_argument("--json", action="store_true", help="emit exact preview records as a JSON array")
    pv.add_argument("--sample-call", nargs="+", default=[
        'shell("curl -s \'https://api.datausa.io/tesseract/data.jsonrecords?cube=pums_5&drilldowns=State%2CYear&include=Industry%20Sector%3A61-62%3BWorkforce%20Status%3Atrue%3BState%3AWest%20Virginia&locale=en&measures=Total%20Population\'")',
    ], help="tool call line(s) used to render the env model's per-turn prompt")
    pv.add_argument("--docent", metavar="COLLECTION", default=None, help="upload to this Docent collection (created if missing) instead of printing")
    pv.add_argument("--replace", action="store_true", help="with --docent: clear the collection first")

    ag = sub.add_parser("agent", help="list every edit by an agent label, with the text it added")
    ag.add_argument("--label", required=True)
    ag.add_argument("--full", action="store_true", help="also print the page text it saw before each edit")

    args = p.parse_args(argv)

    arms = None
    if args.cmd in ("play", "preview"):
        if args.arm and not args.arms:
            p.error("--arm requires --arms FILE")
        if args.arms:
            if args.variant is not None or args.mode is not None or args.hint is not None:
                p.error("--arms cannot be combined with explicit --variant, --mode, or --hint")
            from .arms import load_arms
            import yaml

            try:
                arms = load_arms(args.arms, only=args.arm)
            except (ValueError, OSError, yaml.YAMLError) as exc:
                p.error(str(exc))
        args.mode = args.mode or (["neutral", "broken"] if args.cmd == "preview" else ["neutral"])
        if args.cmd == "preview" and args.json and args.docent:
            p.error("--json cannot be combined with --docent")
    if hasattr(args, "hint"):
        args.hint = args.hint or "none"
    if hasattr(args, "variant"):
        args.variant = args.variant or ["base"]

    if args.cmd == "fetch":
        fetch_dump(args.dump)
        return

    if not Path(args.dump, "revisions.jsonl").exists():
        raise SystemExit(f"no dump at {args.dump}; run: ai-collusion-wiki fetch --dump {args.dump}")
    dump = Dump(args.dump)

    if args.cmd == "agent":
        revs = dump.by_label.get(args.label)
        if not revs:
            raise SystemExit(f"no edits by label {args.label!r}")
        print(f"{args.label}: {len(revs)} edits on {len({r['page_id'] for r in revs})} pages")
        for r in revs:
            print(f"\n--- {r['time']}  {r['page_id']}@{r['seq']}  ip={r['ip16']} ---")
            if args.full:
                print("[saw]\n" + (dump.body_before(r["page_id"], r["seq"]) or "(new page)"))
                print("[wrote]")
            print(dump.added_text(r))
        return

    page_id = _page_id(args.page)
    spec = load_spec(args.spec)
    cuts = {seq: reconstruct(dump, page_id, seq) for seq in args.rev}

    if arms is not None:
        from .arms import validate_arms

        try:
            validate_arms(arms, spec, cuts)
        except ValueError as exc:
            p.error(str(exc))

    variants = [None if v == "base" else v for v in args.variant]

    if args.cmd == "preview":
        from .preview import build_previews, print_previews, upload_previews

        previews = build_previews(spec, dump, cuts, variants, args.mode, args.sample_call,
                                  arms=arms, max_turns=args.max_turns, hint=args.hint)
        if args.docent:
            upload_previews(previews, args.docent, replace=args.replace)
        elif args.json:
            print(json.dumps(previews, indent=2, ensure_ascii=False))
        else:
            print_previews(previews)
        return

    def cond_name(cut, seq, variant):
        return f"{cut.page_name}@{seq}" + (f":{variant}" if variant else "")

    if args.cmd == "show":
        for seq, cut in cuts.items():
            for variant in variants:
                ctx = prepare_replay_context(spec, dump, cut, variant, hint=args.hint)
                ref = reference_for(cut)
                name = cond_name(cut, seq, variant)
                if args.json:
                    print(json.dumps({"cut": f"{page_id}@{seq}", "variant": variant, "agent": cut.label, **ctx, "reference": ref}, indent=2, ensure_ascii=False))
                    continue
                print(f"# condition {name}  agent={cut.label}  time={cut.time}  spec={spec.id}")
                print(f"# cut-specific config: {'yes' if f'{page_id}@{seq}' in spec.cuts else 'NO (default status; add an entry under cuts: in the spec)'}\n")
                print(format_context(ctx))
                print("\n" + "=" * 78 + "\nREFERENCE (what the real agent did)\n" + "=" * 78)
                print(json.dumps(ref, indent=2, ensure_ascii=False))
        return

    from .runner import load_models, run_contexts

    if args.cmd == "play":
        from .episode import run_episodes

        env_model = load_models(args.models, [args.env_model])[0]
        models = [m for m in load_models(args.models, args.only) if m.name != args.env_model or args.only]
        run_episodes(
            models=models, env_model=env_model, spec=spec, dump=dump, cuts=cuts, variants=variants,
            modes=args.mode, n_samples=args.samples, out_dir=args.out, base_seed=args.seed,
            temperature=args.temperature, max_turns=args.max_turns, run_id=args.run_id, workers=args.workers,
            arms=arms, hint=args.hint,
            agents=args.agents, shared_wiki=not args.isolated_wiki, html=args.html,
            wiki_host=args.wiki_host, wiki_port=args.wiki_port,
            manifest_extra={"spec": args.spec, "dump": args.dump, "page": page_id,
                            "variants": list(dict.fromkeys(a.variant or "base" for a in arms)) if arms is not None else args.variant,
                            "agents": {seq: c.label for seq, c in cuts.items()}},
        )
        return

    contexts = {cond_name(cut, seq, v): prepare_replay_context(spec, dump, cut, v, hint=args.hint) for seq, cut in cuts.items() for v in variants}
    labels = sorted({c.label for c in cuts.values()})
    if len(labels) > 1:
        print(f"note: cuts span different agents {labels}; each condition uses its own", file=sys.stderr)
    run_contexts(
        models=load_models(args.models, args.only),
        contexts=contexts,
        task_id=f"wiki:{spec.id}",
        reference={cond_name(cut, seq, v): reference_for(cut) for seq, cut in cuts.items() for v in variants},
        reference_per_condition=True,
        manifest_extra={"spec": args.spec, "dump": args.dump, "page": page_id, "variants": args.variant, "hint": args.hint,
                        "agents": {seq: c.label for seq, c in cuts.items()}},
        n_samples=args.samples, out_dir=args.out, base_seed=args.seed, temperature=args.temperature, run_id=args.run_id,
    )


if __name__ == "__main__":
    main()
