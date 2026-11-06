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

def test_legacy_state_without_last_received_at_uses_the_segment(tmp_path):
    """bae7cc4 state.json has no last_received_at. The gap must copy a real receive time."""
    import gzip
    received = '2026-11-06T21:06:50.278633Z'
    earlier = '2026-11-06T21:00:00Z'
    with gzip.open(tmp_path / 'segment-0001.jsonl.gz', 'wt', encoding='utf-8') as handle:
        handle.write(json.dumps({'event': 'snapshot', 'response_received_at': earlier, 'local_seq': 1}) + '\n')
        handle.write(json.dumps({'event': 'snapshot', 'response_received_at': 'not-a-time', 'local_seq': 2}) + '\n')
        handle.write(json.dumps({
            'event': 'snapshot', 'schema': 'depth-snapshot-v1', 'local_seq': 5955,
            'response_received_at': received, 'ticker': 'KXBTC15M-26NOV061615-15',
        }) + '\n')
    (tmp_path / 'state.json').write_text(json.dumps({
        'local_seq': 5956, 'active': None, 'segments': [], 'next_index': 1,
        'last_ticker': 'KXBTC15M-26NOV061615-15', 'compressed': ['segment-0001.jsonl.gz'],
    }))
    clock = FakeClock()
    clock.now = datetime(2026, 11, 6, 21, 56, 17, tzinfo=timezone.utc)
    recorder = Recorder(tmp_path, Script([]), clock, metadata_seconds=3600)
    assert 'last_received_at' not in recorder.store.state
    gap = recorder.note_gap(30)
    recorder.close()
    assert gap['previous_received_at'] == received
    assert gap['previous_source'] == 'segment_response_received_at'
    assert gap['resumed_at'].startswith('2026-11-06T21:56:17')
    assert gap['fail_closed'] is True
    assert gap['gap_seconds'] > 2900
    from research.depth_recorder import read_records
    records, skipped = read_records(tmp_path)
    assert skipped == 0
    assert sum(row['event'] == 'snapshot' for row in records) == 3
    assert sum(row['event'] == 'gap' for row in records) == 1
    bare = tmp_path / 'empty'
    bare.mkdir()
    quiet = Recorder(bare, Script([]), clock)
    assert quiet.note_gap(30) is None
    quiet.close()

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
