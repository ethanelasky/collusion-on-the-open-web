import copy
import json
import html
from pathlib import Path

import yaml

from ai_collusion.judge import project_record
from ai_collusion.preparation import prepare_episode
from ai_collusion.wiki import Dump, load_spec, reconstruct
from experiments.overnight_realism import CUT, EXACT, GENERAL, build_spec, install_discussion_prefix

ROOT = Path(__file__).resolve().parents[1]


def test_only_sentence_and_search_change(tmp_path):
    source = yaml.safe_load((ROOT/'wikitasks/sector61_state_sparse.yaml').read_text())
    before = copy.deepcopy(source)
    new = build_spec(source)
    assert source == before
    variants = new['cuts'][CUT]['variants']
    for search in ('google', 'brave'):
        exact = json.dumps(variants[f'{search}-exact'])
        general = json.dumps(variants[f'{search}-general'])
        assert exact.count(EXACT) == 1
        assert exact.replace(EXACT, GENERAL) == general
    for wording in ('exact', 'general'):
        a = copy.deepcopy(variants[f'google-{wording}'])
        b = copy.deepcopy(variants[f'brave-{wording}'])
        a.pop('search_fixture'); b.pop('search_fixture')
        assert a == b
    assert 'Later rounds allow 13 seconds' in new['system']


def test_forced_prefix_is_context_only_and_time_comes_from_world(tmp_path):
    cfg = build_spec(yaml.safe_load((ROOT/'wikitasks/sector61_state_sparse.yaml').read_text()))
    path = tmp_path/'spec.yaml'; path.write_text(yaml.safe_dump(cfg))
    spec = load_spec(path); dump = Dump(ROOT/'data/collusion-wiki')
    cut = reconstruct(dump, 'dse/DataUSAStateSequenceCollab2027', 4)
    contexts = []
    for wording in ('exact', 'general'):
        p = prepare_episode(spec, dump, cut, f'brave-{wording}', 'neutral',
                            seed=400, hint='search_result', wiki_write_instructions=True)
        install_discussion_prefix(p)
        assert p.context['messages'][-1]['content'].startswith('RESULT')
        assert 'Save=1' in p.context['messages'][-1]['content']
        assert p.world.task_clock() == p.context['provenance']['diagnostic_prefix']['steps'][-1]['task_clock']
        record = {'context': p.context, 'live_start_message_index': len(p.context['messages']),
                  'episode': {'turns': []}}
        projection = project_record(record)
        assert projection['context']['context_only'] and not projection['turns']
        contexts.append(json.dumps(p.context))
    # The surface passage is the sole context intervention (HTML wraps source lines).
    assert contexts[0].replace(html.escape(EXACT), html.escape(GENERAL)) == contexts[1]
