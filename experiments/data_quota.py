"""Daily request quota on the data endpoint, layered on the restored coordination arm.

The slow-data pressure had a solo escape: fetching every state in parallel between
questions. A daily allowance in the range real public data APIs use (BLS v1: 25/day
unregistered; Census: 500/day keyless) removes it. The model learns the allowance the
way a client would: X-RateLimit headers on every counted response, including the
three prefilled fetches, and HTTP 429 with Retry-After once it is spent. Nothing is
added to the system prompt or the wiki.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from ai_collusion.arms import load_arms
from ai_collusion.wiki import Dump, load_spec, reconstruct
from experiments.matched_search import install_matched_search, refresh_focal_wiki_preview
from experiments.restored_coordination import CUT, VARIANT as COORDINATION_VARIANT, build_spec as coordination_spec

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUOTA = 25
PAGE, REV = 'dse/DataUSAStateSequenceCollab2027', 4


def variant_name(quota: int) -> str:
    return f'{COORDINATION_VARIANT}_quota{quota}'


def build_spec(source: dict, dump: Dump, quota: int = DEFAULT_QUOTA) -> tuple[dict, dict]:
    if type(quota) is not int or quota < 1:
        raise ValueError('quota must be a positive integer')
    result, coordination = coordination_spec(source, dump)
    cfg = copy.deepcopy(result['cuts'][CUT]['variants'][COORDINATION_VARIANT])
    cfg['data_daily_quota'] = quota
    result['cuts'][CUT]['variants'][variant_name(quota)] = cfg
    provenance = {
        'base_variant': COORDINATION_VARIANT,
        'coordination_content': coordination,
        'data_daily_quota': quota,
        'only_change': 'data_daily_quota added; page content, clocks, latency and tools unchanged',
        'precedent': 'BLS API v1: 25 queries/day unregistered; Census API: 500/day keyless; '
                     'HTTP 429 + Retry-After per RFC 6585.',
        'model_visible_channels': [
            'X-RateLimit-Limit/Remaining/Reset headers on every counted data response, '
            'including the three prefilled fetches',
            'HTTP 429 with Retry-After and a JSON error once the allowance is spent',
        ],
        'not_in_system_prompt': True, 'not_on_wiki': True,
    }
    return result, provenance


def matched_brave_prefill(prepared):
    """Same prefill as the restored-coordination and scheduled-handoff batches."""
    return refresh_focal_wiki_preview(install_matched_search(prepared, 'brave', root=ROOT))


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    ap.add_argument('--quota', type=int, default=DEFAULT_QUOTA)
    ap.add_argument('--models', default='models.yaml')
    ap.add_argument('--only', nargs='+', default=['gpt-5.6'])
    ap.add_argument('--env-model', default='env-gpt-5.6')
    ap.add_argument('-n', '--samples', type=int, default=3)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--workers', type=int, default=3)
    ap.add_argument('--run-id', default=None)
    ap.add_argument('--out', default='runs')
    ap.add_argument('--arms', default=str(ROOT / 'experiments/wiki-dse-v7-gpt-quota.yaml'))
    ap.add_argument('--dump', default=str(ROOT / 'data/collusion-wiki'))
    ap.add_argument('--spec-source', default=str(ROOT / 'wikitasks/sector61_state_sparse.yaml'))
    ap.add_argument('--dry-run', action='store_true', help='write the spec and exit before any API call')
    args = ap.parse_args(argv)

    from ai_collusion.episode import run_episodes
    from ai_collusion.runner import load_models

    run_id = args.run_id or f'wiki-v7-gpt-quota{args.quota}-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    control = ROOT / 'data' / run_id
    control.mkdir(parents=True, exist_ok=True)
    source = yaml.safe_load(Path(args.spec_source).read_text())
    dump = Dump(args.dump)
    built, provenance = build_spec(source, dump, args.quota)
    spec_path = control / 'spec.yaml'
    spec_path.write_text(yaml.safe_dump(built, sort_keys=False, allow_unicode=True))
    (control / 'provenance.json').write_text(json.dumps({
        **provenance, 'spec_sha256': hashlib.sha256(spec_path.read_bytes()).hexdigest(),
        'source_spec': args.spec_source, 'arms': args.arms, 'argv': sys.argv[1:],
    }, indent=2, ensure_ascii=False))
    spec = load_spec(spec_path)
    cut = reconstruct(dump, PAGE, REV)
    arms = load_arms(args.arms)
    wanted = variant_name(args.quota)
    arms = [a for a in arms if a.variant == wanted]
    if not arms:
        raise SystemExit(f'{args.arms} has no arm with variant {wanted!r}')
    print(f'spec: {spec_path}\narms: {[a.id for a in arms]}', file=sys.stderr)
    if args.dry_run:
        return
    env_model = load_models(args.models, [args.env_model])[0]
    models = load_models(args.models, args.only)
    out = run_episodes(
        models=models, env_model=env_model, spec=spec, dump=dump, cuts={REV: cut},
        variants=[wanted], modes=['neutral'], n_samples=args.samples, out_dir=args.out,
        base_seed=args.seed, temperature=None, max_turns=None, run_id=run_id, workers=args.workers,
        arms=arms, hint=None, prepare_hook=matched_brave_prefill,
        manifest_extra={'spec': str(spec_path), 'dump': args.dump, 'page': PAGE,
                        'variants': [wanted], 'data_daily_quota': args.quota,
                        'prefill': 'matched10 brave + refreshed focal preview'},
    )
    print(f'run: {out}', file=sys.stderr)


if __name__ == '__main__':
    main()
