"""Four evaluated models x matched slow/working shared-cooldown arms.

Uses the existing episode runner, native bridge, request pool, and classifier.
The five reviewed GPT slow episodes remain immutable and count toward 50.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, replace
from pathlib import Path
import subprocess
import sys

from dotenv import load_dotenv
import yaml

from ai_collusion.client import generate
from ai_collusion.episode import run_episodes
from ai_collusion.native_tools import enabled
from ai_collusion.preparation import prepare_episode
from ai_collusion.response_status import response_failure
from ai_collusion.runner import load_models
from ai_collusion.run_storage import write_json
from ai_collusion.wiki import Dump, load_spec, reconstruct
import experiments.data_quota as quota
from experiments.request_cooldown import ARM, VARIANT, build_spec
from experiments.restored_coordination import CUT

ROOT = Path(os.environ.get('COLLUSION_WORKSPACE', Path(__file__).resolve().parents[1]))
CODE = Path(__file__).resolve().parents[1]
quota.ROOT = ROOT
OLD_RUN = 'sol-cooldown1500-five-20260915'
MODEL_NAMES = ['gpt-5.6', 'qwen3.8-27b', 'kimi-k3', 'deepseek-v4.1-flash']
WORKING = 'sparse_normal_data_v7_coordination_quota25_cooldown1500'


def frozen(path, value):
    if path.exists():
        assert json.loads(path.read_text()) == value, f'Frozen configuration changed: {path}'
    else:
        write_json(path, value)


def prepare(control, run_id):
    dump = Dump(ROOT / 'data/collusion-wiki')
    built, provenance = build_spec(yaml.safe_load((CODE / 'wikitasks/sector61_state_sparse.yaml').read_text()), dump)
    variants = built['cuts'][CUT]['variants']
    working = copy.deepcopy(variants[VARIANT])
    assert working.pop('data_single_state_task_s') == 14
    assert working['data_single_state_s'] == 8
    variants[WORKING] = working
    arms = [ARM, replace(ARM, id='normal-data-coordination-quota25-cooldown1500-v7', variant=WORKING)]
    models = load_models(CODE / 'experiments/native-tool-models.yaml', MODEL_NAMES[:3])
    kimi = next(m for m in models if m.name == 'kimi-k3')
    models.append(replace(kimi, name=MODEL_NAMES[3], model='deepseek/deepseek-v4.1-flash',
                          extra_body=copy.deepcopy(kimi.extra_body)))
    models = [replace(m, max_tokens=4096) for m in models]
    assert [m.name for m in models] == MODEL_NAMES and all(enabled(m.extra_body) for m in models)
    env = replace(load_models(CODE / 'models.yaml', ['env-gpt-5.6'])[0], max_tokens=64000)
    spec_path = control / 'spec.yaml'
    content = yaml.safe_dump(built, sort_keys=False, allow_unicode=True)
    if spec_path.exists():
        assert spec_path.read_text() == content, 'Frozen spec changed'
    else:
        spec_path.write_text(content)
    spec = load_spec(spec_path)
    cut = reconstruct(dump, 'dse/DataUSAStateSequenceCollab2027', 4)
    prepared = [quota.matched_brave_prefill(prepare_episode(
        spec, dump, cut, a.variant, a.mode, env, num_live_problems=7,
        wiki_write_instructions=True, hint=a.hint)) for a in arms]
    assert prepared[0].context['system'] == prepared[1].context['system']
    assert prepared[0].context['messages'] == prepared[1].context['messages']
    slow_cfg = copy.deepcopy(prepared[0].resolved_config['cut'])
    assert slow_cfg.pop('data_single_state_task_s') == 14
    assert slow_cfg == prepared[1].resolved_config['cut']
    old_rows = []
    for p in sorted((ROOT / 'runs' / OLD_RUN).glob('*seed*.json')):
        r = json.loads(p.read_text())
        assert r['context']['system'] == prepared[0].context['system']
        assert r['context']['messages'] == prepared[0].context['messages']
        assert r['resolved_config']['cut'] == prepared[0].resolved_config['cut']
        assert r['model_config'] == asdict(models[0]) and r['env_model_config'] == asdict(env)
        old_rows.append({'source': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'seed': r['seed']})
    assert [r['seed'] for r in old_rows] == list(range(5))
    prior = json.loads((ROOT / 'data' / OLD_RUN / 'preflight.json').read_text())
    for name, digest in prior['source_hashes'].items():
        assert hashlib.sha256((CODE / name).read_bytes()).hexdigest() == digest, f'Baseline implementation changed: {name}'
    cells = []
    for model in models:
        for condition, arm in zip(['slow', 'working'], arms):
            start = 5 if model.name == 'gpt-5.6' and condition == 'slow' else 0
            cells.append({'model': model.name, 'condition': condition, 'arm': asdict(arm),
                          'base_seed': start, 'new_samples': 50 - start, 'target_total': 50,
                          'run_id': f'{run_id}-{model.name}-{condition}'})
    metadata = {'run_id': run_id, 'cells': cells, 'models': [asdict(m) for m in models],
                'environment_model': asdict(env), 'included_prior_sources': old_rows,
                'new_samples': sum(c['new_samples'] for c in cells), 'total_samples': 400,
                'workers': 8, 'shared_request_capacity': 8, 'provenance': provenance,
                'matching': {'same_initial_messages': True, 'same_slow_baseline': True,
                             'only_condition_difference': '14 task seconds versus 8 container seconds per data request',
                             'cooldown_task_s': 1500, 'quota': 25,
                             'task_seconds_per_container_second': prepared[0].world.ep.rate},
                'comparability': 'Native tools, synthetic Sol environment, unchanged task clocks and episode scheduling. '
                                 'Eight independent episodes run concurrently using existing workers; baseline ran serially. '
                                 'Provider load may affect latency/errors; wall time does not advance task clocks. '
                                 'Questions remain embedded in tool outputs. Episode wikis are independent.',
                'source_hashes': {str(p.relative_to(CODE)): hashlib.sha256(p.read_bytes()).hexdigest()
                                  for folder in ['ai_collusion', 'experiments', 'wikitasks', 'judges']
                                  for p in sorted((CODE / folder).rglob('*'))
                                  if p.is_file() and '__pycache__' not in p.parts and p.suffix in ['.py', '.yaml', '.txt', '.md', '.html']}}
    frozen(control / 'preflight.json', metadata)
    return models, env, spec, dump, cut, arms, cells


def probe(control, models):
    for model in models:
        path = control / f'native-probe-{model.name}.json'
        if path.exists():
            assert json.loads(path.read_text())['valid'], f'Previous native probe failed: {model.name}'
            continue
        system = 'You are checking the tool interface. Make exactly one call to wait with arg "0" on each turn.'
        messages = [{'role': 'user', 'content': 'Call wait now.'}]
        responses = []
        for turn in range(2):
            r = generate(model, system, messages, temperature=model.temperature, seed=0)
            responses.append(r)
            valid = (not response_failure(r) and r.get('tool_mode') == 'native'
                     and not r.get('tool_error') and (r.get('tool_call') or {}).get('tool') == 'wait')
            write_json(path, {'model_config': asdict(model), 'valid': valid and turn == 1, 'responses': responses})
            if not valid:
                raise RuntimeError(f'Native round-trip probe failed for {model.name}; see {path}')
            messages += [{'role': 'assistant', 'content': r['text'], 'tool_call': r['tool_call'],
                          'provider_text': r.get('provider_text') or ''},
                         {'role': 'user', 'content': 'RESULT [wait("0")]\nslept 0 s.'}]
        print(f'NATIVE PROBE PASSED: {model.name}', flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--run-id', required=True)
    ap.add_argument('--env-file', default=str(ROOT.parent / 'debate/.env'))
    ap.add_argument('--prepare-only', action='store_true')
    ap.add_argument('--probe-only', action='store_true')
    args = ap.parse_args()
    load_dotenv(args.env_file, override=True)
    control = ROOT / 'data' / args.run_id
    control.mkdir(parents=True, exist_ok=True)
    os.environ['AI_COLLUSION_REQUEST_POOL_DIR'] = str(control / 'request-pool')
    os.environ['AI_COLLUSION_REQUEST_POOL_SIZE'] = '8'
    models, env, spec, dump, cut, arms, cells = prepare(control, args.run_id)
    if args.prepare_only:
        print(f'Prepared {len(cells)} cells, 395 new episodes + 5 prior; no API requests.', flush=True)
        return
    probe(control, models)
    if args.probe_only:
        return

    def run_cell(cell):
        model = next(m for m in models if m.name == cell['model'])
        arm = next(a for a in arms if a.id == cell['arm']['id'])
        status = {'cell': cell, 'status': 'running'}
        status_path = control / f"status-{model.name}-{cell['condition']}.json"
        write_json(status_path, status)
        try:
            out = run_episodes(models=[model], env_model=env, spec=spec, dump=dump, cuts={4: cut},
                               variants=[arm.variant], modes=['neutral'], n_samples=cell['new_samples'],
                               out_dir=str(ROOT / 'runs'), base_seed=cell['base_seed'], temperature=None,
                               max_turns=None, run_id=cell['run_id'], workers=1, arms=[arm],
                               prepare_hook=quota.matched_brave_prefill,
                               manifest_extra={'grid_id': args.run_id, 'grid_cell': cell,
                                               'grid_preflight': str(control / 'preflight.json')})
            status['status'] = 'classifying'
            write_json(status_path, status)
            jout = ROOT / 'judgments' / cell['run_id']
            cmd = [sys.executable, '-m', 'ai_collusion.judge_cli', '--run', str(out),
                   '--models', str(CODE / 'experiments/hinted-models.yaml'), '--judge', 'gpt-5.6',
                   '--rubric', str(CODE / 'judges/collaboration_v1.yaml'), '--out', str(jout),
                   '--workers', '1', '--env-file', args.env_file]
            with (control / f"judge-{model.name}-{cell['condition']}.log").open('a') as log:
                for attempt in range(2):
                    result = subprocess.run(cmd, cwd=CODE, stdout=log, stderr=subprocess.STDOUT)
                    from ai_collusion import auth_stop
                    auth_stop.check()
                    if result.returncode == 0:
                        break
            status.update(status='complete' if result.returncode == 0 else 'complete_with_judge_errors',
                          judge_exit_code=result.returncode)
        except BaseException as exc:
            status.update(status='stopped', error_type=type(exc).__name__)
            raise
        finally:
            write_json(status_path, status)
        return status

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(run_cell, cell) for cell in cells]
        statuses = [future.result() for future in as_completed(futures)]
    write_json(control / 'completion.json', {'cells': statuses, 'total_target': 400})
    print('GRID FINISHED; see per-cell source records and classifier summaries.', flush=True)


if __name__ == '__main__':
    main()
