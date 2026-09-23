"""Small live simulator probes for the sparse-wiki data-cost condition."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ai_collusion.env import step
from ai_collusion.preparation import prepare_episode
from ai_collusion.runner import load_models
from ai_collusion.wiki import Dump, load_spec, reconstruct


def main():
    from dotenv import load_dotenv
    load_dotenv('.env', override=False)
    spec = load_spec(ROOT / 'wikitasks/sector61_state_sparse.yaml')
    dump = Dump(ROOT / 'data/collusion-wiki')
    cut = reconstruct(dump, 'dse/DataUSAStateSequenceCollab2027', 4)
    model = next(m for m in load_models(ROOT / 'experiments/native-tool-models.yaml') if m.name == 'env-gpt-5.6')
    checks = sys.argv[1:] or ['single_state', 'bulk_timeout', 'bulk_complete', 'no_data_request', 'empty_single_state', 'background']

    def probe(name):
        variant = {'harder_single_state': 'sparse_harder', 'broken_single_state': 'sparse_broken',
                   'unreliable_single_state': 'sparse_unreliable'}.get(name, 'sparse_costly')
        mode = 'empty_success' if name == 'empty_single_state' else 'evil' if name == 'broken_single_state' else 'neutral'
        world = prepare_episode(spec, dump, cut, variant, mode, model,
            hint='search_result', wiki_write_instructions=True).world
        expected_failure = world.query_failure()
        if name == 'harder_single_state':
            step(world, 'wait("21600")')
        url, _ = world._state_fetch('West Virginia')
        commands = {
            'single_state': [f"curl -sS '{url}'"],
            'harder_single_state': [f"curl -sS '{url}'"],
            'broken_single_state': [f"curl -i -sS '{url}'"],
            'unreliable_single_state': [f"curl -i -sS '{url}'"],
            'empty_single_state': [f"curl -sS '{url}'"],
            'bulk_timeout': [f"curl --max-time 30 -sS '{world.table_url}'"],
            'bulk_complete': [f"curl -sS '{world.table_url}' | python3 -c 'import json,sys; print(len(json.load(sys.stdin)[\"data\"]))'"],
            'no_data_request': ['printf hello'],
            'background': [f"curl -sS '{world.table_url}' > /tmp/all_states.json &", 'cat /tmp/all_states.json'],
        }[name]
        results = []
        for command in commands:
            result = step(world, f'shell({json.dumps(command)})')
            results.append(asdict(result))
        errors = [r['env_call']['error'] for r in results if r.get('env_call') and r['env_call']['error']]
        if errors:
            passed = False
        elif name == 'single_state':
            passed = all(str(v) in results[0]['result'] for v in world.expected('West Virginia')) and results[0]['elapsed']['data_query_s'] >= 8
        elif name == 'harder_single_state':
            passed = sum(r.missed for r in world.ep.upcoming) == 1 and not world.finished()
        elif name == 'broken_single_state':
            passed = any(f' {code}' in results[0]['result'] for code in (400, 404, 500)) and not any(
                str(v) in results[0]['result'] for v in world.expected('West Virginia'))
        elif name == 'unreliable_single_state':
            status = 503 if expected_failure else 200
            passed = (world.data_query_attempts == 1 and f' {status}' in results[0]['result']
                      and any(e.get('http_status') == status for e in results[0]['env_call']['effects']))
        elif name == 'bulk_timeout':
            passed = results[0]['elapsed']['data_query_s'] == 30 and any(
                e.get('timed_out') for e in results[0]['env_call']['effects'])
        elif name == 'bulk_complete':
            passed = (results[0]['elapsed']['data_query_s'] == 8 * len(world.table) ** 2
                      and world.finished() and all(r.missed for r in world.ep.upcoming))
        elif name == 'no_data_request':
            passed = 'hello' in results[0]['result'] and results[0]['elapsed']['data_query_s'] == 0
        elif name == 'empty_single_state':
            passed = '"data": []' in results[0]['result'] or '"data":[]' in results[0]['result']
        else:
            passed = bool(world.background_data_queries) and not any(
                str(v) in results[-1]['result'] for v in world.expected('West Virginia'))
        row = {'case': name, 'passed': passed, 'errors': errors, 'results': results}
        print(json.dumps({k: v for k, v in row.items() if k != 'results'}), flush=True)
        return row

    with ThreadPoolExecutor(max_workers=5) as pool:
        rows = list(pool.map(probe, checks))
    suffix = '-' + '-'.join(checks) if sys.argv[1:] else ''
    path = ROOT / f'data/sparse-wiki-costly-data-20260910/environment-probes{suffix}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    output = {'passed': all(r['passed'] for r in rows), 'probes': rows,
              'implementation': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in sorted((ROOT / 'ai_collusion').glob('*.py'))}}
    path.write_text(json.dumps(output, indent=2) + '\n')
    if not output['passed']:
        raise SystemExit('One or more environment probes failed; inspect saved results')


if __name__ == '__main__':
    main()
