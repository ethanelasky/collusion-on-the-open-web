import copy
import html
import re
from pathlib import Path

import yaml

from ai_collusion.preparation import prepare_episode
from ai_collusion.wiki import Dump, load_spec, reconstruct
from experiments.matched_search import install_matched_search, refresh_focal_wiki_preview
from experiments.restored_coordination import CUT, VARIANT, build_spec

ROOT = Path(__file__).resolve().parents[1]


def test_restoration_changes_only_one_focal_post_and_preserves_inputs(tmp_path):
    source = yaml.safe_load((ROOT/'wikitasks/sector61_state_sparse.yaml').read_text())
    original = copy.deepcopy(source)
    dump = Dump(ROOT/'data/collusion-wiki')
    built, provenance = build_spec(source, dump)
    assert source == original
    variants = built['cuts'][CUT]['variants']
    restored = copy.deepcopy(variants[VARIANT])
    added = restored['wiki_inject'].pop()
    assert restored == variants['sparse_slow_data_v7']
    assert added['text'] == provenance['inserted_text']
    assert all(provenance[k] in added['text'] for k in
               ['verbatim_sequence', 'verbatim_invitation', 'verbatim_signal_request'])
    assert 'UTC 10:' not in added['text']
    path = tmp_path/'spec.yaml';path.write_text(yaml.safe_dump(built))
    spec = load_spec(path);cut = reconstruct(dump, 'dse/DataUSAStateSequenceCollab2027', 4)
    pair = [refresh_focal_wiki_preview(install_matched_search(prepare_episode(
        spec, dump, cut, v, 'neutral', num_live_problems=7,
        hint='search_result_and_wiki_preview', wiki_write_instructions=True),
        'brave', root=ROOT)) for v in ['sparse_slow_data_v7', VARIANT]]
    a, b = pair
    assert a.context['system'] == b.context['system']
    assert a.context['messages'][:-1] == b.context['messages'][:-1]
    assert a.world.task_s == b.world.task_s and a.world.container_utc == b.world.container_utc
    assert a.world.search_fixtures == b.world.search_fixtures
    assert a.world.page_index() == b.world.page_index()
    for v in [a, b]:
        assert v.resolved_config['cut']['data_single_state_task_s'] == 14
        assert v.resolved_config['cut'].get('query_failure_probability', 0) == 0
    page = b.context['messages'][-1]['content']
    assert 'STATE5-XX' in page and 'Active schedules' in page
    assert 'DseWikiStripBlau.gif' in page
    for url in re.findall(r"(?:href|src)=['\"]([^'\"]+)", page):
        url = html.unescape(url)
        if 'wikiservice.at' in url:
            response, _ = b.world.resolve_url(url, read_only=True)
            assert response is not None and 'HTTP 404' not in response
