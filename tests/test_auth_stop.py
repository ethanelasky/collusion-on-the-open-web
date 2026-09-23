"""Authentication failures abort batches without making further API requests."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
import threading

import anthropic
import httpx
import httpx2
import openai
import pytest

from ai_collusion import auth_stop, client
from ai_collusion.episode import run_episodes
from ai_collusion.request_pool import RequestPool
from test_episode_provenance import run_inputs


@pytest.fixture(autouse=True)
def isolated_stop(monkeypatch):
    # Production has no reset API: a stopped process must be restarted.
    existing_threads = set(threading.enumerate())
    monkeypatch.setattr(auth_stop, '_STOP', threading.Event())
    monkeypatch.delenv('AI_COLLUSION_REQUEST_POOL_DIR', raising=False)
    monkeypatch.delenv('AI_COLLUSION_REQUEST_POOL_SIZE', raising=False)
    yield
    # Realtime workers may outlive an aborted rollout. Settle mocked requests
    # before restoring SDK patches or the process-wide authentication latch.
    for thread in set(threading.enumerate()) - existing_threads:
        if thread.name.startswith('color-clock-'):
            thread.join(timeout=5)
            assert not thread.is_alive(), 'mock realtime request did not settle'


@pytest.fixture
def unauthorized_api(monkeypatch):
    calls = []

    for module, name in [(openai, 'OpenAI'), (anthropic, 'Anthropic')]:
        real = getattr(module, name)
        http = httpx2 if module is anthropic else httpx

        def factory(_real=real, _http=http, **kwargs):
            def handler(request):
                calls.append(request)
                return _http.Response(401, json={'error': {
                    'type': 'authentication_error', 'message': 'secret-provider-body'}})
            return _real(**kwargs, http_client=_http.Client(transport=_http.MockTransport(handler)))

        monkeypatch.setattr(module, name, factory)
    monkeypatch.setenv('TEST_AUTH_KEY', 'secret-test-key')
    return calls


@pytest.mark.parametrize('transport', ['openai', 'responses', 'anthropic'])
def test_real_sdk_401_stops_all_models_without_retry(transport, unauthorized_api, monkeypatch, caplog):
    waits = []
    monkeypatch.setattr(client.time, 'sleep', waits.append)
    cfg = client.ModelConfig('test', transport, 'test', base_url='https://example.test/v1',
                             api_key_env='TEST_AUTH_KEY', retries=8)
    for model in [cfg, replace(cfg, name='another-model')]:
        with pytest.raises(auth_stop.AuthenticationStop, match='401 Unauthorized'):
            client.generate(model, 'system', [], temperature=None, seed=None)
    assert len(unauthorized_api) == 1
    assert waits == []
    assert 'FATAL' in caplog.text
    assert 'secret-provider-body' not in caplog.text
    assert 'secret-test-key' not in caplog.text


@pytest.mark.parametrize('with_message', [False, True])
def test_embedded_401_also_stops_even_with_a_partial_message(with_message):
    response = openai.types.chat.ChatCompletion.model_construct(
        id='local', choices=[openai.types.chat.chat_completion.Choice.model_construct(
            message=openai.types.chat.ChatCompletionMessage.model_construct(content='partial'))]
        if with_message else [], error={'code': '401', 'message': 'secret-provider-body'})
    with pytest.raises(auth_stop.AuthenticationStop):
        client._with_retries(client.ModelConfig('test', 'openai', 'test'),
                             lambda: client._validate_chat_response(response))
    with pytest.raises(auth_stop.AuthenticationStop):
        client._with_retries(client.ModelConfig('other', 'openai', 'test'),
                             lambda: pytest.fail('must not reach API'))


def test_waiting_and_queued_workers_never_send_after_401(tmp_path, monkeypatch):
    monkeypatch.setenv('AI_COLLUSION_REQUEST_POOL_DIR', str(tmp_path))
    monkeypatch.setenv('AI_COLLUSION_REQUEST_POOL_SIZE', '1')
    entered, release, waiting = threading.Event(), threading.Event(), threading.Event()
    calls = []
    cfg = client.ModelConfig('test', 'openai', 'test')

    def rejected():
        calls.append('first')
        entered.set()
        assert release.wait(5)
        error = RuntimeError('secret-provider-body')
        error.status_code = 401
        raise error

    @contextmanager
    def admission():
        waiting.set()
        yield

    def blocked():
        with client.request_control(attempt_context=admission):
            return client._with_retries(cfg, lambda: calls.append('unexpected'))

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(client._with_retries, cfg, rejected)
        try:
            assert entered.wait(5)
            second = executor.submit(blocked)
            assert waiting.wait(5)
            queued = executor.submit(blocked)
        finally:
            release.set()
        for future in [first, second, queued]:
            with pytest.raises(auth_stop.AuthenticationStop):
                future.result(timeout=5)
    assert calls == ['first']
    assert RequestPool(tmp_path, 1).snapshot()['active_requests'] == 0
    assert (tmp_path / auth_stop.MARKER).read_text() == auth_stop.MESSAGE + '\n'


@pytest.mark.parametrize('source', ['evaluee', 'environment'])
@pytest.mark.parametrize('workers', [1, 3])
def test_episode_batch_propagates_fatal_exit(source, workers, tmp_path, monkeypatch,
                                          unauthorized_api, capsys):
    inputs = run_inputs(tmp_path)
    inputs.update(n_samples=5, workers=workers)
    monkeypatch.setenv('AI_COLLUSION_REQUEST_POOL_DIR', str(tmp_path / 'pool'))
    monkeypatch.setenv('AI_COLLUSION_REQUEST_POOL_SIZE', '1')
    if source == 'evaluee':
        inputs['models'] = [replace(inputs['models'][0], transport='openai',
                                   api_key_env='TEST_AUTH_KEY', base_url='https://example.test/v1')]
    else:
        inputs['models'] = [replace(inputs['models'][0], stub_text='shell("pwd")')]
        inputs['env_model'] = replace(inputs['env_model'], transport='openai',
                                      api_key_env='TEST_AUTH_KEY', base_url='https://example.test/v1')
    with pytest.raises(auth_stop.AuthenticationStop):
        run_episodes(**inputs)
    assert len(unauthorized_api) == 1
    assert 'wrote ' not in capsys.readouterr().err


def test_fatal_exit_is_nonzero_and_persists_across_processes(tmp_path):
    env = {**os.environ, 'AI_COLLUSION_REQUEST_POOL_DIR': str(tmp_path),
           'AI_COLLUSION_REQUEST_POOL_SIZE': '1'}
    code = '''
from ai_collusion import client
def call():
    print('API_ATTEMPT', flush=True)
    error = RuntimeError('secret-provider-body')
    error.status_code = 401
    raise error
try:
    client._with_retries(client.ModelConfig('test', 'openai', 'test'), call)
except Exception:
    print('INCORRECTLY_SWALLOWED')
'''
    for expected_calls in [1, 0]:
        result = subprocess.run([sys.executable, '-c', code], env=env,
                                cwd=Path(__file__).resolve().parents[1],
                                text=True, capture_output=True, timeout=10)
        assert result.returncode == 1
        assert result.stdout.count('API_ATTEMPT') == expected_calls
        assert 'INCORRECTLY_SWALLOWED' not in result.stdout
        assert '401 Unauthorized' in result.stderr
        assert 'secret-provider-body' not in result.stderr


def test_cannot_resume_in_same_process_by_deleting_marker(tmp_path, monkeypatch):
    monkeypatch.setenv('AI_COLLUSION_REQUEST_POOL_DIR', str(tmp_path))
    with pytest.raises(auth_stop.AuthenticationStop):
        auth_stop.trip()
    (tmp_path / auth_stop.MARKER).unlink()
    with pytest.raises(auth_stop.AuthenticationStop):
        auth_stop.check()


@pytest.mark.parametrize('runner', ['batch', 'campaign', 'realtime_campaign'])
def test_color_runners_do_not_swallow_authentication_stop(runner, tmp_path, monkeypatch,
                                                        unauthorized_api):
    from experiments.color_game.batch import run_all_configs
    from experiments.color_game.campaign import run_campaign
    from experiments.color_game.config import GameConfig

    monkeypatch.setenv('AI_COLLUSION_REQUEST_POOL_DIR', str(tmp_path / 'pool'))
    monkeypatch.setenv('AI_COLLUSION_REQUEST_POOL_SIZE', '1')
    config = GameConfig(rounds=1, colors=('red', 'blue'), actions_per_agent=3,
                        round_time_limit_s=1.0, seed=124)
    model = client.ModelConfig('test', 'openai', 'test', base_url='https://example.test/v1',
                               api_key_env='TEST_AUTH_KEY')
    with pytest.raises(auth_stop.AuthenticationStop):
        if runner == 'batch':
            run_all_configs(config, model, output_dir=tmp_path / 'batch')
        else:
            run_campaign(config, model, output_dir=tmp_path / 'campaign',
                         rollouts_per_setting=3, max_parallel_rollouts=1,
                         settings=['sync_counter' if runner == 'realtime_campaign' else 'guessing_only'])
    assert len(unauthorized_api) == 1
