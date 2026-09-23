"""Shared 25-task-minute data cooldown, layered on the quota25 coordination arm."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

import yaml

from ai_collusion.arms import Arm
from ai_collusion.episode import run_episodes
from ai_collusion.runner import load_models, load_repo_env
from ai_collusion.wiki import Dump, load_spec, reconstruct
from experiments.data_quota import build_spec as quota_spec, matched_brave_prefill, variant_name
from experiments.restored_coordination import CUT

ROOT = Path(__file__).resolve().parents[1]
VARIANT = variant_name(25) + '_cooldown1500'
ARM = Arm(id='slow-data-coordination-quota25-cooldown1500-v7', variant=VARIANT,
          mode='neutral', hint='search_result_and_wiki_preview', wiki_write_instructions=True,
          max_tokens=4096, max_turns=100, num_live_problems=7)


def build_spec(source, dump):
    built, provenance = quota_spec(source, dump, 25)
    cfg = copy.deepcopy(built['cuts'][CUT]['variants'][variant_name(25)])
    cfg['data_request_cooldown_task_s'] = 1500
    built['cuts'][CUT]['variants'][VARIANT] = cfg
    return built, {'base': provenance, 'only_setting_added': 'data_request_cooldown_task_s=1500',
                   'capacity': 1, 'bulk_requests': 'rejected; require one supported State filter',
                   'initial_state': 'one available slot at live start; historical prefill unchanged'}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--samples', type=int, default=3)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--run-id')
    ap.add_argument('--env-file', default=str(ROOT.parent / 'debate/.env'))
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    rid = args.run_id or 'sol-cooldown1500-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    control = ROOT / 'data' / rid
    control.mkdir(parents=True, exist_ok=False)
    dump = Dump(ROOT / 'data/collusion-wiki')
    built, provenance = build_spec(yaml.safe_load((ROOT / 'wikitasks/sector61_state_sparse.yaml').read_text()), dump)
    spec_path = control / 'spec.yaml'
    spec_path.write_text(yaml.safe_dump(built, sort_keys=False, allow_unicode=True))
    model = load_models(str(ROOT / 'experiments/native-tool-models.yaml'), ['gpt-5.6'])[0]
    env = replace(load_models(str(ROOT / 'models.yaml'), ['env-gpt-5.6'])[0], max_tokens=64000)
    metadata = {**provenance, 'arm': asdict(ARM), 'model': asdict(model), 'environment_model': asdict(env)}
    metadata['source_hashes'] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                               for name in ('ai_collusion/env.py', 'ai_collusion/data_cost.py',
                                            'ai_collusion/data_cooldown.py', 'ai_collusion/episode.py',
                                            'ai_collusion/client.py', 'ai_collusion/auth_stop.py',
                                            'experiments/request_cooldown.py')}
    (control / 'preflight.json').write_text(json.dumps(metadata, indent=2) + '\n')
    if args.dry_run:
        print(f'Prepared {spec_path}; no API requests sent.')
        return
    load_repo_env(Path(args.env_file))
    import os
    os.environ['AI_COLLUSION_REQUEST_POOL_DIR'] = str(control / 'request-pool')
    os.environ['AI_COLLUSION_REQUEST_POOL_SIZE'] = '1'
    out = run_episodes(models=[model], env_model=env, spec=load_spec(spec_path), dump=dump,
                       cuts={4: reconstruct(dump, 'dse/DataUSAStateSequenceCollab2027', 4)},
                       variants=[VARIANT], modes=['neutral'], n_samples=args.samples,
                       out_dir=str(ROOT / 'runs'), base_seed=args.seed, temperature=None,
                       max_turns=None, run_id=rid, workers=1, arms=[ARM],
                       prepare_hook=matched_brave_prefill, manifest_extra={'shared_cooldown': metadata})
    print(out)


if __name__ == '__main__':
    main()
