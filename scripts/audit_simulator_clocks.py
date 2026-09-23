"""Offline clock ownership probes and saved DeepSeek time-call inventory."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

from ai_collusion.client import ModelConfig
import ai_collusion.env as env
from ai_collusion.preparation import prepare_episode
from ai_collusion.wiki import Dump, load_spec, reconstruct
from experiments.data_quota import matched_brave_prefill

ROOT = Path(__file__).resolve().parents[1]
C = ROOT / 'data/os-env-deepseek-normal-quota25-20260914'
OUT = ROOT / 'data/simulator-clock-audit-20260915'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    meta = json.loads((C / 'preflight.json').read_text())
    dump = Dump(ROOT / 'data/collusion-wiki')
    spec = load_spec(C / 'spec.yaml')
    cut = reconstruct(dump, 'dse/DataUSAStateSequenceCollab2027', 4)
    stub = ModelConfig(name='offline-clock-probe', transport='stub', model='offline')

    def world():
        return matched_brave_prefill(prepare_episode(spec, dump, cut, meta['arm']['variant'], 'neutral', stub,
               seed=0, num_live_problems=7, wiki_write_instructions=True, hint=meta['arm']['hint'])).world

    calls = []
    def fake_generate(model, system, messages, **kwargs):
        # Deliberately false output demonstrates what the current runtime validates.
        calls.append(messages[-1]['content'])
        return {'text': '2099-01-01 00:00:00 UTC\n@@DATA_QUERIES []\n@@ELAPSED 1'}

    original = env.generate
    env.generate = fake_generate
    probes = []
    try:
        for name, call in [
            ('standalone_wait', 'wait("60")'),
            ('date_utc', 'shell("date -u")'),
            ('python_time', 'shell(\'python3 -c "import time; print(time.time())"\')'),
            ('shell_sleep', 'shell("sleep 60")'),
            ('external_time_service', 'web_fetch("https://worldtimeapi.org/api/timezone/Etc/UTC")'),
            ('wiki_read', 'web_fetch("https://wikiservice.at/dse/wiki.cgi?action=browse&id=WorkforceLookupNotes")'),
        ]:
            w = world()
            before_task, before_utc, before_calls = w.task_s, w.container_utc, len(calls)
            result = env.step(w, call)
            probes.append({'name': name, 'call': call, 'source': result.source,
                           'model_called': len(calls) > before_calls, 'error': bool(result.env_call and result.env_call.error),
                           'task_delta_s': w.task_s - before_task,
                           'utc_delta_s': (w.container_utc - before_utc).total_seconds(),
                           'elapsed_metadata': result.elapsed, 'result_excerpt': result.result[:450]})
    finally:
        env.generate = original
    assert [p['model_called'] for p in probes] == [False, True, True, True, True, False]
    assert all(not p['error'] for p in probes)
    assert probes[0]['task_delta_s'] == 39
    assert probes[3]['utc_delta_s'] < 5  # Explicit sleep duration is not parsed by the runtime.
    inventory, counts = [], Counter()
    for row in json.loads((C / 'review-index.json').read_text()):
        path = Path(row['source'])
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row['sha256']
        record = json.loads(path.read_text())
        for t in record['episode']['turns']:
            call = t.get('call') or {}
            counts['turns'] += 1
            counts['env_calls'] += bool(t.get('env_call'))
            counts['deterministic_waits'] += call.get('tool') == 'wait' and not t.get('env_call')
            arg = call.get('arg', '')
            tags = []
            if call.get('tool') == 'shell':
                if re.search(r'\bdate\s+-', arg): tags.append('date command mentioned')
                if re.search(r'\bsleep\s+\d', arg): tags.append('numeric sleep mentioned')
                if re.search(r'\bps\s', arg): tags.append('process listing mentioned')
                if re.search(r'\bls\s+-[^\s]*l', arg): tags.append('long file listing mentioned')
                if re.search(r'time\.(time|monotonic)|datetime\.', arg): tags.append('Python clock mentioned')
            if 'worldtimeapi.org' in arg: tags.append('external clock service mentioned')
            if tags:
                counts['time_related_calls'] += 1
                for tag in tags: counts[tag] += 1
                inventory.append({'source_id': row['id'], 'turn': t['turn'], 'source': t['source'],
                                  'tags': tags, 'call': call, 'container_utc': t['container_utc'],
                                  'elapsed': t.get('elapsed')})
    report = {'configuration': {'task_clock': world().task_clock(), 'utc': world().container_utc.isoformat(),
                                'rate': world().ep.rate, 'call_cost_s': world().ep.call_cost_s,
                                'elapsed_cap_s': world().ep.elapsed_cap_s},
              'probes': probes, 'counts': dict(counts), 'inventory': inventory,
              'new_model_calls': 0, 'new_network_calls': 0,
              'method': 'Locally replaced env.generate with a stub; no source records or runtime code modified. Inventory tags are lexical, not a shell execution parser.'}
    (OUT / 'audit.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({'counts': dict(counts), 'probes': probes}, indent=2))


if __name__ == '__main__':
    main()
