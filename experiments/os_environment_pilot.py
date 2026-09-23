"""Compare configured open-weight environment models; never load an OpenAI key."""
from __future__ import annotations

import argparse
import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path

import yaml

from ai_collusion.arms import Arm
from ai_collusion.client import ModelConfig
from ai_collusion.env import step
from ai_collusion.episode import run_episodes
from ai_collusion.preparation import prepare_episode
from ai_collusion.wiki import Dump, load_spec, reconstruct
from experiments.data_quota import matched_brave_prefill
from experiments.restored_coordination import CUT, VARIANT, build_spec

ROOT = Path(__file__).resolve().parents[1]
PILOT_VARIANT = 'normal_quota25_os_environment_pilot'
KEY_ENV = 'PILOT_OPENROUTER_API_KEY'


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--run-id', required=True)
    ap.add_argument('--samples', type=int, default=5)
    ap.add_argument('--evaluees', nargs='+', default=['glm-5.3'], choices=['glm-5.3', 'kimi-k3'])
    ap.add_argument('--simulators', nargs='+', default=['env-glm-5.3', 'env-kimi-k3'],
                    choices=['env-glm-5.3', 'env-kimi-k3', 'env-deepseek-v4.1-flash'])
    ap.add_argument('--key-file', default=str(ROOT.parent / 'debate/.env'))
    ap.add_argument('--phase', choices=['preflight', 'probe', 'run'], default='preflight')
    ap.add_argument('--explicit-command-start', action='store_true',
                    help='Opt in to simulator command-start timing clarification; charging is unchanged.')
    ap.add_argument('--shell-workflow', action='store_true',
                    help='Add a local checkpoint/Python HTTP workflow to increase simulator exposure.')
    args = ap.parse_args()
    # No load_models/load_repo_env: only the explicitly selected OpenRouter credential.
    os.environ.pop('OPENAI_API_KEY', None)
    os.environ.pop('OPENAI_ADMIN_KEY', None)
    control = ROOT / 'data' / args.run_id
    control.mkdir(parents=True, exist_ok=True)
    os.environ['AI_COLLUSION_REQUEST_POOL_DIR'] = str(control / 'request-pool')
    os.environ['AI_COLLUSION_REQUEST_POOL_SIZE'] = '32'
    native = yaml.safe_load((ROOT / 'experiments/native-tool-models.yaml').read_text())['models']
    envs = yaml.safe_load((ROOT / 'experiments/envab-models.yaml').read_text())['models']
    models = []
    for name in args.evaluees + args.simulators:
        template = 'env-kimi-k3' if name == 'env-deepseek-v4.1-flash' else name
        cfg = copy.deepcopy(next(m for m in (envs if name.startswith('env-') else native) if m['name'] == template))
        if name == 'env-deepseek-v4.1-flash':
            cfg.update(name=name, model='deepseek/deepseek-v4.1-flash')
        assert cfg['api_key_env'] == 'OPENROUTER_API_KEY'
        assert cfg['base_url'] == 'https://openrouter.ai/api/v1'
        assert cfg['model'] in ['z-ai/glm-5.3', 'moonshotai/kimi-k3', 'deepseek/deepseek-v4.1-flash']
        cfg['api_key_env'] = KEY_ENV
        if name.startswith('env-'):
            cfg['max_tokens'] = 32000
            assert not any(k in cfg['extra_body'] for k in ['tools', 'tool_choice', 'parallel_tool_calls'])
        if cfg['model'] == 'z-ai/glm-5.3':
            cfg['extra_body']['provider'] = {'only': ['fireworks'], 'allow_fallbacks': False}
        models.append(ModelConfig.from_dict(cfg))
    evaluators, simulators = models[:len(args.evaluees)], models[len(args.evaluees):]
    dump = Dump(ROOT / 'data/collusion-wiki')
    source = yaml.safe_load((ROOT / 'wikitasks/sector61_state_sparse.yaml').read_text())
    built, provenance = build_spec(source, dump)
    variant = copy.deepcopy(built['cuts'][CUT]['variants'][VARIANT])
    variant.pop('data_single_state_task_s', None)
    variant['data_daily_quota'] = 25
    if args.explicit_command_start:
        variant['explicit_command_start'] = True
    if args.shell_workflow:
        prefix = (ROOT / 'experiments/shell-workflow-prefix.txt').read_text()
        variant['system_suffix'] = (variant.get('system_suffix', '').rstrip() + '\n\n' + prefix).strip()
    assert variant['data_single_state_s'] == 8
    built['cuts'][CUT]['variants'][PILOT_VARIANT] = variant
    serialized = yaml.safe_dump(built, sort_keys=False, allow_unicode=True)
    spec_path = control / 'spec.yaml'
    if spec_path.exists():
        assert spec_path.read_text() == serialized, 'Frozen spec differs'
    else:
        spec_path.write_text(serialized)
    specs = load_spec(spec_path)
    cut = reconstruct(dump, 'dse/DataUSAStateSequenceCollab2027', 4)
    arm = Arm(id='normal-quota25-env-comparison', variant=PILOT_VARIANT, mode='neutral',
              hint='search_result_and_wiki_preview', wiki_write_instructions=True,
              max_tokens=4096, max_turns=100, num_live_problems=7)

    def prepare(env):
        return matched_brave_prefill(prepare_episode(
            specs, dump, cut, PILOT_VARIANT, 'neutral', env, seed=0,
            num_live_problems=7, wiki_write_instructions=True, hint=arm.hint))

    prepared = [prepare(env) for env in simulators]
    assert all(p.context == prepared[0].context for p in prepared)
    assert all(p.context_sha256 == prepared[0].context_sha256 for p in prepared)
    for p in prepared:
        assert p.world.quota_remaining() == 22
        assert p.world.data_single_state_s == 8
    metadata = {'run_id': args.run_id, 'samples_per_simulator_per_evaluee': args.samples,
                'models': [asdict(m) for m in models], 'arm': arm.to_dict(),
                'context_sha256': prepared[0].context_sha256,
                'spec_sha256': hashlib.sha256(spec_path.read_bytes()).hexdigest(),
                'equal_evaluated_context': True, 'environment_max_tokens': 32000,
                'routing': ('OpenRouter only; GLM pinned Fireworks; Kimi default routing'
                            if 'env-deepseek-v4.1-flash' not in args.simulators else
                            'OpenRouter only; GLM pinned Fireworks; DeepSeek default routing'),
                'openai_key_loaded': False, 'classifier': 'Codex reviewers, collaboration-v1.2, exact live evidence',
                'historical_baseline': 'Context only; no matched historical GLM/normal/quota25 GPT simulator runs',
                'content_provenance': provenance}
    frozen = control / 'preflight.json'
    if frozen.exists():
        assert json.loads(frozen.read_text()) == metadata, 'Frozen preflight differs'
    else:
        save(frozen, metadata)
    print(json.dumps({'preflight': 'passed', 'phase': args.phase, 'evaluees': args.evaluees,
                      'simulators': [m.name for m in simulators], 'context_equal': True}), flush=True)
    if args.phase == 'preflight':
        return
    from dotenv import dotenv_values
    credential = os.environ.get('OPENROUTER_API_KEY') or dotenv_values(args.key_file).get('OPENROUTER_API_KEY')
    if not credential:
        raise RuntimeError('OpenRouter credential unavailable; no fallback permitted')
    os.environ[KEY_ENV] = credential
    assert 'OPENAI_API_KEY' not in os.environ
    if args.phase == 'probe':
        def probe(env):
            p = prepare(env)
            result = step(p.world, 'shell("python -c \'print(17 + 25)\'")')
            data = asdict(result)
            save(control / (env.name + '-probe.json'), data)
            assert result.env_call is not None and not result.env_call.error
            assert '42' in result.result, result.result
            return {'simulator': env.name, 'passed': True, 'result': result.result}
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(probe, simulators))
        save(control / 'probes.json', results)
        print(json.dumps(results), flush=True)
        return
    assert (control / 'probes.json').exists(), 'Run the bounded simulator probes first'
    def play(env):
        return str(run_episodes(models=evaluators, env_model=env, spec=specs, dump=dump,
            cuts={4: cut}, variants=[PILOT_VARIANT], modes=['neutral'], n_samples=args.samples,
            out_dir=str(ROOT / 'runs'), base_seed=0, temperature=None, max_turns=None,
            run_id=args.run_id + '-' + env.name, workers=args.samples * len(evaluators),
            arms=[arm], prepare_hook=matched_brave_prefill,
            manifest_extra={'spec': str(spec_path), 'environment_comparison': metadata}))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(play, simulators))
    save(control / 'completed.json', {'runs': results})
    print(json.dumps({'completed': results}), flush=True)


if __name__ == '__main__':
    main()
