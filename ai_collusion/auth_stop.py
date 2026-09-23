"""Fatal model authentication failures stop all workers, not just one episode."""
import logging
import os
from pathlib import Path
import threading


_STOP = threading.Event()
MARKER = 'AUTHENTICATION_FAILED'
MESSAGE = (
    'FATAL: model API returned HTTP 401 Unauthorized. Experiment stopped; '
    'further model requests are blocked. Fix the credentials and restart the process. '
    'If using a shared request pool, remove its AUTHENTICATION_FAILED marker '
    'only after fixing the credentials.'
)


class AuthenticationStop(SystemExit):
    """Bypass ordinary per-episode Exception handlers and exit with status 1."""

    status_code = 401

    def __init__(self):
        super().__init__(MESSAGE)


def _marker():
    directory = os.environ.get('AI_COLLUSION_REQUEST_POOL_DIR')
    return Path(directory) / MARKER if directory else None


def check():
    """Check both this process and the experiment's optional shared stop file."""
    marker = _marker()
    if _STOP.is_set() or (marker is not None and marker.exists()):
        _STOP.set()
        raise AuthenticationStop() from None


def trip():
    """Latch before releasing request admission; never include provider secrets."""
    _STOP.set()
    logging.getLogger(__name__).critical(MESSAGE)
    marker = _marker()
    if marker is not None:
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            # Existence is the signal; exclusive creation is safe across workers.
            with marker.open('x') as stream:
                stream.write(MESSAGE + '\n')
        except FileExistsError:
            pass
        except OSError:
            logging.getLogger(__name__).critical(
                'Could not persist the authentication stop marker; this process is still stopped.')
    raise AuthenticationStop() from None
