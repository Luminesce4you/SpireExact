"""Interactive budget clock; process suspension time does not consume budgets.

The dashboard owns the ledger and the OS suspension. This module only reads
that ledger. It never changes search requests, state identity, or node budgets.
Without SPIRE_PAUSE_LEDGER every call uses the original monotonic clock.
"""
from __future__ import annotations
import json
import math
import os
import queue
import threading
import time
from pathlib import Path


class PauseLedgerClock:
    def __init__(self, path, *, raw=time.perf_counter, poll_seconds=.025):
        self.path = Path(path)
        self.raw = raw
        self.poll_seconds = poll_seconds
        self.lock = threading.Lock()
        self.next_read = float('-inf')
        self.completed = 0.0
        self.started = None

    def paused_seconds(self, now=None):
        now = self.raw() if now is None else now
        with self.lock:
            if now >= self.next_read:
                self.next_read = now + self.poll_seconds
                try:
                    row = json.loads(self.path.read_text(encoding='utf-8'))
                    completed = float(row.get('paused_total_seconds', 0))
                    started = row.get('paused_started_monotonic')
                    started = float(started) if started is not None else None
                    if not math.isfinite(completed) or completed < 0 or (started is not None and not math.isfinite(started)):
                        raise ValueError('Invalid pause ledger clock')
                    self.completed, self.started = completed, started
                except (OSError, ValueError, TypeError):
                    # The owner replaces the file atomically. Keep the last
                    # valid ledger through a transient read failure.
                    pass
            return self.completed + (max(0.0, now - self.started) if self.started is not None else 0.0)

    def counter(self):
        now = self.raw()
        return now - self.paused_seconds(now)


_clock = None
_path = None
_configuration_lock = threading.Lock()


def _configured_clock():
    global _clock, _path
    path = os.environ.get('SPIRE_PAUSE_LEDGER')
    if not path:
        return None
    if path != _path:
        with _configuration_lock:
            if path != _path:
                _clock, _path = PauseLedgerClock(path), path
    return _clock


def active_counter():
    clock = _configured_clock()
    return clock.counter() if clock else time.perf_counter()


def paused_seconds():
    clock = _configured_clock()
    return clock.paused_seconds() if clock else 0.0


def queue_get(inbox, timeout):
    """Queue's internal wall timeout cannot survive an OS process suspension."""
    if not os.environ.get('SPIRE_PAUSE_LEDGER'):
        return inbox.get(timeout=timeout)
    end = active_counter() + timeout
    while True:
        remaining = end - active_counter()
        if remaining <= 0:
            raise queue.Empty
        try:
            return inbox.get(timeout=min(.2, remaining))
        except queue.Empty:
            continue
