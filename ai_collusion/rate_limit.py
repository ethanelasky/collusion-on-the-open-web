"""Optional request-start pacing, independently scoped to each provider/model."""
import threading
import time


class RequestPacer:
    def __init__(self, requests_per_minute, *, clock=time.monotonic, sleep=time.sleep):
        if requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be positive")
        self.interval = 60.0 / requests_per_minute
        self.clock, self.sleep = clock, sleep
        self.next_start = 0.0
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            now = self.clock()
            start = max(now, self.next_start)
            self.next_start = start + self.interval
        # Never hold the lock while waiting, and never hold a slot for the
        # duration of the network request: many requests can be in flight.
        if start > now:
            self.sleep(start - now)


_PACERS = {}


def _key(config):
    return config.transport, config.base_url, config.model, config.api_key_env


def configure(config, requests_per_minute):
    key = _key(config)
    if requests_per_minute is None:
        _PACERS.pop(key, None)
    else:
        _PACERS[key] = RequestPacer(requests_per_minute)


def wait_for_request(config):
    pacer = _PACERS.get(_key(config))
    if pacer is not None:
        pacer.wait()
