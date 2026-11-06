"""Lock, gap, and restart behavior. No host service is installed."""
from datetime import datetime, timedelta, timezone
import json
from research.depth_recorder import Recorder
from research.runner import LOCK_BUSY_EXIT, LockBusy, WriterLock, supervise
from tests.test_depth_recorder import AT, FakeClock, Script, book, raw_market

def test_second_writer_is_rejected(tmp_path):
    first = WriterLock(tmp_path)
    first.acquire()
    second = WriterLock(tmp_path)
    try:
        try:
            second.acquire()
            raised = False
        except LockBusy:
            raised = True
        assert raised
    finally:
        first.release()
        second.release()

def test_restart_gap_does_not_invent_snapshots(tmp_path):
    clock = FakeClock()
    market = raw_market('KXBTC15M-A', AT - timedelta(minutes=1), AT + timedelta(hours=1))
    script = Script([{'body': {'markets': [market]}}, {'body': book(yes=[['0.4000', '2.00']], no=[['0.5900', '2.00']])}])
    recorder = Recorder(tmp_path, script, clock, metadata_seconds=3600)
    recorder.step()
    recorder.close()
    clock.now = AT + timedelta(minutes=10)
    again = Recorder(tmp_path, Script([
        {'body': {'markets': [market]}},
        {'body': book(yes=[['0.4100', '2.00']], no=[['0.5800', '2.00']])},
    ]), clock, metadata_seconds=3600)
    gap = again.note_gap(30)
    again.close()
    assert gap['fail_closed'] is True and gap['gap_seconds'] > 30
    from research.depth_recorder import read_records
    records, skipped = read_records(tmp_path)
    assert skipped == 0
    assert sum(row['event'] == 'snapshot' for row in records) == 1
    assert any(row['event'] == 'gap' for row in records)
    assert (tmp_path / 'heartbeat.json').exists()

def test_supervise_stops_on_clean_exit_and_on_a_busy_lock():
    class Child:
        def __init__(self, code):
            self.code = code
        def wait(self):
            return self.code
    calls = {'n': 0}
    def spawn(argv):
        calls['n'] += 1
        return Child(0 if calls['n'] == 2 else 1)
    now = {'t': AT}
    def clock():
        return now['t']
    def sleep(seconds):
        now['t'] += timedelta(seconds=seconds)
    result = supervise(['recorder'], AT + timedelta(seconds=10), sleep=sleep, spawn=spawn, now=clock, max_backoff=4, initial_backoff=1)
    assert result['reason'] == 'child_exit_0'
    assert [row['code'] for row in result['restarts']] == [1]
    def busy(argv):
        return Child(LOCK_BUSY_EXIT)
    held = supervise(['recorder'], AT + timedelta(hours=1), sleep=sleep, spawn=busy, now=clock)
    assert held['reason'] == 'lock_busy' and held['restarts'] == []
