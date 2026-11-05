"""Offline recorder tests. Live HTTP is not required."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import httpx
import pytest
from research.depth_recorder import SCHEMA, Clock, Recorder, RestClient, Store, classify_book, parse_until, read_records, assert_output_safe

AT = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)

def raw_market(ticker, open_at, close_at, ranges=None):
    return {
        'ticker': ticker, 'open_time': open_at.isoformat(), 'close_time': close_at.isoformat(), 'status': 'active',
        'price_level_structure': 'tapered_deci_cent',
        'price_ranges': ranges or [{'start': '0.1000', 'end': '0.9000', 'step': '0.0100'}],
        'floor_strike': 100,
    }

class FakeClock:
    def __init__(self):
        self.now = AT
        self.ns = 1_000_000_000
    def wall(self):
        return self.now
    def mono_ns(self):
        self.ns += 2_000_000
        return self.ns
    def sleep(self, seconds):
        self.now += timedelta(seconds=seconds)
        self.ns += int(seconds * 1e9)

class Script:
    def __init__(self, items):
        self.base = 'https://example.test/trade-api/v2'
        self.items = list(items)
        self.paths = []
    def call(self, clock, path, params=None):
        self.paths.append(path)
        started = clock.mono_ns()
        item = self.items.pop(0)
        ended = clock.mono_ns()
        return {'ok': item.get('error') is None, 'status': item.get('status', 200), 'body': item.get('body'), 'error': item.get('error'),
                'request_started_at': clock.wall().isoformat(), 'response_received_at': clock.wall().isoformat(),
                'latency_ms': item.get('latency_ms', (ended - started) / 1e6)}

def book(yes=None, no=None):
    return {'orderbook_fp': {'yes_dollars': yes or [], 'no_dollars': no or []}}

def test_size_strings_roundtrip_and_flags(tmp_path):
    clock = FakeClock()
    market = raw_market('KXBTC15M-A', AT - timedelta(minutes=5), AT + timedelta(minutes=10))
    ladder = book(yes=[['0.4200', '10.50']], no=[['0.5700', '3.00']])
    script = Script([{'body': {'markets': [market]}}, {'body': ladder}])
    recorder = Recorder(tmp_path, script, clock, poll_seconds=3, metadata_seconds=60, segment_bytes=10**9)
    record = recorder.step()
    assert record['schema'] == SCHEMA
    assert record['orderbook_fp']['yes_dollars'] == [['0.4200', '10.50']]
    assert record['flags']['empty'] is False and record['flags']['one_sided'] is False
    assert record['market']['price_ranges'][0]['step'] == '0.0100'
    assert 'exchange' not in json.dumps(record).lower() or 'not an exchange' in json.dumps(record)
    text = (tmp_path / 'segment-0001.jsonl').read_text()
    assert '"10.50"' in text and '10.5,' not in text
    again = Script([{'body': ladder}])
    # Same recorder, second poll does not rediscover.
    recorder.client = again
    second = recorder.step()
    assert second['unchanged_from_previous'] is True
    assert second['local_seq'] == record['local_seq'] + 1
    loaded, skipped = read_records(tmp_path)
    snaps = [row for row in loaded if row['event'] == 'snapshot']
    assert skipped == 0 and snaps[0]['orderbook_fp']['no_dollars'][0][1] == '3.00'

def test_empty_one_sided_malformed_and_error(tmp_path):
    assert classify_book(book())[1]['empty'] is True
    assert classify_book(book(yes=[['0.1000', '1.00']]))[1]['one_sided'] is True
    ladders, flags = classify_book({'orderbook_fp': {'yes_dollars': 'bad', 'no_dollars': []}})
    assert ladders is None and flags['malformed'] is True
    clock = FakeClock()
    market = raw_market('KXBTC15M-A', AT - timedelta(minutes=1), AT + timedelta(minutes=10))
    script = Script([
        {'body': {'markets': [market]}},
        {'status': 500, 'error': 'http_500', 'body': None},
    ])
    recorder = Recorder(tmp_path, script, clock, poll_seconds=3)
    failed = recorder.step()
    assert failed['error'] == 'http_500' and failed['orderbook_fp'] is None and failed['flags']['error'] is True
    assert recorder.backoff == 6
    recorder.client = Script([{'body': {'orderbook_fp': {'yes_dollars': {'nope': 1}, 'no_dollars': []}}}])
    bad = recorder.step()
    assert bad['flags']['malformed'] is True and bad['raw_orderbook_fp']['yes_dollars'] == {'nope': 1}

def test_rollover_records_the_new_ticker(tmp_path):
    clock = FakeClock()
    first = raw_market('KXBTC15M-A', AT - timedelta(minutes=14), AT + timedelta(seconds=10))
    second = raw_market('KXBTC15M-B', AT + timedelta(seconds=10), AT + timedelta(minutes=15))
    script = Script([
        {'body': {'markets': [first]}},
        {'body': book(yes=[['0.4000', '2.00']], no=[['0.5900', '2.00']])},
        {'body': {'markets': [second]}},
        {'body': book(yes=[['0.4100', '4.00']], no=[['0.5800', '4.00']])},
    ])
    recorder = Recorder(tmp_path, script, clock, poll_seconds=3, metadata_seconds=3600)
    opened = recorder.step()
    assert opened['ticker'] == 'KXBTC15M-A' and opened['ticker_changed'] is False
    clock.now = AT + timedelta(seconds=10)
    rolled = recorder.step()
    assert rolled['ticker'] == 'KXBTC15M-B' and rolled['ticker_changed'] is True
    assert '/markets/KXBTC15M-B/orderbook' in script.paths

def test_resume_keeps_old_bytes_and_skips_a_torn_tail(tmp_path):
    clock = FakeClock()
    market = raw_market('KXBTC15M-A', AT - timedelta(minutes=1), AT + timedelta(minutes=10))
    script = Script([{'body': {'markets': [market]}}, {'body': book(yes=[['0.2000', '1.00']], no=[['0.7000', '1.00']])}])
    recorder = Recorder(tmp_path, script, clock)
    recorder.step()
    recorder.close()
    target = tmp_path / 'segment-0001.jsonl'
    before = target.read_bytes()
    with target.open('a', encoding='utf-8') as handle:
        handle.write('{"local_seq":999,')
    torn = target.read_bytes()
    resumed = Recorder(tmp_path, Script([
        {'body': {'markets': [market]}},
        {'body': book(yes=[['0.2100', '1.25']], no=[['0.7800', '8.00']])},
    ]), clock, metadata_seconds=3600)
    # Torn segment is unchanged; the new poll is a metadata refresh or a book.
    resumed.step()
    resumed.close()
    assert target.read_bytes() == torn
    assert before in torn
    loaded, skipped = read_records(tmp_path)
    assert skipped == 1
    seqs = [row['local_seq'] for row in loaded if row['event'] == 'snapshot']
    assert seqs[0] == 1 and max(seqs) > 1

def test_rotation_gzip_matches_and_until_stops(tmp_path):
    clock = FakeClock()
    market = raw_market('KXBTC15M-A', AT - timedelta(minutes=1), AT + timedelta(hours=1))
    responses = [{'body': {'markets': [market]}}]
    responses += [{'body': book(yes=[['0.3000', f'{i}.00']], no=[['0.6000', '1.00']])} for i in range(1, 6)]
    script = Script(responses)
    recorder = Recorder(tmp_path, script, clock, poll_seconds=3, metadata_seconds=3600, segment_bytes=180, max_total_bytes=10**7)
    reason = recorder.run(AT + timedelta(seconds=9), sleep=clock.sleep, install_signals=False)
    assert reason == 'until'
    gz = list(tmp_path.glob('segment-*.jsonl.gz'))
    assert gz
    loaded, skipped = read_records(tmp_path)
    assert skipped == 0
    assert [row['event'] for row in loaded[:1]] == ['started']
    assert loaded[-1]['event'] == 'stopped' and loaded[-1]['reason'] == 'until'
    assert any(row['event'] == 'snapshot' and row['orderbook_fp']['yes_dollars'][0][1] == '1.00' for row in loaded)

def test_refuses_paper_paths_and_requires_timezone():
    with pytest.raises(ValueError, match='paper'):
        assert_output_safe('/tmp/kalshi-paper-taker/depth')
    with pytest.raises(ValueError, match='sqlite'):
        assert_output_safe('/tmp/depth/runtime.sqlite')
    assert parse_until('2026-11-06T23:20:00Z') == datetime(2026, 11, 6, 23, 20, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        parse_until('2026-11-06T23:20:00')

def test_rest_client_preserves_transport_body():
    def handler(request):
        assert request.url.params['depth'] == '0'
        return httpx.Response(200, json={'orderbook_fp': {'yes_dollars': [['0.3300', '8.50']], 'no_dollars': [['0.6600', '8.50']]}})
    client = RestClient('https://example.test', min_interval=0, transport=httpx.MockTransport(handler))
    call = client.call(Clock(), '/markets/TEST/orderbook', {'depth': 0})
    client.close()
    assert call['ok'] and call['latency_ms'] >= 0
    assert call['body']['orderbook_fp']['yes_dollars'][0] == ['0.3300', '8.50']
