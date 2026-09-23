"""Simulated fetches must be charged after their request outcome is known."""
import json

import pytest

from ai_collusion.client import ModelConfig
from ai_collusion.data_cost import query_seconds
from ai_collusion.env import step
from tests.test_sparse_costly import fetch, prepared  # noqa: F401


@pytest.mark.parametrize('variant', ['sparse_costly', 'sparse_costly_v4', 'sparse_slow_data_v4'])
def test_unsupported_fetch_timeout_does_not_charge_full_query(prepared, monkeypatch, variant):
    w = prepared(variant=variant).world
    w.env_model = ModelConfig('env', 'stub', 'test')
    # Exact query shape from the failed GPT slow-data seed 1 rollout: separate
    # Year parameter is outside the deterministic resolver's supported semantics.
    url = w.table_url + '&Year=2015%2C2016%2C2017%2C2018%2C2019%2C2020'
    assert w.resolve_url(url, read_only=True) is None
    duration = query_seconds(url, w.table_url, w.table, w.data_single_state_s)
    assert duration > 20000
    query = {'url': url, 'start_s': 0, 'timeout_s': 30}
    monkeypatch.setattr('ai_collusion.env.generate', lambda *a, **kw: {
        'text': 'Error: request timed out after 30 seconds\n@@DATA_QUERIES '
                + json.dumps([query]) + '\n@@ELAPSED 30'})
    start = w.task_s
    result = fetch(w, url)
    assert result.source == 'env-model' and result.env_call.error is None
    assert result.elapsed == {'reported_s': 30, 'charged_s': 30, 'data_query_s': 30}
    assert abs(w.task_s - start - round(30 * w.ep.rate)) <= 1
    assert not result.done and not any(r.missed for r in w.ep.upcoming)
    assert not w.pending_fetches and not w.background_data_queries
    assert all(e.get('timed_out') is True for e in result.env_call.effects if 'data_query' in e)
    # The next question remains available rather than being skipped.
    step(w, 'wait("21600")')
    assert w.pending().n == 4


def test_unsupported_fetch_completion_keeps_deterministic_latency_floor(prepared, monkeypatch):
    w = prepared(variant='sparse_slow_data_v4').world
    w.env_model = ModelConfig('env', 'stub', 'test')
    url = w.table_url + '&Year=2015'
    query = {'url': url, 'start_s': 0}
    monkeypatch.setattr('ai_collusion.env.generate', lambda *a, **kw: {
        'text': 'Request completed\n@@DATA_QUERIES ' + json.dumps([query]) + '\n@@ELAPSED 1'})
    start = w.task_s
    result = fetch(w, url)
    duration = query_seconds(url, w.table_url, w.table, w.data_single_state_s)
    assert result.elapsed['charged_s'] >= duration
    assert abs(w.task_s - start - round(result.elapsed['charged_s'] * w.ep.rate)) <= 1
    assert result.done  # A completed foreground bulk request really takes this long.


def test_rejected_simulator_fetch_does_not_precharge_query_duration(prepared, monkeypatch):
    w = prepared(variant='sparse_slow_data_v4').world
    w.env_model = ModelConfig('env', 'stub', 'test')
    monkeypatch.setattr('ai_collusion.env.generate', lambda *a, **kw: {
        'text': 'Missing request ledger\n@@ELAPSED 30'})
    start = w.task_s
    result = fetch(w, w.table_url + '&Year=2015')
    assert result.source == 'env-error'
    assert w.task_s - start == round(w.ep.call_cost_s['web_fetch'] * w.ep.rate)
    assert not any(r.missed for r in w.ep.upcoming)
