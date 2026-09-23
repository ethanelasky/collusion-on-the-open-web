"""Daily request quota on the data endpoint (spec `data_daily_quota`)."""
import json
import re
from pathlib import Path

import pytest
import yaml

from ai_collusion.client import ModelConfig
from ai_collusion.data_cost import extract_queries
from ai_collusion.env import step
from ai_collusion.preparation import prepare_episode
from ai_collusion.wiki import Dump, load_spec, reconstruct
from experiments.data_quota import build_spec, matched_brave_prefill, variant_name
from tests.test_sparse_costly import fetch, prepared  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
QUOTA = 25


@pytest.fixture(scope='module')
def quota_prepared(tmp_path_factory):
    source = yaml.safe_load((ROOT / 'wikitasks/sector61_state_sparse.yaml').read_text())
    dump = Dump(ROOT / 'data/collusion-wiki')
    built, provenance = build_spec(source, dump, QUOTA)
    path = tmp_path_factory.mktemp('quota') / 'spec.yaml'
    path.write_text(yaml.safe_dump(built))
    spec = load_spec(path)
    cut = reconstruct(dump, 'dse/DataUSAStateSequenceCollab2027', 4)

    def make(variant=variant_name(QUOTA), seed=0):
        return prepare_episode(spec, dump, cut, variant, 'neutral', seed=seed, num_live_problems=7,
                               hint='search_result_and_wiki_preview', wiki_write_instructions=True)
    return make, provenance


def state_url(world, state):
    return world._state_fetch(state)[0]


def test_variant_changes_only_the_quota(quota_prepared):
    make, provenance = quota_prepared
    a, b = make(variant='sparse_slow_data_v7_coordination'), make()
    assert b.resolved_config['cut']['data_daily_quota'] == QUOTA
    cut_a = dict(a.resolved_config['cut']); cut_b = dict(b.resolved_config['cut'])
    cut_b.pop('data_daily_quota')
    assert cut_a == cut_b
    assert provenance['not_in_system_prompt'] and 'quota' not in a.context['system'].lower()
    assert 'quota' not in b.context['system'].lower()
    assert a.world.wiki_body(a.world.cut.page_name) == b.world.wiki_body(b.world.cut.page_name)


def test_prefilled_fetches_count_and_show_headers(quota_prepared):
    make, _ = quota_prepared
    p = make()
    results = [m['content'] for m in p.context['messages'] if m['role'] == 'user' and '--- GET https://api.datausa.io' in m['content']]
    assert len(results) == 3
    remaining = [int(re.search(r'X-RateLimit-Remaining: (\d+)', r).group(1)) for r in results]
    assert remaining == [QUOTA - 1, QUOTA - 2, QUOTA - 3]
    assert all(f'X-RateLimit-Limit: {QUOTA}' in r and 'HTTP 200 OK' in r for r in results)
    assert [r['source'] for r in p.world.data_requests] == ['prefill'] * 3
    assert p.world.quota_used() == 3 and p.world.quota_remaining() == QUOTA - 3
    # The prefilled headers carry the prefill clocks, all on the same UTC day as live play.
    assert {r['utc'][:10] for r in p.world.data_requests} == {'2026-06-16'}
    # Search and wiki results are untouched.
    others = [m['content'] for m in p.context['messages'] if m['role'] == 'user' and 'wikiservice.at' in m['content']]
    assert others and all('X-RateLimit' not in o for o in others)


def test_live_fetch_counts_then_endpoint_rejects_without_data(quota_prepared):
    make, _ = quota_prepared
    w = make().world
    w.data_daily_quota = 4  # three prefilled + one live
    result = fetch(w, state_url(w, 'West Virginia'))
    assert result.source == 'data' and 'X-RateLimit-Remaining: 0' in result.result
    assert 'Total Population' in result.result and w.quota_remaining() == 0
    before = w.task_s
    rejected = fetch(w, state_url(w, 'Idaho'))
    assert rejected.source == 'http-error' and rejected.result.split('\n')[1].startswith('HTTP 429')
    assert 'Retry-After' in rejected.result and 'Total Population' not in rejected.result
    assert json.loads(rejected.result.split('\n\n', 1)[1])['error'].startswith('Daily request limit of 4')
    assert rejected.elapsed['quota_rejected'] and rejected.elapsed['charged_s'] <= 3
    assert w.task_s - before <= round(3 * w.ep.rate)
    assert w.quota_used() == 4 and w.data_requests[-1]['counted'] is False
    report = w.data_quota_report()
    assert report['rejected'] == 1 and report['used_today'] == 4 and report['prefill_requests'] == 3


def test_bulk_fetch_counts_once_in_background_and_is_rejected_when_spent(quota_prepared):
    make, _ = quota_prepared
    w = make().world
    assert w.web_fetch_yield_s == 30
    pending = fetch(w, w.table_url)
    assert pending.source == 'data-pending' and len(w.pending_fetches) == 1
    assert w.quota_used() == 4
    w.data_daily_quota = 4
    rejected = fetch(w, w.table_url)
    assert rejected.result.split('\n')[1].startswith('HTTP 429') and len(w.pending_fetches) == 1
    assert w.quota_used() == 4


def test_shell_ledger_rejects_requests_beyond_the_allowance_in_start_order(quota_prepared):
    make, _ = quota_prepared
    w = make().world
    w.env_model = ModelConfig('env', 'stub', 'test')
    w.data_daily_quota = 5  # two left after prefill
    states = ['West Virginia', 'Idaho', 'Louisiana', 'Iowa']
    queries = [{'url': state_url(w, s), 'start_s': 3 - i} for i, s in enumerate(states)]  # reversed start order
    seen = {}
    def fake_generate(model, system, messages, **kw):
        seen['system'], seen['prompt'] = system, messages[-1]['content']
        return {'text': 'done\n@@DATA_QUERIES ' + json.dumps(queries) + '\n@@ELAPSED 30'}
    import ai_collusion.env as env
    orig = env.generate; env.generate = fake_generate
    try:
        result = step(w, 'shell("for s in WV ID LA IA; do curl ... ; done")')
    finally:
        env.generate = orig
    assert result.source == 'env-model' and result.env_call.error is None
    effects = [e for e in result.env_call.effects if 'data_query' in e]
    by_state = {s: e for s, e in zip(states, effects)}
    assert by_state['Iowa'].get('quota_rejected') is None and by_state['Louisiana'].get('quota_rejected') is None
    assert by_state['Idaho']['http_status'] == 429 and by_state['West Virginia']['http_status'] == 429
    assert by_state['Idaho']['duration_s'] == 1
    assert w.quota_used() == 5 and sum(r['status'] == 429 for r in w.data_requests) == 2
    assert 'DATA REQUEST QUOTA' in seen['system'] and 'remaining 2 at the start of this command' in seen['prompt']


def test_extract_queries_quota_is_pure():
    endpoint = 'https://api.datausa.io/tesseract/data.jsonrecords?cube=pums_5'
    states = {'Ohio': [1], 'Iowa': [2]}
    single = endpoint + '&include=State%3AOhio'
    text = 'ok\n@@DATA_QUERIES ' + json.dumps([
        {'url': single, 'start_s': 0}, {'url': single, 'start_s': 5, 'timeout_s': 0.5},
        {'url': 'https://example.org/other', 'start_s': 1, 'timeout_s': 2}]) + '\n'
    _, effects, finish = extract_queries(text, endpoint, states, 8, quota_remaining=1)
    assert effects[0].get('quota_rejected') is None and effects[1]['quota_rejected']
    assert effects[1]['duration_s'] == 0.5 and effects[2].get('quota_rejected') is None
    assert finish == 8


def test_unreliable_failures_still_consume_allowance(quota_prepared):
    make, _ = quota_prepared
    w = make().world
    w.query_failure_probability = 1
    w.web_fetch_yield_s = 0
    result = fetch(w, state_url(w, 'West Virginia'))
    assert 'HTTP 503' in result.result and w.quota_used() == 4
    assert w.data_requests[-1]['status'] == 503


def test_quota_requires_a_latency_model_and_is_off_by_default(prepared):
    w = prepared(variant='sparse_slow_data_v7').world
    assert w.data_daily_quota == 0 and not w.data_requests
    result = fetch(w, state_url(w, 'West Virginia'))
    assert result.result.split('\n')[1].startswith('--- GET') and 'X-RateLimit' not in result.result
    source = yaml.safe_load((ROOT / 'wikitasks/sector61_state_sparse.yaml').read_text())
    dump = Dump(ROOT / 'data/collusion-wiki')
    built, _ = build_spec(source, dump, 10)
    built['cuts']['dse/DataUSAStateSequenceCollab2027@4']['variants']['broken_quota'] = {
        **built['cuts']['dse/DataUSAStateSequenceCollab2027@4']['variants']['sparse_broken_v7'], 'data_daily_quota': 10}
    path = Path(w.spec._path).parent / '.pytest-broken-quota.yaml'
    path.write_text(yaml.safe_dump(built))
    try:
        spec = load_spec(path)
        cut = reconstruct(dump, 'dse/DataUSAStateSequenceCollab2027', 4)
        with pytest.raises(ValueError, match='data_daily_quota requires'):
            prepare_episode(spec, dump, cut, 'broken_quota', 'neutral')
    finally:
        path.unlink()


def test_matched_prefill_hook_keeps_quota_headers(quota_prepared):
    make, _ = quota_prepared
    p = matched_brave_prefill(make())
    assert p.context['provenance']['search']['provider'] == 'brave'
    results = [m['content'] for m in p.context['messages'] if m['role'] == 'user' and 'X-RateLimit-Remaining' in m['content']]
    assert len(results) == 3 and p.world.quota_used() == 3
