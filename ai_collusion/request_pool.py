"""Opt-in fixed admission across processes on one host.

Locks cover actual API attempts, never retry sleeps. The OS releases a slot if
a worker exits, so an interrupted request cannot leave a permanent lease.
"""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import random
import threading
import time

from . import auth_stop


class RequestPool:
    def __init__(self, directory, capacity):
        if type(capacity) is not int or capacity < 1:
            raise ValueError('request pool capacity must be a positive integer')
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.capacity = capacity
        with (self.directory / 'configuration.lock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            config = self.directory / 'capacity.json'
            if config.exists():
                if json.loads(config.read_text()) != {'capacity': capacity}:
                    raise ValueError('existing request pool has a different fixed capacity')
            else:
                temporary = self.directory / 'capacity.tmp'
                temporary.write_text(json.dumps({'capacity': capacity}))
                temporary.replace(config)

    @contextmanager
    def attempt(self, model=None):
        slots = list(range(self.capacity))
        random.shuffle(slots)
        acquired = None
        try:
            while acquired is None:
                auth_stop.check()
                for slot in slots:
                    stream = (self.directory / f'slot-{slot:04d}.lock').open('a+')
                    try:
                        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        stream.close()
                        continue
                    except BaseException:
                        stream.close()
                        raise
                    acquired = stream
                    stream.seek(0)
                    stream.truncate()
                    json.dump({'pid': os.getpid(), 'thread': threading.get_ident(),
                               'model': model, 'started_unix_s': time.time()}, stream)
                    stream.flush()
                    break
                if acquired is None:
                    time.sleep(random.uniform(0.025, 0.075))
            auth_stop.check()
            yield
        finally:
            if acquired is not None:
                acquired.close()

    def snapshot(self):
        active = []
        for slot in range(self.capacity):
            with (self.directory / f'slot-{slot:04d}.lock').open('a+') as stream:
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    stream.seek(0)
                    try:
                        owner = json.load(stream)
                    except (ValueError, OSError):
                        owner = {'owner_update_in_progress': True}
                    active.append({'slot': slot, **owner})
        return {'capacity': self.capacity, 'active_requests': len(active), 'active': active}


_CACHE = {}
_LOCK = threading.Lock()


def configured_pool():
    """Only explicitly opted-in processes participate; historical defaults stay unchanged."""
    directory = os.environ.get('AI_COLLUSION_REQUEST_POOL_DIR')
    if not directory:
        return None
    capacity = int(os.environ['AI_COLLUSION_REQUEST_POOL_SIZE'])
    key = (directory, capacity)
    with _LOCK:
        if key not in _CACHE:
            _CACHE[key] = RequestPool(directory, capacity)
        return _CACHE[key]


def exponential_delay(attempt):
    """Seconds before the next attempt, excluding jitter and Retry-After."""
    return float(2 ** min(attempt, 7)) if attempt < 7 else 120.0
