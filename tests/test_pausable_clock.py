import json
import os
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from spire_exact.pausable_clock import PauseLedgerClock, active_counter, queue_get


class PauseClockTests(unittest.TestCase):
    def test_pause_and_resume_leave_active_deadline_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'pause.json'
            now = [100.0]
            clock = PauseLedgerClock(path, raw=lambda: now[0], poll_seconds=0)
            path.write_text(json.dumps({'paused_total_seconds': 0, 'paused_started_monotonic': None}))
            started = clock.counter()
            now[0] = 105
            path.write_text(json.dumps({'paused_total_seconds': 0, 'paused_started_monotonic': 105}))
            self.assertEqual(clock.counter() - started, 5)
            now[0] = 405
            self.assertEqual(clock.counter() - started, 5)
            path.write_text(json.dumps({'paused_total_seconds': 300, 'paused_started_monotonic': None}))
            self.assertEqual(clock.counter() - started, 5)
            now[0] = 410
            self.assertEqual(clock.counter() - started, 10)

    def test_invalid_transient_ledger_keeps_valid_pause(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'pause.json'
            now = [20.0]
            clock = PauseLedgerClock(path, raw=lambda: now[0], poll_seconds=0)
            path.write_text(json.dumps({'paused_total_seconds': 4, 'paused_started_monotonic': 15}))
            self.assertEqual(clock.counter(), 11)
            path.write_text('incomplete')
            now[0] = 100
            self.assertEqual(clock.counter(), 11)

    def test_noninteractive_clock_and_queue_use_original_calls(self):
        with patch.dict(os.environ, {}, clear=True), patch('spire_exact.pausable_clock.time.perf_counter', return_value=123):
            self.assertEqual(active_counter(), 123)
            inbox = queue.Queue()
            inbox.put('ready')
            self.assertEqual(queue_get(inbox, 2), 'ready')

    def test_queue_wall_expiration_after_suspend_can_still_receive_result(self):
        class Inbox:
            calls = 0
            def get(self, timeout):
                self.calls += 1
                if self.calls == 1:
                    raise queue.Empty
                return 'native result'
        with patch.dict(os.environ, {'SPIRE_PAUSE_LEDGER': 'test-ledger'}), patch('spire_exact.pausable_clock.active_counter', side_effect=[10, 11, 11]):
            inbox = Inbox()
            self.assertEqual(queue_get(inbox, 3), 'native result')
            self.assertEqual(inbox.calls, 2)
