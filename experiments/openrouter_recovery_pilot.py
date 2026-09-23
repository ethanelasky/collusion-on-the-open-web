"""Small, separately labeled recovery pilot using the frozen cooldown task."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from ai_collusion.arms import Arm
from ai_collusion.client import ModelConfig
from ai_collusion.episode import run_episodes
from ai_collusion.preparation import prepare_episode
from ai_collusion.run_storage import write_json
from ai_collusion.wiki import Dump, load_spec, reconstruct
from experiments.data_quota import matched_brave_prefill
from experiments.openrouter_recovery import recovery_profile

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'data/cooldown-grid-4models-20260915'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', default='openrouter-recovery-pilot-20260916')
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    control = ROOT / 'data' / args.run_id
    control.mkdir(parents=True, exist_ok=True)
    baseline = json.loads((BASE / 'preflight.json').read_text())
    env = ModelConfig(**baseline['environment_model'])
    models = {m['name']: ModelConfig(**m) for m in baseline['models']}
    spec = load_spec(BASE / 'spec.yaml')
    dump = Dump(ROOT / 'data/collusion-wiki')
    cut = reconstruct(dump, 'dse/DataUSAStateSequenceCollab2027', 4)
    jobs = []
    for cell in baseline['cells']:
        if cell['model'] not in {'qwen3.8-27b', 'deepseek-v4.1-flash'}:
            continue
        # Provider switching did not improve DeepSeek in the small replay sample.
        retries_only = cell['model'].startswith('deepseek')
        model = recovery_profile(models[cell['model']], retries_only=retries_only)
        old_arm = Arm(**cell['arm'])
        arm = replace(old_arm, id=old_arm.id + '-or-recovery-v1', max_tokens=model.max_tokens)
        prepared = matched_brave_prefill(prepare_episode(
            spec, dump, cut, arm.variant, arm.mode, env, num_live_problems=7,
            wiki_write_instructions=True, hint=arm.hint))
        original_path = next((ROOT / 'runs' / cell['run_id']).glob('*seed0.json'))
        original = json.loads(original_path.read_text())
        assert prepared.context['system'] == original['context']['system']
        assert prepared.context['messages'] == original['context']['messages']
        assert prepared.resolved_config['cut'] == original['resolved_config']['cut']
        jobs.append({'model': asdict(model), 'arm': asdict(arm), 'condition': cell['condition'],
                     'profile': 'retries-only' if retries_only else 'routing-budget',
                     'run_id': f"{args.run_id}-{model.name}-{cell['condition']}", 'seed': 0,
                     'baseline_source': str(original_path)})
    metadata = {'jobs': jobs, 'environment_model': asdict(env), 'workers': 2,
                'shared_request_pool': str(BASE / 'request-pool'), 'shared_request_capacity': 8,
                'source_hashes': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                  for folder in ['ai_collusion', 'experiments']
                                  for p in sorted((ROOT / folder).rglob('*.py'))},
                'spec_sha256': hashlib.sha256((BASE / 'spec.yaml').read_bytes()).hexdigest(),
                'comparability': 'Separate cohort: changed generation budgets, recovery feedback, '
                                 'attempt limits, and Qwen routing. Identical initial context, task '
                                 'configuration, simulator, tool execution, and task clocks. '
                                 'Do not pool with the original grid.'}
    path = control / 'preflight.json'
    if path.exists():
        assert json.loads(path.read_text()) == metadata, 'Pilot configuration changed'
    else:
        write_json(path, metadata)
    if args.prepare_only:
        print('Prepared four episodes; matched all initial contexts and task configurations.')
        return
    load_dotenv(ROOT.parent / 'debate/.env', override=True)
    os.environ['AI_COLLUSION_REQUEST_POOL_DIR'] = str(BASE / 'request-pool')
    os.environ['AI_COLLUSION_REQUEST_POOL_SIZE'] = '8'

    def run(job):
        model, arm = ModelConfig(**job['model']), Arm(**job['arm'])
        status_path = control / f"status-{model.name}-{job['condition']}.json"
        status = {'job': job, 'status': 'running'}
        write_json(status_path, status)
        try:
            out = run_episodes(models=[model], env_model=env, spec=spec, dump=dump, cuts={4: cut},
                               variants=[arm.variant], modes=['neutral'], n_samples=1,
                               out_dir=str(ROOT / 'runs'), base_seed=job['seed'], temperature=None,
                               max_turns=None, run_id=job['run_id'], workers=1, arms=[arm],
                               prepare_hook=matched_brave_prefill,
                               manifest_extra={'recovery_pilot': metadata, 'recovery_job': job})
            records = [json.loads(p.read_text()) for p in out.glob('*seed*.json')]
            status.update(status='complete', records=len(records),
                          episode_errors=[(r.get('error') or {}).get('type') for r in records])
        except BaseException as exc:
            status.update(status='stopped', error_type=type(exc).__name__)
            raise
        finally:
            write_json(status_path, status)
        print(json.dumps(status), flush=True)
        return status

    # Interleave model families so both begin immediately with two workers.
    jobs.sort(key=lambda j: (j['condition'], j['model']['name']))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, jobs))
    write_json(control / 'completion.json', results)


if __name__ == '__main__':
    main()
