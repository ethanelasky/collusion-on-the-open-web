"""Cancellation returns a live question instead of skipping the whole episode."""
import json
import re

from ai_collusion.client import ModelConfig
from ai_collusion.env import step
from tests.test_sparse_costly import prepared  # noqa: F401


def _boundary(messages):
    match = re.search(r'SHELL EXECUTION BOUNDARY: (\{[^\n]+\})', messages[-1]['content'])
    return json.loads(match[1])


def test_waiting_for_bulk_children_cancels_and_model_can_answer(prepared, monkeypatch):
    w = prepared(variant='sparse_unreliable_v4').world
    w.env_model = ModelConfig('env', 'stub', 'test')
    earlier = {'data_query': 'earlier job', 'finishes_at': '2026-06-17T00:00:00Z'}
    w.background_data_queries.append(earlier)
    state_url = w._state_fetch('Connecticut')[0]
    queries = [{'url': state_url, 'start_s': 0}, {'url': w.table_url, 'start_s': 0}]
    captured = []
    def generate(*args, **kwargs):
        messages = args[2];captured.append(messages)
        limit = _boundary(messages)['limit_s']
        return {'text': 'State file written; bulk download still pending.\n'
                '@@WIKI_SAVE BeforeCancellation\ncompleted before waiting\n@@END\n'
                '@@SHELL_CANCELLED\n@@DATA_QUERIES ' + json.dumps(queries) + f'\n@@ELAPSED {round(limit)}'}
    monkeypatch.setattr('ai_collusion.env.generate', generate)
    expected_time = w.next_round().asked
    result = step(w, 'shell("curl state -o /tmp/state.json & curl bulk -o /tmp/all.json & wait; echo DONE")')
    assert w.task_s == expected_time and w.pending().n == 4 and not result.done
    assert not any(q.missed for q in w.ep.upcoming)
    assert 'Shell call cancelled' in result.result and 'question 4' in result.result
    assert '\nDONE' not in result.result and '@@SHELL_CANCELLED' not in result.result
    effects = [e for e in result.env_call.effects if 'data_query' in e]
    assert not effects[0].get('cancelled') and effects[1]['cancelled']
    assert effects[1]['duration_s'] == result.elapsed['charged_s']
    assert w.wiki_body('BeforeCancellation') == 'completed before waiting'
    assert w.background_data_queries == [earlier] and not w.pending_fetches
    assert 'CANCELLATION APPLIED' in w.env_thread[-1]['content']
    assert 'cancelled' in w.env_thread[-1]['content']
    assert w.data_query_attempts == 2
    step(w, 'answer("' + ','.join(map(str, w.expected('West Virginia'))) + '")')
    assert w.ep.upcoming[0].correct
    # The next simulator call sees cancellation, not a completed future result.
    monkeypatch.setattr('ai_collusion.env.generate', lambda *a, **kw: {
        'text': 'partial state file only\n@@DATA_QUERIES []\n@@ELAPSED 1'})
    follow = step(w, 'shell("ls /tmp")')
    assert 'CANCELLATION APPLIED' in follow.env_call.prompt
    assert w.background_data_queries == [earlier] and w.data_query_attempts == 2


def test_future_completed_result_retried_before_any_effect_is_applied(prepared, monkeypatch):
    w = prepared(variant='sparse_costly_v4').world
    w.env_model = ModelConfig('env', 'stub', 'test')
    queries = [{'url': w.table_url, 'start_s': 0}]
    calls = []
    def generate(*args, **kwargs):
        calls.append(args[2])
        if len(calls) == 1:
            return {'text': 'DONE_FUTURE\n@@WIKI_SAVE Forbidden\nshould never commit\n@@END\n'
                    '@@DATA_QUERIES ' + json.dumps(queries) + '\n@@ELAPSED 21632'}
        # The original boundary remains in the base request before feedback.
        limit = _boundary(calls[0])['limit_s']
        return {'text': 'Download cancelled\n@@SHELL_CANCELLED\n@@DATA_QUERIES '
                + json.dumps(queries) + f'\n@@ELAPSED {round(limit)}'}
    monkeypatch.setattr('ai_collusion.env.generate', generate)
    result = step(w, 'shell("curl bulk; publish_future_result")')
    assert result.env_call.error is None and len(result.env_call.generation_attempts) == 2
    assert 'boundary' in result.env_call.generation_attempts[0]['error']
    assert not w.own_posts and w.wiki_body('Forbidden') is None
    assert 'DONE_FUTURE' not in result.result and 'DONE_FUTURE' not in json.dumps(w.env_thread)
    assert w.pending().n == 4 and not w.ep.upcoming[0].missed


def test_sleep_without_data_is_cancelled_at_question_not_elapsed_cap(prepared, monkeypatch):
    w = prepared(variant='sparse_costly_v4').world
    w.env_model = ModelConfig('env', 'stub', 'test')
    def generate(*args, **kwargs):
        limit = _boundary(args[2])['limit_s']
        assert limit > w.ep.elapsed_cap_s
        return {'text': 'started\n@@SHELL_CANCELLED\n@@DATA_QUERIES []\n' + f'@@ELAPSED {round(limit)}'}
    monkeypatch.setattr('ai_collusion.env.generate', generate)
    result = step(w, 'shell("echo started; sleep 9000; echo never")')
    assert w.task_s == w.pending().asked and w.pending().n == 4
    assert 'started\nShell call cancelled' in result.result
    assert result.elapsed['charged_s'] > w.ep.elapsed_cap_s and not result.done


def test_question_during_call_overhead_cancels_before_simulation(prepared, monkeypatch):
    w = prepared(variant='sparse_costly_v4').world
    w.task_s = w.next_round().asked - 1
    monkeypatch.setattr('ai_collusion.env.generate', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('must not execute')))
    result = step(w, 'shell("touch /tmp/not_started")')
    assert result.source == 'shell-cancelled' and result.env_call is None
    assert w.task_s == w.pending().asked and not result.done
    assert not w.own_posts


def test_quick_background_launch_is_not_cancelled_by_later_questions(prepared, monkeypatch):
    w = prepared(variant='sparse_costly_v4').world
    w.env_model = ModelConfig('env', 'stub', 'test')
    query = {'url': w.table_url, 'start_s': 0, 'background': True}
    monkeypatch.setattr('ai_collusion.env.generate', lambda *a, **kw: {
        'text': 'job launched\n@@DATA_QUERIES ' + json.dumps([query]) + '\n@@ELAPSED 1'})
    result = step(w, 'shell("curl bulk -o /tmp/all.json &")')
    assert result.env_call.shell_boundary is None and len(w.background_data_queries) == 1
    assert 'cancelled' not in result.result
    step(w, 'wait("21600")')
    assert len(w.background_data_queries) == 1 and w.pending().n == 4


def test_old_variants_keep_blocking_shell_behavior(prepared, monkeypatch):
    w = prepared(variant='sparse_costly_v3').world
    assert not w.shell_interrupt_on_question
    w.env_model = ModelConfig('env', 'stub', 'test')
    query = {'url': w.table_url, 'start_s': 0}
    monkeypatch.setattr('ai_collusion.env.generate', lambda *a, **kw: {
        'text': 'completed\n@@DATA_QUERIES ' + json.dumps([query]) + '\n@@ELAPSED 21632'})
    assert step(w, 'shell("curl bulk & wait")').done
