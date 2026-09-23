"""Stop a campaign after an exhausted model error without consuming queued questions."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class RunCancelled(RuntimeError):
    """No model request was sent because the campaign had already stopped."""


class RunControl:
    def __init__(self, stop_file=None, admission_file=None, max_active=50):
        self.stop_file = Path(stop_file) if stop_file else None
        self.event = threading.Event()
        self.lock = threading.Lock()
        self.reason = None
        self.gate = RequestGate(admission_file, max_active) if admission_file else None

    def cancelled(self):
        if self.stop_file and self.stop_file.exists():
            self.event.set()
        return self.event.is_set()

    def fail(self, exc, **context):
        if isinstance(exc, RunCancelled):
            return
        with self.lock:
            if self.event.is_set():
                return
            # Error messages/provider bodies remain in the private transcript. This
            # shared control file needs only status and the location of the failure.
            self.reason = {"reason": "model_error_after_retries", "type": type(exc).__name__,
                           "status_code": getattr(exc, "status_code", None),
                           "at_utc": datetime.now(timezone.utc).isoformat(), **context}
            if self.stop_file:
                try:
                    with self.stop_file.open("x") as f:
                        json.dump(self.reason, f, indent=2)
                        f.flush()
                        os.fsync(f.fileno())
                except FileExistsError:
                    pass  # Another cohort already requested the same campaign stop.
            self.event.set()

    def details(self):
        if self.stop_file and self.stop_file.exists():
            try:
                return json.loads(self.stop_file.read_text())
            except (OSError, ValueError):
                return {"reason": "campaign_stop_requested"}
        return self.reason

    @contextmanager
    def attempt(self):
        if self.cancelled():
            raise RunCancelled("Campaign stopped; no model request was sent")
        if self.gate:
            with self.gate.attempt(self.cancelled):
                yield
        else:
            yield

    def on_retry(self, event):
        if self.gate:
            self.gate.backoff(event)


class RequestGate:
    """FIFO API admission with shared backoff and bounded, success-driven recovery.

    Each API credential/provider pool should have its own database. All callers
    using one database must use the same configured ``max_active``. Existing
    databases retain their current limit and cooldown when opened by this version.
    """

    RECOVERY_INTERVAL_S = 30.0
    POLL_INTERVAL_S = 0.25

    def __init__(self, path, max_active):
        if max_active < 1:
            raise ValueError("Request concurrency must be positive")
        self.path = str(path)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, request_limit INTEGER, cooldown_until REAL, backoffs INTEGER)")
            db.execute("INSERT OR IGNORE INTO state VALUES (1, ?, 0, 0)", (max_active,))
            db.execute("CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, pid INTEGER, started REAL)")
            db.execute("CREATE TABLE IF NOT EXISTS backoff_events (at REAL, request_limit INTEGER, cooldown_until REAL, reason TEXT)")
            # Separate tables leave the original schema readable by old reports.
            db.execute("CREATE TABLE IF NOT EXISTS waiters (ticket INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, pid INTEGER)")
            db.execute("CREATE TABLE IF NOT EXISTS recovery_state (id INTEGER PRIMARY KEY, max_active INTEGER, recovery_after REAL, healthy_successes INTEGER, recoveries INTEGER)")
            db.execute("INSERT OR IGNORE INTO recovery_state SELECT 1, ?, max(?,cooldown_until)+?, 0, 0 FROM state WHERE id=1",
                       (max_active, time.time(), self.RECOVERY_INTERVAL_S))
            db.execute("UPDATE recovery_state SET max_active=? WHERE id=1", (max_active,))
            db.execute("UPDATE state SET request_limit=min(request_limit,?) WHERE id=1", (max_active,))
            db.execute("CREATE TABLE IF NOT EXISTS recovery_events (at REAL, previous_limit INTEGER, request_limit INTEGER)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def attempt(self, cancelled):
        token = uuid.uuid4().hex
        admitted = False
        succeeded = False
        failed = False
        try:
            if cancelled():
                raise RunCancelled("Campaign stopped while waiting for API capacity")
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("INSERT INTO waiters (id,pid) VALUES (?,?)", (token, os.getpid()))
            while not admitted:
                if cancelled():
                    raise RunCancelled("Campaign stopped while waiting for API capacity")
                with self.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    self._reap_dead_processes(db)
                    limit, until = db.execute("SELECT request_limit,cooldown_until FROM state WHERE id=1").fetchone()
                    active = db.execute("SELECT count(*) FROM requests").fetchone()[0]
                    ahead = db.execute("SELECT count(*) FROM waiters WHERE ticket < (SELECT ticket FROM waiters WHERE id=?)", (token,)).fetchone()[0]
                    # Earlier waiters reserve their places. A fast session cannot
                    # repeatedly take the next slot ahead of slower sessions.
                    admitted = time.time() >= until and active + ahead < limit
                    if admitted:
                        db.execute("DELETE FROM waiters WHERE id=?", (token,))
                        db.execute("INSERT INTO requests VALUES (?,?,?)", (token, os.getpid(), time.time()))
                if not admitted:
                    time.sleep(self.POLL_INTERVAL_S)
            if cancelled():
                raise RunCancelled("Campaign stopped before API request")
            yield
            succeeded = True
        except RunCancelled:
            raise
        except BaseException:
            failed = admitted
            raise
        finally:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT started FROM requests WHERE id=?", (token,)).fetchone()
                db.execute("DELETE FROM requests WHERE id=?", (token,))
                db.execute("DELETE FROM waiters WHERE id=?", (token,))
                if succeeded and row is not None:
                    self._record_success(db, row[0])
                elif failed:
                    # Even non-rate-limit failures end the healthy interval. The
                    # retry hook handles rate-limit reductions and Retry-After.
                    db.execute("UPDATE recovery_state SET healthy_successes=0,recovery_after=max(recovery_after,?) WHERE id=1",
                               (time.time() + self.RECOVERY_INTERVAL_S,))

    @staticmethod
    def _reap_dead_processes(db):
        pids = [row[0] for row in db.execute("SELECT pid FROM requests UNION SELECT pid FROM waiters")]
        for pid in pids:
            if pid == os.getpid():
                continue
            try:
                if pid <= 0:
                    raise ProcessLookupError
                os.kill(pid, 0)
            except ProcessLookupError:
                db.execute("DELETE FROM requests WHERE pid=?", (pid,))
                db.execute("DELETE FROM waiters WHERE pid=?", (pid,))
            except PermissionError:
                pass  # A live process owned by another account still owns its slot.

    def _record_success(self, db, started):
        now = time.time()
        limit, until = db.execute("SELECT request_limit,cooldown_until FROM state WHERE id=1").fetchone()
        maximum, after, healthy = db.execute("SELECT max_active,recovery_after,healthy_successes FROM recovery_state WHERE id=1").fetchone()
        if limit >= maximum or started < until:
            return  # Requests from the rejected burst cannot trigger recovery.
        healthy += 1
        if now >= max(until, after) and healthy >= limit:
            # Add at most 25% after one limit-sized set of successful requests and
            # 30 quiet seconds. At a limit of one, one success permits recovery.
            raised = min(maximum, limit + max(1, (limit + 3) // 4))
            db.execute("UPDATE state SET request_limit=? WHERE id=1", (raised,))
            db.execute("UPDATE recovery_state SET healthy_successes=0,recovery_after=?,recoveries=recoveries+1 WHERE id=1",
                       (now + self.RECOVERY_INTERVAL_S,))
            db.execute("INSERT INTO recovery_events VALUES (?,?,?)", (now, limit, raised))
        else:
            db.execute("UPDATE recovery_state SET healthy_successes=? WHERE id=1", (min(healthy, limit),))

    def backoff(self, event):
        status = event.get("status_code")
        if status == 429:
            reason = "upstream_rate_limit"
        elif status == 402 and event.get("reason") == "in_flight_budget_exhausted":
            reason = event["reason"]
        else:
            return
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            limit, until = db.execute("SELECT request_limit,cooldown_until FROM state WHERE id=1").fetchone()
            # Concurrent rejections from the same burst lower the limit only once.
            if now >= until:
                limit = max(1, limit // 2)
            until = max(until, now + event["wait_s"])
            db.execute("UPDATE state SET request_limit=?,cooldown_until=?,backoffs=backoffs+1 WHERE id=1", (limit, until))
            db.execute("UPDATE recovery_state SET healthy_successes=0,recovery_after=? WHERE id=1",
                       (until + self.RECOVERY_INTERVAL_S,))
            db.execute("INSERT INTO backoff_events VALUES (?,?,?,?)", (now, limit, until, reason))

    def snapshot(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._reap_dead_processes(db)
            limit, until, backoffs = db.execute("SELECT request_limit,cooldown_until,backoffs FROM state WHERE id=1").fetchone()
            active = db.execute("SELECT count(*) FROM requests").fetchone()[0]
            waiting = db.execute("SELECT count(*) FROM waiters").fetchone()[0]
            maximum, after, healthy, recoveries = db.execute("SELECT max_active,recovery_after,healthy_successes,recoveries FROM recovery_state WHERE id=1").fetchone()
        return {"request_limit": limit, "active_requests": active,
                "cooldown_until_unix_s": until, "backoff_events": backoffs,
                "configured_request_limit": maximum, "waiting_requests": waiting,
                "recovery_after_unix_s": after, "healthy_requests_since_recovery": healthy,
                "recovery_events": recoveries}
