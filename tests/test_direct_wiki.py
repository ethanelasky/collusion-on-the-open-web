import copy
import html
import re
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from ai_collusion.judge import project_record
from ai_collusion.preparation import prepare_episode
from ai_collusion.wiki import Dump, load_spec, reconstruct, wiki_save_url
from experiments.direct_wiki import build_spec, remove_search_prefill, URL_RELAY_VARIANT, URL_RELAY_POST
from experiments.matched_search import install_matched_search, refresh_focal_wiki_preview
from experiments.restored_coordination import CUT, VARIANT

ROOT = Path(__file__).resolve().parents[1]


def test_direct_prefix_and_url_relay_preserve_controlled_inputs(tmp_path):
    source = yaml.safe_load((ROOT/'wikitasks/sector61_state_sparse.yaml').read_text())
    dump = Dump(ROOT/'data/collusion-wiki')
    built, provenance = build_spec(source, dump)
    cfg = copy.deepcopy(built['cuts'][CUT]['variants'][URL_RELAY_VARIANT])
    updates = provenance['scheduled_focal_updates']
    assert cfg['wiki_inject'][-len(updates):] == updates
    del cfg['wiki_inject'][-len(updates):]
    assert cfg['wiki_inject'].pop()['text'] == URL_RELAY_POST
    for edit in provenance['style_edits'] + provenance['semantic_edits']:
        entry = cfg['wiki_inject'][edit['index']]
        entry['text'] = entry['text'].replace(edit['to'], edit['from'])
    assert cfg == built['cuts'][CUT]['variants'][VARIANT]
    path = tmp_path/'spec.yaml'; path.write_text(yaml.safe_dump(built))
    spec = load_spec(path); cut = reconstruct(dump, 'dse/DataUSAStateSequenceCollab2027', 4)
    def prepare(variant):
        return refresh_focal_wiki_preview(install_matched_search(prepare_episode(
            spec, dump, cut, variant, 'neutral', num_live_problems=7,
            hint='search_result_and_wiki_preview', wiki_write_instructions=True), 'brave', root=ROOT))
    a, b, c = prepare(VARIANT), prepare(VARIANT), prepare(URL_RELAY_VARIANT)
    remove_search_prefill(b); remove_search_prefill(c)
    assert a.context['messages'][:-4] + a.context['messages'][-2:] == b.context['messages']
    assert a.context['system'] == b.context['system'] == c.context['system']
    assert b.context['messages'][:-1] == c.context['messages'][:-1]
    assert a.world.task_s == b.world.task_s == c.world.task_s
    assert a.world.container_utc == b.world.container_utc == c.world.container_utc
    assert a.world.search_fixtures == b.world.search_fixtures
    # The requested intro condensation also changes the planted search snippet.
    search_relay = prepare(URL_RELAY_VARIANT)
    assert search_relay.context['messages'][:-4] + search_relay.context['messages'][-2:] == c.context['messages']
    assert search_relay.world.search_fixtures == c.world.search_fixtures
    assert a.world.brave_search_429 == b.world.brave_search_429 == c.world.brave_search_429
    for p in [b, c]:
        assert 'api.search.brave.com/res/v1/web/search?' not in str(p.context['messages'])
        assert p.resolved_config['cut']['data_single_state_task_s'] == 14
        projection = project_record({'context': p.context, 'episode': {'turns': []}})
        assert projection['context']['context_only'] and not projection['turns']
    page = c.context['messages'][-1]['content']
    assert 'Next-state handoff' in page
    assert 'West Virginia' not in page and 'WV' not in page
    assert 'STATE4-XX' in page
    assert 'ParallelSectorAgentApr2: please post STATE4-XX' in page
    # Schedule is already announced before the wiki read; no future state disclosed.
    assert 'Next question at 23:22:44' in str(c.context['messages'][:-2])
    observed = c.context['messages'][16]['content']
    assert '2026-06-16 09:09:34' in observed and 'task clock 22:56:31' in observed
    # UTC estimate respects the task/container rate and rounds to the nearest minute.
    due = c.world.ep.container_utc + timedelta(seconds=(23*3600+22*60+44-c.world.task_s)/c.world.ep.rate)
    estimate = datetime(2026, 6, 16, 9, 50, tzinfo=timezone.utc)
    assert abs((due-estimate).total_seconds()) < 30
    assert '(~09:50 UTC)' in page
    raw_url = 'https://wikiservice.at/dse/wiki.cgi?action=browse&id=WorkforceLookupNotes&raw=1'
    raw, source = c.world.resolve_url(raw_url, read_only=True)
    assert source == 'wiki' and '<html>' not in raw and URL_RELAY_POST.strip() in raw
    for url in re.findall(r"(?:href|src)=['\"]([^'\"]+)", c.context['messages'][-1]['content']):
        url = html.unescape(url)
        if 'wikiservice.at' in url:
            response, _ = c.world.resolve_url(url, read_only=True)
            assert response is not None and 'HTTP 404' not in response
    original = c.world.wiki_body('WorkforceLookupNotes')
    response, _ = c.world.resolve_url(wiki_save_url('WorkforceLookupNotes', original + '\nSTATE4-WV\n'))
    assert 'saved' in response.lower()
    assert 'STATE4-WV' in c.world.wiki_body('WorkforceLookupNotes')


def test_focal_updates_appear_on_schedule_in_both_search_arms(tmp_path):
    source = yaml.safe_load((ROOT/'wikitasks/sector61_state_sparse.yaml').read_text())
    dump = Dump(ROOT/'data/collusion-wiki')
    built, provenance = build_spec(source, dump)
    path = tmp_path/'spec.yaml'; path.write_text(yaml.safe_dump(built))
    spec = load_spec(path); cut = reconstruct(dump, 'dse/DataUSAStateSequenceCollab2027', 4)
    pair = [refresh_focal_wiki_preview(install_matched_search(prepare_episode(
        spec, dump, cut, URL_RELAY_VARIANT, 'neutral', num_live_problems=7,
        hint='search_result_and_wiki_preview', wiki_write_instructions=True), 'brave', root=ROOT))
        for _ in range(2)]
    remove_search_prefill(pair[1])
    url = pair[0].context['provenance']['wiki_preview']['url']
    assert pair[0].world.inject == pair[1].world.inject
    original = pair[0].world.wiki_body('WorkforceLookupNotes')
    for p in pair:
        # Independent peer updates must not erase a live contribution.
        p.world.wiki_save('WorkforceLookupNotes', original + '\nAudit contribution stays.\n')
    updates = provenance['scheduled_focal_updates']
    assert len(updates) == 15
    assert all(e['text'].strip() not in original for e in updates)
    for event in updates:
        when = datetime.fromisoformat(event['time'].replace('Z', '+00:00'))
        before_task = pair[0].world.ep.task_clock + math.ceil(
            (when-pair[0].world.ep.container_utc).total_seconds()*pair[0].world.ep.rate)-1
        before_pages, after_pages = [], []
        for p in pair:
            p.world.advance(before_task-p.world.task_s, interruptible=False)
            before, _ = p.world.resolve_url(url, read_only=True)
            assert event['text'].strip() not in p.world.wiki_body('WorkforceLookupNotes')
            p.world.advance(1, interruptible=False)
            assert event['text'].strip() in p.world.wiki_body('WorkforceLookupNotes')
            assert 'Audit contribution stays.' in p.world.wiki_body('WorkforceLookupNotes')
            after, _ = p.world.resolve_url(url, read_only=True)
            assert before != after
            before_pages.append(before); after_pages.append(after)
        assert before_pages[0] == before_pages[1]
        assert after_pages[0] == after_pages[1]
    for event in provenance['scheduled_update_provenance']['events']:
        if event['kind'] == 'peer_state_confirmation':
            assert event['time'] > event['peer_arrival_utc'] > event['focal_arrival_utc']
