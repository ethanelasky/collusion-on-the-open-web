"""Admission, cross-tool state, and fail-closed simulator checks for cooldown arms."""
import json
import copy
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from ai_collusion.client import ModelConfig
from ai_collusion.data_cooldown import admission
from ai_collusion.data_cost import extract_queries
from ai_collusion.env import step
from ai_collusion.wiki import wiki_read_url
from tests.test_data_quota import quota_prepared, state_url
from tests.test_sparse_costly import fetch


@pytest.fixture
def world(quota_prepared):
    make, _ = quota_prepared
    w = make().world
    w.data_request_cooldown_task_s = 1500
    w.env_model = ModelConfig('env', 'stub', 'test')
    return w


def reply(queries, output='ok', elapsed=30):
    return {'text': output + '\n@@DATA_QUERIES ' + json.dumps(queries) + f'\n@@ELAPSED {elapsed}'}


def query(w, state, start=0, status=200, **kwargs):
    return {'url': state_url(w, state), 'start_s': start, 'http_status': status, **kwargs}


def shell(w):
    return step(w, 'shell("python /tmp/fetch.py")')


def test_direct_shared_across_states_and_no_accumulated_credit(world):
    w = world
    first = fetch(w, state_url(w, 'Idaho'))
    assert 'Total Population' in first.result and 'X-Request-Cooldown-Seconds' in first.result
    assert w.quota_used() == 4
    rejected = fetch(w, state_url(w, 'Louisiana'))
    assert 'HTTP 429' in rejected.result and 'Retry-After' in rejected.result
    assert 'Total Population' not in rejected.result and w.quota_used() == 4
    w.advance(6000)  # Four intervals of idle time still earn only one request.
    assert fetch(w, state_url(w, 'Louisiana')).source == 'data'
    assert fetch(w, state_url(w, 'Iowa')).elapsed['cooldown_rejected']


def test_boundary_and_future_reservations():
    states = {'Idaho': [1]}; url = 'https://example.org/data?include=State:Idaho'
    assert admission(url, states, 110, 10, [100])['cooldown_admitted']
    assert admission(url, states, 109, 10, [100])['retry_after_s'] == 1
    assert admission(url, states, 90, 10, [100])['cooldown_admitted']
    assert admission(url, states, 95, 10, [100])['retry_after_s'] == 15
    assert admission(url, states, 1000, 10, [100])['cooldown_admitted']


@pytest.mark.parametrize('suffix', ['', '&Year=2015', '&include=State%3AIdaho%3BIowa'])
def test_bulk_cannot_bypass_capacity(world, suffix):
    w = world
    rejected = fetch(w, w.table_url + suffix)
    assert rejected.elapsed['cooldown_reason'] == 'single_state_required'
    assert 'Total Population' not in rejected.result and w.quota_used() == 3
    assert not w.pending_fetches and not w.env_calls


def test_parallel_and_retry_ledger_spends_one_slot(world, monkeypatch):
    w = world
    qs = [query(w, 'Idaho'), query(w, 'Iowa', status=429), query(w, 'Alabama', start=1, status=429)]
    monkeypatch.setattr('ai_collusion.env.generate', lambda *a, **kw: reply(qs))
    result = shell(w)
    assert not result.env_call.error
    assert sum(e.get('cooldown_admitted', False) for e in w.data_cooldown_events) == 1
    assert w.quota_used() == 4
    assert fetch(w, state_url(w, 'Louisiana')).elapsed['cooldown_rejected']


def test_direct_then_shell_uses_same_slot(world, monkeypatch):
    fetch(world, state_url(world, 'Idaho'))
    monkeypatch.setattr('ai_collusion.env.generate', lambda *a, **kw: reply([query(world, 'Iowa', status=429)]))
    result = shell(world)
    assert not result.env_call.error
    assert result.env_call.effects[-1].get('cooldown_rejected') or any(e.get('cooldown_rejected') for e in result.env_call.effects)
    assert world.quota_used() == 4


def test_denied_success_is_retried_without_effects(world, monkeypatch):
    w = world
    fetch(w, state_url(w, 'Idaho'))
    replies = iter([reply([query(w, 'Iowa')], 'invented success'), reply([query(w, 'Iowa', status=429)], 'HTTP 429')])
    monkeypatch.setattr('ai_collusion.env.generate', lambda *a, **kw: next(replies))
    result = shell(w)
    assert not result.env_call.error and len(result.env_call.generation_attempts) == 2
    assert 'invented success' not in result.result and w.quota_used() == 4


def test_false_429_with_data_fails_without_leaking_or_saving(world, monkeypatch):
    w = world
    fetch(w, state_url(w, 'Idaho'))
    output = 'HTTP 429\n' + json.dumps(w.table['Iowa'])
    monkeypatch.setattr('ai_collusion.env.generate', lambda *a, **kw: reply([query(w, 'Iowa', status=429)], output))
    result = shell(w)
    assert result.end_reason == 'environment_error' and result.result == ''
    assert len(result.env_call.generation_attempts) == 3
    assert w.quota_used() == 4 and len(w.data_cooldown_events) == 1


def test_background_launch_reserves_future_slots_and_only_completed_data_is_visible(world, monkeypatch):
    w = world
    interval = w.data_request_cooldown_task_s / w.ep.rate
    qs = [query(w, 'Idaho', background=True), query(w, 'Iowa', start=interval, background=True)]
    monkeypatch.setattr('ai_collusion.env.generate', lambda *a, **kw: reply(qs, '1234', elapsed=4))
    result = shell(w)
    assert not result.env_call.error and len(w.background_data_queries) == 2
    assert len(w.cooldown_context()['reservations']) == 2
    # A file read cannot reveal the queued Iowa result before completion.
    monkeypatch.setattr('ai_collusion.env.generate', lambda *a, **kw: reply([], json.dumps(w.table['Iowa']), elapsed=4))
    early = shell(w)
    assert early.end_reason == 'environment_error' and early.result == ''
    w.advance(1600)
    # Once completed, a cache read consumes no new allowance.
    later = shell(w)
    assert not later.env_call.error and str(w.table['Iowa'][0]) in later.result
    assert w.quota_used() == 5


def test_timeout_consumes_slot_and_wiki_is_exempt(world, monkeypatch):
    monkeypatch.setattr('ai_collusion.env.generate', lambda *a, **kw: reply([query(world, 'Idaho', timeout_s=1)], 'timeout', elapsed=4))
    assert not shell(world).env_call.error
    assert fetch(world, state_url(world, 'Idaho')).elapsed['cooldown_rejected']
    used = world.quota_used()
    assert fetch(world, wiki_read_url('WorkforceLookupNotes')).source == 'wiki'
    assert world.quota_used() == used


def test_missing_or_wrong_status_fails_closed_and_default_ledger_is_unchanged(world):
    w = world; q = {'url': state_url(w, 'Idaho'), 'start_s': 0}
    text = reply([q])['text']
    assert len(extract_queries(text, w.table_url, w.table, w.data_single_state_s)[1]) == 1
    with pytest.raises(ValueError, match='admission mismatch'):
        extract_queries(text, w.table_url, w.table, w.data_single_state_s, cooldown=w.cooldown_context())


def test_cooldown_does_not_reset_at_midnight_or_require_daily_quota(world):
    w = world
    w.data_daily_quota = 0
    w.ep = replace(w.ep, container_utc=datetime(2026, 6, 16, 23, 59, 50, tzinfo=timezone.utc), task_clock=w.task_s)
    assert fetch(w, state_url(w, 'Idaho')).source == 'data'
    assert w.container_utc.day == 17
    assert fetch(w, state_url(w, 'Iowa')).elapsed['cooldown_rejected']


def test_reported_time_cannot_uncap_pending_cache_completion(world, monkeypatch):
    w = world
    # A background job is still pending after the simulator's elapsed-time cap.
    w.data_cooldown_events.append({'url': state_url(w, 'Iowa'), 'cooldown_admitted': True,
                                  'cooldown_start_utc_s': w.container_utc.timestamp() + 1100,
                                  'duration_s': 22, 'http_status': 200})
    w.shell_interrupt_on_question = False
    monkeypatch.setattr('ai_collusion.env.generate', lambda *a, **kw: reply([], json.dumps(w.table['Iowa']), elapsed=1200))
    assert shell(w).end_reason == 'environment_error'


def test_cancelled_future_requests_do_not_reserve_slots(world, monkeypatch):
    w = world
    # Make the next question ten container seconds away.
    next_round = w.next_round()
    next_round.asked = w.task_s + 7
    def response(*a, **kw):
        return reply([query(w, 'Idaho', start=30)], elapsed=11) | {
            'text': reply([query(w, 'Idaho', start=30)], elapsed=11)['text'] + '\n@@SHELL_CANCELLED'}
    monkeypatch.setattr('ai_collusion.env.generate', response)
    assert shell(w).end_reason == 'environment_error'
    assert not w.data_cooldown_events and w.quota_used() == 3


def test_new_variant_changes_only_cooldown_setting(quota_prepared, tmp_path):
    import yaml
    from pathlib import Path
    from ai_collusion.preparation import prepare_episode
    from ai_collusion.wiki import Dump, load_spec, reconstruct
    from experiments.request_cooldown import build_spec, VARIANT, ARM
    from experiments.restored_coordination import CUT
    from experiments.data_quota import variant_name
    root = Path(__file__).resolve().parents[1]
    dump = Dump(root / 'data/collusion-wiki')
    built, _ = build_spec(yaml.safe_load((root / 'wikitasks/sector61_state_sparse.yaml').read_text()), dump)
    variants = built['cuts'][CUT]['variants']
    assert variants[VARIANT] == {**variants[variant_name(25)], 'data_request_cooldown_task_s': 1500}
    path = tmp_path / 'spec.yaml'; path.write_text(yaml.safe_dump(built))
    spec = load_spec(path); cut = reconstruct(dump, 'dse/DataUSAStateSequenceCollab2027', 4)
    a = prepare_episode(spec, dump, cut, VARIANT, 'neutral', num_live_problems=7,
                        hint=ARM.hint, wiki_write_instructions=True)
    b = quota_prepared[0]()
    assert a.context == b.context
    assert a.world.data_request_cooldown_task_s == 1500
    assert not b.world.cooldown_active()
    assert a.world.cooldown_context()['reservations'] == []


@pytest.mark.parametrize('value', [-1, True, 0.5, '1500'])
def test_invalid_setting_rejected(quota_prepared, value):
    from ai_collusion.env import make_world
    w = quota_prepared[0]().world
    w.spec = copy.deepcopy(w.spec)
    from experiments.restored_coordination import CUT
    from experiments.data_quota import variant_name
    w.spec.cuts[CUT]['variants'][variant_name(25)]['data_request_cooldown_task_s'] = value
    with pytest.raises(ValueError, match='data_request_cooldown_task_s'):
        make_world(w.spec, w.dump, replace(w.cut, page_id='dse/DataUSAStateSequenceCollab2027'),
                   variant_name(25), 'neutral', None, 0)
