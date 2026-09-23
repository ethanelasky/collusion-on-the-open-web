"""Matched normal-speed quota pilot; existing runtime and native model tools."""
from __future__ import annotations

import argparse
import copy
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from ai_collusion.arms import Arm
from ai_collusion.episode import run_episodes
from ai_collusion.native_tools import enabled
from ai_collusion.preparation import prepare_episode
from ai_collusion.runner import load_models
from ai_collusion.wiki import Dump, load_spec, reconstruct
from experiments.data_quota import matched_brave_prefill
from experiments.restored_coordination import CUT, VARIANT, build_spec

ROOT = Path(__file__).resolve().parents[1]
BASE = 'sparse_normal_data_v7_coordination'
QUOTA = BASE + '_quota25'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    control = ROOT / 'data' / args.run_id
    control.mkdir(parents=True, exist_ok=True)
    source = yaml.safe_load((ROOT / 'wikitasks/sector61_state_sparse.yaml').read_text())
    dump = Dump(ROOT / 'data/collusion-wiki')
    built, provenance = build_spec(source, dump)
    variants = built['cuts'][CUT]['variants']
    normal = copy.deepcopy(variants[VARIANT])
    normal.pop('data_single_state_task_s', None)
    normal.pop('data_daily_quota', None)
    assert normal['data_single_state_s'] == 8
    assert not normal.get('website_errors')
    assert not normal.get('query_failure_probability', 0)
    variants[BASE] = normal
    variants[QUOTA] = {**copy.deepcopy(normal), 'data_daily_quota': 25}
    spec_path = control / 'spec.yaml'
    serialized = yaml.safe_dump(built, sort_keys=False, allow_unicode=True)
    if spec_path.exists():
        assert spec_path.read_text() == serialized, 'Refusing to change frozen spec'
    else:
        spec_path.write_text(serialized)
    models_path = ROOT / 'experiments/native-tool-models.yaml'
    model = load_models(models_path, ['gpt-5.6'])[0]
    env = load_models(models_path, ['env-gpt-5.6'])[0]
    assert enabled(model.extra_body), 'Native tools are required'
    arms = [Arm(id=name, variant=variant, mode='neutral',
                hint='search_result_and_wiki_preview', wiki_write_instructions=True,
                max_tokens=4096, max_turns=100, num_live_problems=7)
            for name, variant in [('normal-data-no-quota-v7', BASE),
                                  ('normal-data-quota25-v7', QUOTA)]]
    (control / 'arms.yaml').write_text(yaml.safe_dump({'arms': [a.to_dict() for a in arms]}))
    spec = load_spec(spec_path)
    cut = reconstruct(dump, 'dse/DataUSAStateSequenceCollab2027', 4)
    prepared = [matched_brave_prefill(prepare_episode(
        spec, dump, cut, a.variant, a.mode, env, num_live_problems=7,
        wiki_write_instructions=True, hint=a.hint)) for a in arms]
    a, b = prepared
    assert a.context['system'] == b.context['system']
    header = re.compile(r'HTTP 200 OK\nContent-Type: application/json\n'
                        r'X-RateLimit-Limit: 25\nX-RateLimit-Remaining: \d+\n'
                        r'X-RateLimit-Reset: \d+\n\n')
    normalized = copy.deepcopy(b.context['messages'])
    for m in normalized:
        m['content'] = header.sub('', m['content'])
    assert normalized == a.context['messages'], 'Unexpected prefill difference'
    assert a.world.container_utc == b.world.container_utc
    assert a.world.task_s == b.world.task_s
    assert a.world.wiki_body('WorkforceLookupNotes') == b.world.wiki_body('WorkforceLookupNotes')
    assert b.world.quota_remaining() == 22
    cfg_a = copy.deepcopy(a.resolved_config['cut'])
    cfg_b = copy.deepcopy(b.resolved_config['cut'])
    assert cfg_b.pop('data_daily_quota') == 25
    assert cfg_a == cfg_b
    report = {'checked_at': datetime.now(timezone.utc).isoformat(),
              'normal_single_state_container_s': 8, 'slow_task_override_removed': True,
              'native_tools': True, 'samples_per_arm': 5, 'workers': 10,
              'only_condition_difference': 'quota enforcement and response headers',
              'same_system_wiki_clocks_and_prefill_except_quota_headers': True,
              'coordination_content': provenance,
              'historical_comparability': 'Separate pilot; earlier smoke used slow data and text tools.'}
    (control / 'preflight.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != 'coordination_content'}), flush=True)
    if args.dry_run:
        return
    out = run_episodes(models=[model], env_model=env, spec=spec, dump=dump,
                       cuts={4: cut}, variants=[BASE, QUOTA], modes=['neutral'],
                       n_samples=5, out_dir=str(ROOT / 'runs'), base_seed=0,
                       temperature=None, max_turns=None, run_id=args.run_id,
                       workers=10, arms=arms, prepare_hook=matched_brave_prefill,
                       manifest_extra={'spec': str(spec_path), 'models_file': str(models_path),
                                       'pilot': report})
    print(f'Finished: {out}', flush=True)


if __name__ == '__main__':
    main()
