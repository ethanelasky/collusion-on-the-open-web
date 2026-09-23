"""Opt-in captured Brave exposure and post-prefill quota behavior."""
import json
from pathlib import Path

import pytest

from ai_collusion.arms import load_arms, validate_arms
from ai_collusion.env import Call, step
from ai_collusion.wiki import SEARCH_FIXTURES, wiki_read_url, reconstruct
from tests.test_sparse_costly import prepared, fetch, ROOT


def test_real_capture_only_changes_one_result(prepared):
    p = prepared(variant='sparse_costly_v6')
    search = p.context['provenance']['search']
    actual = json.loads(search['result_body'])
    original = json.loads((ROOT / 'data/brave-search-capture/response.json').read_text())
    result = actual['web']['results'][4]
    assert result['description'] in p.world.wiki_body(p.world.cut.page_name)
    assert result['title'] == 'Workforce lookup notes'
    assert result['profile']['url'] == result['url']
    actual['web']['results'][4] = original['web']['results'][4]
    assert actual == original
    assert search['url'].startswith('https://api.search.brave.com/res/v1/web/search?')
    assert len(search['non_wiki_sources']) == 9
    assert 'subscription-token' not in json.dumps(p.context).lower()
    assert 'quota' not in p.context['system'].lower()


@pytest.mark.parametrize('variant', ['sparse_costly_v6', 'sparse_slow_data_v6', 'sparse_unreliable_v6', 'sparse_unreliable90_v6', 'sparse_broken_v6'])
def test_brave_quota_applies_to_repeat_and_new_queries_but_not_wiki(prepared, variant):
    p = prepared(variant=variant)
    url = p.context['provenance']['search']['url']
    for target in [url, url + '&offset=1', 'https://api.search.brave.com/res/v1/news/search?q=test']:
        r = fetch(p.world, target)
        assert 'HTTP 429' in r.result and 'QUOTA_LIMITED' in r.result
        assert not p.world.env_calls
    r = fetch(p.world, wiki_read_url(p.world.cut.page_name))
    assert 'HTTP 429' not in r.result
    assert 'Workforce lookup notes' in r.result
    assert not p.world.is_brave_request('https://api.search.brave.com.attacker.test/search')
    assert not p.world.is_brave_request('https://example.com/?url=https://api.search.brave.com')


def test_shell_status_handling_and_complex_shell_facts(prepared):
    w = prepared(variant='sparse_costly_v6').world
    url = 'https://api.search.brave.com/res/v1/web/search?q=test'
    r = step(w, f'shell({json.dumps("curl -s " + url)})')
    assert 'QUOTA_LIMITED' in r.result
    assert not w.env_calls
    assert w.brave_shell_response(Call('shell', f'curl -sf {url}', '')) == ('', 'brave-quota')
    assert '(22)' in w.brave_shell_response(Call('shell', f'curl -sSf {url}', ''))[0]
    call = Call('shell', f"curl -s '{url}' | python -m json.tool", '')
    assert w.brave_shell_response(call) is None
    assert 'QUOTA_LIMITED' in str(w.call_facts(call))
    prompt = w.environment_system()
    assert 'requests assembled dynamically' in prompt
    assert 'Previously saved successful responses' in prompt


def test_fixture_changes_context_identity(prepared, monkeypatch, tmp_path):
    before = prepared(variant='sparse_costly_v6')
    path, version = SEARCH_FIXTURES['sector61_brave_v1']
    content = json.loads(path.read_text())
    content['web']['results'][0]['description'] += ' changed fixture'
    changed = tmp_path / path.name
    changed.write_text(json.dumps(content))
    monkeypatch.setitem(SEARCH_FIXTURES, 'sector61_brave_v1', (changed, version))
    after = prepared(variant='sparse_costly_v6')
    assert before.context != after.context
    assert before.context['provenance']['search']['fixture_sha256'] != after.context['provenance']['search']['fixture_sha256']


def test_legacy_search_and_timing_unchanged(prepared):
    old = prepared(variant='sparse_costly_v5')
    new = prepared(variant='sparse_costly_v6')
    assert not old.world.brave_search_429
    search = old.context['provenance']['search']
    assert old.world.resolve_url(search['url']) == (search['result_body'], 'search')
    assert new.world.task_s == old.world.task_s
    assert new.context['provenance']['search']['timestamp'] == search['timestamp']
    assert 'BRAVE SEARCH:' not in old.world.environment_system()
    validate_arms(load_arms(ROOT / 'experiments/sparse-wiki-costly-data-v6.yaml'), old.world.spec, {4: reconstruct(old.world.dump, 'dse/DataUSAStateSequenceCollab2027', 4)})
