import multiprocessing as mp

import pytest

from ai_collusion import client
from ai_collusion.request_pool import RequestPool


def hold_slot(directory, capacity, entered, release, active, peak):
    pool = RequestPool(directory, capacity)
    with pool.attempt('test'):
        with active.get_lock():
            active.value += 1
            peak.value = max(peak.value, active.value)
        entered.put(True)
        release.wait(10)
        with active.get_lock():
            active.value -= 1


def test_pool_is_shared_across_processes_and_fills_fixed_capacity(tmp_path):
    ctx = mp.get_context('spawn')
    entered, release = ctx.Queue(), ctx.Event()
    active, peak = ctx.Value('i', 0), ctx.Value('i', 0)
    processes = [ctx.Process(target=hold_slot,
                            args=(str(tmp_path), 2, entered, release, active, peak))
                 for _ in range(6)]
    try:
        for process in processes:
            process.start()
        for _ in range(2):
            assert entered.get(timeout=10)
        assert RequestPool(tmp_path, 2).snapshot()['active_requests'] == 2
        release.set()
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
        assert peak.value == 2
        assert RequestPool(tmp_path, 2).snapshot()['active_requests'] == 0
    finally:
        release.set()
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)


def test_crashed_worker_releases_its_slot(tmp_path):
    ctx = mp.get_context('spawn')
    entered, release = ctx.Queue(), ctx.Event()
    active, peak = ctx.Value('i', 0), ctx.Value('i', 0)
    process = ctx.Process(target=hold_slot, args=(str(tmp_path), 1, entered, release,
                                                active, peak))
    process.start()
    try:
        assert entered.get(timeout=10)
        assert RequestPool(tmp_path, 1).snapshot()['active_requests'] == 1
        process.terminate()
        process.join(timeout=5)
        pool = RequestPool(tmp_path, 1)
        assert pool.snapshot()['active_requests'] == 0
        with pool.attempt():
            assert pool.snapshot()['active_requests'] == 1
    finally:
        if process.is_alive():
            process.terminate()
        process.join(timeout=5)


def test_capacity_cannot_silently_change(tmp_path):
    RequestPool(tmp_path, 2)
    with pytest.raises(ValueError, match='different fixed capacity'):
        RequestPool(tmp_path, 3)


@pytest.mark.parametrize('retry_after,expected', [(None, [2, 4, 8]), ('9', [9, 9, 9])])
def test_retries_release_slot_and_back_off_exponentially(tmp_path, monkeypatch,
                                                       retry_after, expected):
    monkeypatch.setenv('AI_COLLUSION_REQUEST_POOL_DIR', str(tmp_path))
    monkeypatch.setenv('AI_COLLUSION_REQUEST_POOL_SIZE', '1')
    monkeypatch.setattr(client.random, 'uniform', lambda a, b: 0)
    pool = RequestPool(tmp_path, 1)
    sleeps = []
    calls = []

    def sleep(seconds):
        assert pool.snapshot()['active_requests'] == 0
        sleeps.append(seconds)

    def call():
        assert pool.snapshot()['active_requests'] == 1
        calls.append(True)
        if len(calls) < 4:
            error = RuntimeError('rate limit')
            error.status_code = 429
            error.body = {'error': {'metadata': {'headers': {'Retry-After': retry_after}}}}
            raise error
        return 'ok'

    monkeypatch.setattr(client.time, 'sleep', sleep)
    assert client._with_retries(client.ModelConfig('test', 'openai', 'test'), call) == 'ok'
    assert sleeps == expected
    assert pool.snapshot()['active_requests'] == 0
