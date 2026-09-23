from copy import deepcopy
from types import SimpleNamespace
import json

import pytest

from ai_collusion.client import ModelConfig
from ai_collusion.env import parse_call
from ai_collusion.native_tools import TOOL_DEFINITIONS, provider_messages
from ai_collusion.tool_protocol import ToolProtocolError, generate_for_turn


@pytest.mark.parametrize('text,arg', [
    ('wait("300")', '300'),
    ('I can use web_fetch() to inspect the page.\nwait("300")', '300'),
    ('Consider shell() or answer() later.\n  wait("300")', '300'),
    ('Saving cache now.\nshell("printf hello")', 'printf hello'),
    ('web_fetch("https://example.org")', 'https://example.org'),
    ('shell("python - <<\'EOF\'\\nprint(1)\\nEOF")', "python - <<'EOF'\nprint(1)\nEOF"),
])
def test_valid_standalone_call_with_optional_preceding_notes(text, arg):
    assert parse_call(text).arg == arg


@pytest.mark.parametrize('text', [
    'wait("300") m', 'Saving cache now.shell("printf hello")',
    '<tool_call>web_fetch("https://example.org")</tool_call>',
    'shell("literal\nnewline")',
    'wait("60")wait("60")', 'wait("60")\nwait("60")',
    'wait("60")\nwait("', 'shell("unfinished',
    'wait("60", "120")', 'wait(__import__("os").system("false"))',
])
def test_ambiguous_truncated_or_expression_calls_are_not_executed(text):
    assert parse_call(text) is None


def test_text_syntax_errors_are_visible_and_retry_without_execution(tmp_path, monkeypatch):
    from test_preparation import scenario
    from ai_collusion.preparation import prepare_episode
    from ai_collusion.episode import play
    spec, dump, cuts = scenario(tmp_path)
    prepared = prepare_episode(spec, dump, cuts[4], None, 'neutral')
    before = prepared.world.task_s
    inputs = []
    responses = iter(['wait("300") m', 'wait("300") m', 'wait("3")'])
    def generate(model, system, messages, *args, **kwargs):
        inputs.append(deepcopy(messages))
        return {'text': next(responses), 'finish_reason': 'stop'}
    monkeypatch.setattr('ai_collusion.episode.generate', generate)
    body = play(model=ModelConfig('test', 'stub', 'test'), world=prepared.world,
                context=prepared.context, prepared=prepared, temperature=None, seed=0, max_turns=3)
    turns = body['episode']['turns']
    assert [t['source'] for t in turns] == ['error', 'error', 'clock']
    assert all(t['call'] is None and t['env_call'] is None for t in turns[:2])
    assert turns[0]['task_clock'] == turns[1]['task_clock']
    assert prepared.world.task_s == before + round(3 * prepared.world.ep.rate)
    assert inputs[1][-2]['content'] == 'wait("300") m'
    assert 'nothing executed. Retry' in inputs[1][-1]['content']
    assert len(inputs) == 3


def native_config():
    return ModelConfig('test', 'openai', 'test', max_tokens=4096,
        extra_body={'tools': TOOL_DEFINITIONS, 'tool_choice': 'required'})


def good_response():
    return {'text': 'wait("3600")', 'provider_text': '', 'finish_reason': 'tool_calls',
        'tool_mode': 'native', 'tool_call': {'tool': 'wait', 'arg': '3600', 'raw': 'wait("3600")'}}


def test_native_history_has_one_action_and_keeps_source_unchanged():
    response = good_response()
    messages = [{'role': 'assistant', 'content': 'wait("3600")\nwait("3600")',
                 'provider_text': 'Waiting.\nwait("3600")', 'tool_call': response['tool_call']},
                {'role': 'user', 'content': 'RESULT slept'}]
    original = deepcopy(messages)
    payload = provider_messages(messages)
    assert messages == original
    assert payload[0]['content'] == 'Waiting.'
    assert len(payload[0]['tool_calls']) == 1
    assert payload[1]['role'] == 'tool'


def test_truncation_retries_before_execution_with_a_larger_recorded_budget():
    received = []
    def generate(model, system, messages, temperature, seed):
        received.append((model.max_tokens, deepcopy(messages)))
        if len(received) == 1:
            return {**good_response(), 'finish_reason': 'length', 'provider_text': 'unfinished'}
        return good_response()
    result = generate_for_turn(native_config(), 'task', [{'role': 'user', 'content': 'go'}], None, 0, generate)
    assert [r[0] for r in received] == [4096, 8192]
    assert 'unfinished' not in str(received[1][1])
    assert result['generation_attempts'][0]['issue'] == 'truncated'
    assert result['generation_attempts'][0]['response']['provider_text'] == 'unfinished'


def test_repeated_output_is_not_fed_back_or_given_more_tokens():
    received = []
    def generate(model, system, messages, temperature, seed):
        received.append((model.max_tokens, messages))
        if len(received) == 1:
            return {**good_response(), 'provider_text': 'wait("10")\n' * 20}
        return good_response()
    result = generate_for_turn(native_config(), 'task', [], None, 0, generate)
    assert [r[0] for r in received] == [4096, 4096]
    assert 'wait("10")' not in str(received[1][1])
    assert result['generation_attempts'][0]['issue'] == 'repetition'


@pytest.mark.parametrize('bad', [
    {'text': 'wait("3600")', 'finish_reason': 'stop'},  # No actual native call.
    {**good_response(), 'tool_call': {'tool': 'wait', 'arg': '0', 'raw': 'wait("0")'}},
    {**good_response(), 'tool_call': {'tool': 'wait', 'arg': 'NaN', 'raw': 'wait("NaN")'}},
])
def test_recovery_is_bounded_and_retains_all_failed_attempts(bad):
    with pytest.raises(ToolProtocolError) as exc:
        generate_for_turn(native_config(), 'task', [], None, 0, lambda *args: bad)
    assert len(exc.value.attempts) == 4


def test_responses_transport_uses_native_tools_and_recovers_the_declared_call(monkeypatch):
    import openai
    from ai_collusion.client import generate
    captured = {}
    response = SimpleNamespace(output=[SimpleNamespace(type='function_call', name='wait', arguments='{"arg":"3600"}')],
                               status='completed', usage=None, model_dump=lambda: {})
    def create(**kwargs):
        captured.update(kwargs)
        return response
    monkeypatch.setattr(openai, 'OpenAI', lambda **kwargs: SimpleNamespace(responses=SimpleNamespace(create=create)))
    model = native_config()
    model.transport = 'responses'
    result = generate(model, 'task', [
        {'role': 'assistant', 'content': 'wait("10")'}, {'role': 'user', 'content': 'RESULT slept'}],
        temperature=None, seed=None)
    assert captured['tools'][0]['type'] == 'function'
    assert captured['tools'][0]['name'] == 'shell'
    assert 'tools' not in captured['extra_body']
    assert [item['type'] for item in captured['input']] == ['function_call', 'function_call_output']
    assert result['tool_call']['arg'] == '3600'


def test_provider_error_inside_success_response_is_retried(monkeypatch):
    import openai
    from ai_collusion.client import generate
    calls = []
    message = SimpleNamespace(content='ok', model_extra={}, tool_calls=[])
    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason='error' if len(calls)==1 else 'stop')],
                               usage=None, model_dump=lambda: {})
    monkeypatch.setattr(openai, 'OpenAI', lambda **kwargs: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    monkeypatch.setattr('ai_collusion.client.time.sleep', lambda _: None)
    assert generate(ModelConfig('test','openai','test'), 'task', [], temperature=None, seed=None)['text'] == 'ok'
    assert len(calls) == 2


def test_only_transient_402_is_retryable():
    from ai_collusion.client import _retryable
    for message, expected in [('in_flight_budget_exhausted', True), ('Insufficient credits', False)]:
        error = RuntimeError(message)
        error.status_code = 402
        error.body = {"error": {"metadata": {"reason": message}}}
        assert _retryable(error) is expected


def test_wait_reminder_and_zero_wait_rejection_use_real_world(tmp_path, monkeypatch):
    from test_preparation import scenario
    from ai_collusion.preparation import prepare_episode
    from ai_collusion.episode import play
    from ai_collusion.env import step
    spec, dump, cuts = scenario(tmp_path)
    prepared = prepare_episode(spec, dump, cuts[4], None, 'neutral')
    before = prepared.world.task_s
    result = step(prepared.world, 'wait("0")')
    assert result.source == 'error' and 'positive' in result.result
    assert prepared.world.task_s == before
    monkeypatch.setattr('ai_collusion.episode.generate', lambda *a, **kw: {'text': 'wait("1")'})
    body = play(model=ModelConfig('test','stub','test'), world=prepared.world, context=prepared.context,
                prepared=prepared, temperature=None, seed=0, max_turns=9)
    assert 'longer wait returns early' in body['episode']['turns'][7]['result']
    assert 'longer wait returns early' in body['messages_final'][-3]['content']


def test_truncated_write_does_not_mutate_world_before_recovery(tmp_path, monkeypatch):
    from test_preparation import scenario
    from ai_collusion.preparation import prepare_episode
    from ai_collusion.episode import play
    from ai_collusion.wiki import wiki_save_url
    spec, dump, cuts = scenario(tmp_path)
    prepared = prepare_episode(spec, dump, cuts[4], None, 'neutral')
    url = wiki_save_url('RecoveryBoundary', 'must not be published')
    responses = iter([
        {'text': f'web_fetch({json.dumps(url)})', 'finish_reason': 'length'},
        {'text': 'wait("1")', 'finish_reason': 'stop'},
    ])
    monkeypatch.setattr('ai_collusion.episode.generate', lambda *a, **kw: next(responses))
    body = play(model=ModelConfig('test','stub','test'), world=prepared.world, context=prepared.context,
                prepared=prepared, temperature=None, seed=0, max_turns=1)
    assert not prepared.world.own_posts
    assert body['episode']['turns'][0]['call']['tool'] == 'wait'
    assert len(body['episode']['turns'][0]['generation_attempts']) == 2


def test_native_notes_preserve_inline_tool_mentions():
    from ai_collusion.native_tools import clean_notes
    notes = 'I can use web_fetch() to inspect the page.'
    assert clean_notes(notes + '\nwait("300")') == notes
    assert clean_notes(notes) == notes


def test_configured_retries_can_recover_after_four_rejections_without_replaying_them():
    from dataclasses import replace
    model = replace(native_config(), tool_attempts=8)
    inputs = []
    def generate(config, system, messages, *args):
        inputs.append(deepcopy(messages))
        if len(inputs) < 6:
            return {'text': 'untrusted rejected output', 'finish_reason': 'tool_calls',
                    'tool_error': 'Expected one native tool call; received 2'}
        return good_response()
    result = generate_for_turn(model, 'task', [], None, 0, generate)
    assert len(result['generation_attempts']) == 6
    assert all(a['issue'] == 'native_call_missing' for a in result['generation_attempts'][:-1])
    assert all('untrusted rejected output' not in str(m) for m in inputs)
    assert all(len(m) == 1 for m in inputs[1:])  # Feedback stays bounded.
    assert '{"arg":"120"}' in inputs[-1][0]['content']


def test_configured_truncation_budget_grows_to_its_cap_and_stops():
    from dataclasses import replace
    model = replace(native_config(), max_tokens=16384, tool_retry_max_tokens=32768, tool_attempts=5)
    caps = []
    def generate(config, *args):
        caps.append(config.max_tokens)
        return {**good_response(), 'finish_reason': 'length'}
    with pytest.raises(ToolProtocolError, match='after 5 attempts') as exc:
        generate_for_turn(model, 'task', [], None, 0, generate)
    assert caps == [16384, 32768, 32768, 32768, 32768]
    assert len(exc.value.attempts) == 5


@pytest.mark.parametrize('field,value', [('tool_attempts', 0), ('tool_attempts', 17),
                                       ('tool_attempts', True), ('tool_retry_max_tokens', 0)])
def test_invalid_recovery_settings_fail_before_any_request(field, value):
    from dataclasses import replace
    def unexpected(*args):
        pytest.fail('invalid configuration must not call the provider')
    with pytest.raises(ValueError):
        generate_for_turn(replace(native_config(), **{field: value}), 'task', [], None, 0, unexpected)


def test_extra_protocol_retries_do_not_catch_authentication_stop():
    from dataclasses import replace
    from ai_collusion.auth_stop import AuthenticationStop
    calls = []
    def generate(*args):
        calls.append(1)
        raise AuthenticationStop()
    with pytest.raises(AuthenticationStop):
        generate_for_turn(replace(native_config(), tool_attempts=8), 'task', [], None, 0, generate)
    assert len(calls) == 1
