"""Append-only public REST order-book snapshots for the active KXBTC15M contract.

This does not use an API key or the authenticated WebSocket. A snapshot is what
the HTTP response contained when this process received it. Receive time is not
an exchange event time, and these polls are not a full event history, a queue,
or a backfill.

The output directory must not be the paper ledger. Nothing here reads or writes
runtime.sqlite or MODEL_PATH.
"""
from datetime import datetime, timezone
import argparse
import gzip
import json
import os
import signal
import time
from pathlib import Path
import httpx
from btc15.domain import Market, active_market, utc
from research.runner import LockBusy, WriterLock, dependency_manifest, write_heartbeat

SCHEMA = 'depth-snapshot-v1'
DEFAULT_BASE = 'https://external-api.kalshi.com/trade-api/v2'

def assert_output_safe(path):
    resolved = Path(path).expanduser().resolve()
    text = str(resolved)
    if resolved.suffix == '.sqlite' or resolved.name.startswith('runtime.sqlite'):
        raise ValueError('Depth output cannot be a sqlite database')
    if 'kalshi-paper' in text:
        raise ValueError('Depth output cannot be inside a paper-trading directory')
    return resolved

def parse_until(value):
    text = value.strip()
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        raise ValueError('--until must include a timezone')
    return moment.astimezone(timezone.utc)

class Clock:
    def wall(self):
        return datetime.now(timezone.utc)
    def mono_ns(self):
        return time.monotonic_ns()

def _pairs(levels):
    """Keep price and size as returned strings. Refuse to coerce numbers."""
    if not isinstance(levels, list):
        return None
    out = []
    for level in levels:
        if not isinstance(level, (list, tuple)) or len(level) != 2:
            return None
        price, size = level
        if not isinstance(price, str) or not isinstance(size, str):
            return None
        out.append([price, size])
    return out

def classify_book(raw):
    """Return (ladders or None, flags). Missing and malformed books are not filled in."""
    flags = {'empty': False, 'one_sided': False, 'malformed': False, 'missing': False}
    if not isinstance(raw, dict) or 'orderbook_fp' not in raw:
        flags['missing'] = True
        return None, flags
    book = raw.get('orderbook_fp')
    if not isinstance(book, dict):
        flags['malformed'] = True
        flags['missing'] = True
        return None, flags
    yes, no = _pairs(book.get('yes_dollars')), _pairs(book.get('no_dollars'))
    if yes is None or no is None:
        flags['malformed'] = True
        return None, flags
    flags['empty'] = not yes and not no
    flags['one_sided'] = bool(yes) != bool(no)
    return {'yes_dollars': yes, 'no_dollars': no}, flags

def market_view(raw, received_at):
    if not isinstance(raw, dict):
        return None
    try:
        parsed = Market.model_validate(raw)
    except Exception:
        return None
    return {
        'ticker': parsed.ticker,
        'open_time': parsed.open_time.isoformat(),
        'close_time': parsed.close_time.isoformat(),
        'status': parsed.status,
        'price_level_structure': raw.get('price_level_structure') or '',
        'price_ranges': raw.get('price_ranges') or [],
        'metadata_received_at': received_at,
    }

class RestClient:
    """Serial GET client. Timestamps are local; the exchange body is not given a clock."""
    def __init__(self, base=DEFAULT_BASE, timeout=8.0, min_interval=0.25, transport=None):
        self.base = base.rstrip('/')
        self.min_interval = min_interval
        self._last = 0.0
        self.http = httpx.Client(base_url=self.base, timeout=timeout, transport=transport, headers={'User-Agent': 'BTC15-depth-recorder/1'})
    def call(self, clock, path, params=None):
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0 and self.min_interval:
            time.sleep(wait)
        self._last = time.monotonic()
        started_at = clock.wall().isoformat()
        started_ns = clock.mono_ns()
        try:
            response = self.http.get(path, params=params or {})
            received_at = clock.wall().isoformat()
            ended_ns = clock.mono_ns()
            body = None
            error = None
            if response.status_code >= 400:
                error = f'http_{response.status_code}'
            else:
                try:
                    body = response.json()
                except json.JSONDecodeError:
                    error = 'invalid_json'
            return {'ok': error is None, 'status': response.status_code, 'body': body, 'error': error,
                    'request_started_at': started_at, 'response_received_at': received_at,
                    'latency_ms': (ended_ns - started_ns) / 1e6}
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            ended_ns = clock.mono_ns()
            return {'ok': False, 'status': None, 'body': None, 'error': type(exc).__name__,
                    'request_started_at': started_at, 'response_received_at': clock.wall().isoformat(),
                    'latency_ms': (ended_ns - started_ns) / 1e6}
    def close(self):
        self.http.close()

class Store:
    """Append-only segments. A torn tail is left in place and a new segment is opened."""
    def __init__(self, root, segment_bytes=8 * 1024 * 1024, max_total_bytes=512 * 1024 * 1024):
        self.root = assert_output_safe(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.segment_bytes = int(segment_bytes)
        self.max_total_bytes = int(max_total_bytes)
        self.state_path = self.root / 'state.json'
        self.state = self._load_state()
        self.handle = None
        self._open_active()
    def _load_state(self):
        if not self.state_path.exists():
            return {'local_seq': 0, 'active': None, 'segments': []}
        try:
            return json.loads(self.state_path.read_text())
        except json.JSONDecodeError:
            return {'local_seq': 0, 'active': None, 'segments': []}
    def _save_state(self):
        temporary = self.state_path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(self.state, indent=2))
        os.replace(temporary, self.state_path)
    def _scan_seq(self, path):
        highest = 0
        if not path.exists():
            return highest
        opener = gzip.open if path.suffix == '.gz' else open
        with opener(path, 'rt', encoding='utf-8') as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                highest = max(highest, int(record.get('local_seq') or 0))
        return highest
    def _open_active(self):
        name = self.state.get('active')
        path = self.root / name if name else None
        if path is not None and path.exists() and not _ends_clean(path):
            # Leave the torn file untouched and continue in a new segment.
            self.state.setdefault('torn', []).append(name)
            path = None
            self.state['active'] = None
        if path is not None and not path.exists():
            path = None
            self.state['active'] = None
        if path is None:
            self.state['next_index'] = int(self.state.get('next_index') or 0) + 1
            name = f"segment-{self.state['next_index']:04d}.jsonl"
            path = self.root / name
            self.state['active'] = name
            self.state.setdefault('segments', [])
            if name not in self.state['segments']:
                self.state['segments'].append(name)
            self._save_state()
        self.path = path
        self.handle = open(path, 'a', encoding='utf-8')
        self.state['local_seq'] = max(int(self.state.get('local_seq') or 0), self._scan_seq(path))
        self._save_state()
    def bytes_used(self):
        total = 0
        for path in self.root.iterdir():
            if path.is_file() and (path.suffix in {'.jsonl', '.gz'} or path.name.endswith('.jsonl.gz')):
                total += path.stat().st_size
        return total
    def append(self, record):
        if self.bytes_used() >= self.max_total_bytes:
            return False
        self.state['local_seq'] = int(self.state.get('local_seq') or 0) + 1
        record['local_seq'] = self.state['local_seq']
        line = json.dumps(record, separators=(',', ':'), ensure_ascii=False) + '\n'
        self.handle.write(line)
        self.handle.flush()
        os.fsync(self.handle.fileno())
        self._save_state()
        if self.path.stat().st_size >= self.segment_bytes:
            self._rotate()
        return True
    def _rotate(self):
        self.handle.close()
        gzip_path = Path(str(self.path) + '.gz')
        with open(self.path, 'rb') as source, gzip.open(gzip_path, 'wb') as target:
            target.write(source.read())
        with gzip.open(gzip_path, 'rt', encoding='utf-8') as check:
            original = [line for line in self.path.read_text(encoding='utf-8').splitlines() if line.strip()]
            copied = [line for line in check.read().splitlines() if line.strip()]
        if copied != original:
            raise RuntimeError('Refusing to drop a segment whose gzip copy does not match')
        plain_name = self.path.name
        self.path.unlink()
        self.state['segments'] = [name for name in self.state.get('segments', []) if name != plain_name]
        self.state.setdefault('compressed', []).append(gzip_path.name)
        self.state['active'] = None
        self.handle = None
        self._save_state()
        self._open_active()
    def close(self):
        if self.handle and not self.handle.closed:
            self.handle.flush()
            os.fsync(self.handle.fileno())
            self.handle.close()

def _ends_clean(path):
    if not path.exists() or path.stat().st_size == 0:
        return True
    with open(path, 'rb') as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - 1))
        if handle.read(1) != b'\n':
            return False
        handle.seek(max(0, size - 8192))
        tail = handle.read().decode('utf-8', errors='replace').splitlines()
    if not tail:
        return True
    try:
        json.loads(tail[-1])
    except json.JSONDecodeError:
        return False
    return True

def read_records(root):
    root = Path(root)
    names = sorted([path.name for path in root.iterdir() if path.is_file() and (path.name.endswith('.jsonl') or path.name.endswith('.jsonl.gz'))])
    records = []
    skipped = 0
    for name in names:
        path = root / name
        opener = gzip.open if name.endswith('.gz') else open
        with opener(path, 'rt', encoding='utf-8') as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    records.append(json.loads(text))
                except json.JSONDecodeError:
                    skipped += 1
    return records, skipped

class Recorder:
    def __init__(self, output, client, clock=None, poll_seconds=3.0, metadata_seconds=60.0, segment_bytes=8 * 1024 * 1024, max_total_bytes=512 * 1024 * 1024, slow_response_ms=5000.0):
        self.clock = clock or Clock()
        self.client = client
        self.poll_seconds = float(poll_seconds)
        self.metadata_seconds = float(metadata_seconds)
        self.slow_response_ms = float(slow_response_ms)
        self.store = Store(output, segment_bytes=segment_bytes, max_total_bytes=max_total_bytes)
        self.lock = WriterLock(self.store.root)
        self.lock.acquire()
        self.markets = []
        self.metadata = None
        self.metadata_at = None
        self.previous_ladder = None
        self.previous_ticker = None
        self.backoff = self.poll_seconds
        self.stop = False
    def close(self):
        self.store.close()
        if self.lock is not None:
            self.lock.release()
            self.lock = None
    def _heartbeat(self):
        write_heartbeat(self.store.root, {
            'schema': 'runner-heartbeat-v1',
            'at': self.clock.wall().isoformat(),
            'pid': os.getpid(),
            'local_seq': self.store.state.get('local_seq'),
            'last_ticker': self.store.state.get('last_ticker'),
            'last_received_at': self.store.state.get('last_received_at'),
        })
    def note_gap(self, gap_seconds):
        previous = self.store.state.get('last_received_at')
        if not previous:
            return None
        delta = (self.clock.wall() - utc(previous)).total_seconds()
        if delta <= gap_seconds:
            return None
        record = {
            'schema': SCHEMA,
            'event': 'gap',
            'fail_closed': True,
            'previous_received_at': previous,
            'resumed_at': self.clock.wall().isoformat(),
            'gap_seconds': delta,
            'note': 'No snapshots were invented for the missing interval.',
        }
        self.store.append(record)
        return record
    def _control(self, event, **extra):
        record = {'schema': SCHEMA, 'event': event, 'at': self.clock.wall().isoformat(), **extra}
        return self.store.append(record)
    def discover(self, now):
        call = self.client.call(self.clock, '/markets', {'series_ticker': 'KXBTC15M', 'status': 'open', 'limit': 100})
        if not call['ok'] or not isinstance(call['body'], dict):
            return call
        found = []
        for raw in call['body'].get('markets') or []:
            view = market_view(raw, call['response_received_at'])
            if view:
                try:
                    found.append((Market.model_validate(raw), view))
                except Exception:
                    continue
        self.markets = found
        chosen = active_market([item[0] for item in found], now)
        if chosen is None:
            self.metadata = None
            return call
        for parsed, view in found:
            if parsed.ticker == chosen.ticker:
                self.metadata = view
                self.metadata_at = call['response_received_at']
                break
        return call
    def refresh_metadata(self, ticker):
        call = self.client.call(self.clock, '/markets/' + ticker)
        if call['ok'] and isinstance(call['body'], dict) and isinstance(call['body'].get('market'), dict):
            view = market_view(call['body']['market'], call['response_received_at'])
            if view:
                self.metadata = view
                self.metadata_at = call['response_received_at']
        return call
    def step(self):
        now = self.clock.wall()
        need_list = self.metadata is None
        if self.metadata is not None:
            remaining = (utc(self.metadata['close_time']) - now).total_seconds()
            if remaining < 20:
                need_list = True
        if need_list:
            listed = self.discover(now)
            if self.metadata is None:
                self.backoff = min(30.0, max(self.poll_seconds, self.backoff * 2))
                wrote = self._write_snapshot(None, listed, no_market=True)
                return wrote
        elif self.metadata_at is None or (now - utc(self.metadata_at)).total_seconds() >= self.metadata_seconds:
            self.refresh_metadata(self.metadata['ticker'])
        ticker = self.metadata['ticker']
        call = self.client.call(self.clock, f'/markets/{ticker}/orderbook', {'depth': 0})
        if not call['ok']:
            self.backoff = min(30.0, max(self.poll_seconds, self.backoff * 2))
        else:
            self.backoff = self.poll_seconds
        return self._write_snapshot(ticker, call, no_market=False)
    def _write_snapshot(self, ticker, call, no_market):
        ladders, flags = (None, {'empty': False, 'one_sided': False, 'malformed': False, 'missing': True})
        if call.get('ok') and not no_market:
            ladders, flags = classify_book(call.get('body'))
        received = call.get('response_received_at')
        after_close = False
        if self.metadata and received:
            after_close = utc(received) >= utc(self.metadata['close_time'])
        slow = call.get('latency_ms') is not None and call['latency_ms'] > self.slow_response_ms
        unchanged = False
        if ladders is not None and ticker == self.previous_ticker and ladders == self.previous_ladder:
            unchanged = True
        if ladders is not None:
            self.previous_ladder = ladders
            self.previous_ticker = ticker
        rolled = self.previous_ticker not in (None, ticker) and ticker is not None and self.store.state.get('last_ticker') not in (None, ticker)
        record = {
            'schema': SCHEMA,
            'event': 'snapshot',
            'provenance': {
                'source': 'kalshi-public-rest',
                'base_url': getattr(self.client, 'base', DEFAULT_BASE),
                'path': None if no_market else f'/markets/{ticker}/orderbook',
                'query': None if no_market else {'depth': 0},
                'note': 'response_received_at is local receive time, not an exchange event time',
            },
            'ticker': ticker,
            'request_started_at': call.get('request_started_at'),
            'response_received_at': received,
            'latency_ms': call.get('latency_ms'),
            'http_status': call.get('status'),
            'error': 'no_active_market' if no_market else call.get('error'),
            'flags': {**flags, 'error': bool(call.get('error') or no_market), 'local_after_close': after_close, 'slow_response': bool(slow)},
            'unchanged_from_previous': unchanged,
            'ticker_changed': bool(self.store.state.get('last_ticker') and ticker and self.store.state.get('last_ticker') != ticker),
            'market': self.metadata,
            'orderbook_fp': ladders,
        }
        if call.get('ok') and flags['malformed']:
            record['raw_orderbook_fp'] = (call.get('body') or {}).get('orderbook_fp')
        wrote = self.store.append(record)
        if received:
            self.store.state['last_received_at'] = received
        if ticker:
            self.store.state['last_ticker'] = ticker
        self.store._save_state()
        self._heartbeat()
        if not wrote:
            self._control('stopped', reason='max_bytes')
            self.stop = True
        return record
    def run(self, until, sleep=time.sleep, install_signals=True):
        until = utc(until)
        if install_signals:
            def _stop(signum, frame):
                self.stop = True
            signal.signal(signal.SIGINT, _stop)
            signal.signal(signal.SIGTERM, _stop)
        self.note_gap(max(self.poll_seconds * 5, 30))
        self.store.append(dependency_manifest({'until': until.isoformat(), 'poll_seconds': self.poll_seconds}))
        self._control('started', until=until.isoformat(), poll_seconds=self.poll_seconds)
        self._heartbeat()
        reason = 'until'
        while not self.stop and self.clock.wall() < until:
            self.step()
            if self.stop:
                reason = 'max_bytes'
                break
            delay = min(self.backoff, max(0.0, (until - self.clock.wall()).total_seconds()))
            if delay <= 0:
                break
            sleep(delay)
            if self.stop:
                reason = 'signal'
                break
        if self.clock.wall() >= until and not self.stop:
            reason = 'until'
        self._control('stopped', reason=reason)
        self.close()
        return reason

def main(argv=None):
    parser = argparse.ArgumentParser(description='Record public KXBTC15M order-book snapshots until a UTC time.')
    parser.add_argument('--output', default='data/depth-prospective')
    parser.add_argument('--until', required=True, help='Inclusive stop, UTC, for example 2026-11-06T23:20:00Z')
    parser.add_argument('--poll-seconds', type=float, default=3.0)
    parser.add_argument('--metadata-seconds', type=float, default=60.0)
    parser.add_argument('--segment-bytes', type=int, default=8 * 1024 * 1024)
    parser.add_argument('--max-total-bytes', type=int, default=512 * 1024 * 1024)
    parser.add_argument('--slow-response-ms', type=float, default=5000.0)
    parser.add_argument('--base-url', default=DEFAULT_BASE)
    args = parser.parse_args(argv)
    if args.poll_seconds < 1:
        raise SystemExit('--poll-seconds must be at least 1')
    output = assert_output_safe(args.output)
    until = parse_until(args.until)
    client = RestClient(args.base_url)
    try:
        recorder = Recorder(output, client, poll_seconds=args.poll_seconds, metadata_seconds=args.metadata_seconds, segment_bytes=args.segment_bytes, max_total_bytes=args.max_total_bytes, slow_response_ms=args.slow_response_ms)
    except LockBusy:
        client.close()
        raise SystemExit(2)
    try:
        reason = recorder.run(until)
    finally:
        client.close()
        recorder.close()
    print(json.dumps({'output': str(output), 'stopped': reason, 'bytes': recorder.store.bytes_used(), 'local_seq': recorder.store.state.get('local_seq')}))

if __name__ == '__main__':
    main()
