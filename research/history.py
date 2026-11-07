"""Public, resumable download of settled KXBTC15M labels and Coinbase candles.

No API key. No order book is reconstructed. Candle closes are not settlements.
The live paper database is not a valid cache path.
"""
from datetime import datetime, timedelta, timezone
import json
import time
from pathlib import Path
import httpx
import pandas as pd
from btc15.domain import Market, official_label, utc

KALSHI = 'https://external-api.kalshi.com/trade-api/v2'
COINBASE = 'https://api.exchange.coinbase.com'
# Snapshots this many seconds before close. All are outside the final minute.
REMAINING = (600, 300, 180)

def assert_safe_cache(path):
    text=str(path)
    if 'kalshi-paper' in text:
        raise ValueError('Refusing a path that looks like the live paper ledger')

def judge_market(raw):
    """Accept an official binary result. Do not copy expiration_value or quotes."""
    try:
        market=Market.model_validate(raw)
    except Exception:
        return None, 'parse'
    label=official_label(market)
    if label is None:
        return None, 'no_official_label'
    if not market.supported_rules or market.threshold is None:
        return None, 'unsupported_rules_or_strike'
    if market.settlement_ts is None or market.settlement_ts < market.close_time:
        return None, 'missing_settlement_time'
    return {
        'ticker':market.ticker,
        'open_time':market.open_time.isoformat(),
        'close_time':market.close_time.isoformat(),
        'resolved_at':market.settlement_ts.isoformat(),
        'threshold':market.threshold,
        'label':int(label),
        'status':market.status,
    }, 'accept'

def planned_snapshots(record):
    close, open_ = utc(record['close_time']), utc(record['open_time'])
    rows=[]
    for remaining in REMAINING:
        at=close-timedelta(seconds=remaining)
        if at<open_ or remaining<60:
            continue
        rows.append({'ticker':record['ticker'],'timestamp':at,'close_time':close,'threshold':record['threshold'],'threshold_published_at':open_,'remaining':remaining})
    return rows

def candles_frame(rows):
    frame=pd.DataFrame(rows, columns=['timestamp','low','high','open','close','volume'])
    if frame.empty:
        return frame
    frame['timestamp']=pd.to_datetime(frame.timestamp, unit='s', utc=True)+pd.Timedelta(seconds=60)
    return frame.sort_values('timestamp').drop_duplicates('timestamp')

def attach_prices(snapshots, candles):
    """Use the candle that ends at the snapshot. Missing minutes are skipped, not filled."""
    if candles.empty:
        return [], len(snapshots)
    indexed=candles.drop_duplicates('timestamp').set_index('timestamp')['close']
    kept=[]; missing=0
    for snap in snapshots:
        stamp=pd.Timestamp(snap['timestamp'])
        if stamp.tzinfo is None:
            stamp=stamp.tz_localize('UTC')
        if stamp not in indexed.index:
            missing+=1
            continue
        row=dict(snap)
        row['price']=float(indexed.loc[stamp])
        kept.append(row)
    return kept, missing

def gap_count(candles):
    if len(candles)<2:
        return 0
    delta=candles.timestamp.sort_values().diff().dt.total_seconds().dropna()
    return int((delta>90).sum())

class ThrottledGet:
    def __init__(self, interval=0.35, transport=None):
        self.interval=interval
        self.client=httpx.Client(timeout=20, headers={'User-Agent':'BTC15-research/0.1'}, transport=transport)
        self.last=0.0
        self.requests=0
    def get(self, url, params=None):
        time.sleep(max(0, self.interval-(time.monotonic()-self.last)))
        self.last=time.monotonic()
        self.requests+=1
        response=self.client.get(url, params=params)
        if response.status_code==429 or response.status_code>=500:
            time.sleep(2)
            response=self.client.get(url, params=params)
            self.requests+=1
        response.raise_for_status()
        return response.json()
    def close(self):
        self.client.close()

def load_jsonl(path):
    if not path.exists():
        return []
    rows=[]
    for line in path.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows

def collect_markets(fetch, page_size, max_pages, max_contracts):
    """Page the live settled list, newest first, and stop at the cap.

    `fetch(cursor)` returns the JSON body. Historical cutoff is recorded by the caller.
    Markets settled before that cutoff are not in this list; they are not invented.
    """
    cursor=''; pages=0; raw=[]; seen=set()
    while pages<max_pages:
        body=fetch(cursor)
        pages+=1
        batch=body.get('markets') or []
        if not batch:
            break
        for market in batch:
            ticker=market.get('ticker')
            if ticker in seen:
                continue
            seen.add(ticker)
            raw.append(market)
            if len(raw)>=max_contracts:
                return raw, pages, body.get('cursor') or ''
        cursor=body.get('cursor') or ''
        if not cursor:
            break
    return raw, pages, cursor

def collect(cache, max_contracts=160, max_pages=2, page_size=100, max_requests=30, interval=0.35, client=None):
    cache=Path(cache)
    assert_safe_cache(cache)
    cache.mkdir(parents=True, exist_ok=True)
    own=client is None
    http=client or ThrottledGet(interval)
    try:
        cached_lines=load_jsonl(cache/'markets.jsonl')
        fetched_at=datetime.now(timezone.utc).isoformat()
        if len(cached_lines)>=max_contracts:
            cutoff=json.loads((cache/'manifest.json').read_text()).get('cutoff') if (cache/'manifest.json').exists() else None
            raw=[row['market'] for row in cached_lines[:max_contracts]]
            pages=0
            cursor=''
        else:
            cutoff=http.get(f'{KALSHI}/historical/cutoff')
            def page(cursor):
                params={'series_ticker':'KXBTC15M','status':'settled','limit':page_size}
                if cursor:
                    params['cursor']=cursor
                return http.get(f'{KALSHI}/markets', params)
            raw, pages, cursor=collect_markets(page, page_size, max_pages, max_contracts)
            if http.requests>max_requests:
                raise RuntimeError('Request budget exhausted during market paging')
        accepted=[]; rejected={}
        lines=[]
        for market in raw:
            record, reason=judge_market(market)
            rejected[reason]=rejected.get(reason,0)+1
            lines.append({'fetched_at':fetched_at,'decision':reason,'ticker':market.get('ticker'),'market':market})
            if record:
                accepted.append(record)
        if pages:
            (cache/'markets.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in lines))
        candles=pd.DataFrame()
        if accepted:
            opens=min(utc(row['open_time']) for row in accepted)-timedelta(minutes=150)
            closes=max(utc(row['close_time']) for row in accepted)
            candles=_coinbase_range(http, opens, closes, max_requests)
        if not candles.empty:
            candles.to_csv(cache/'coinbase_1m.csv', index=False)
        manifest={
            'source_markets':f'{KALSHI}/markets?series_ticker=KXBTC15M&status=settled',
            'source_cutoff':f'{KALSHI}/historical/cutoff',
            'source_candles':f'{COINBASE}/products/BTC-USD/candles',
            'cutoff':cutoff,
            'fetched_at':fetched_at,
            'requests':http.requests,
            'pages':pages,
            'resumed_market_cache':pages==0,
            'next_cursor_saved':bool(cursor),
            'markets_fetched':len(raw),
            'decisions':rejected,
            'accepted':len(accepted),
            'candle_rows':int(len(candles)),
            'candle_gaps_over_90s':gap_count(candles) if not candles.empty else None,
            'candle_start':None if candles.empty else str(candles.timestamp.min()),
            'candle_end':None if candles.empty else str(candles.timestamp.max()),
            'notes':[
                'Settled payload bid/ask and expiration_value are stored in the raw cache only.',
                'They are not point-in-time quotes and are not labels.',
                'Coinbase closes are proxy prices, not BRTI and not settlement.',
            ],
        }
        (cache/'manifest.json').write_text(json.dumps(manifest, indent=2, default=str))
        pd.DataFrame(accepted, columns=['ticker','open_time','close_time','resolved_at','threshold','label','status']).to_csv(cache/'accepted_contracts.csv', index=False)
        return manifest
    finally:
        if own:
            http.close()

def _coinbase_range(http, start, end, max_requests):
    rows=[]; cursor=start
    while cursor<end:
        if http.requests>=max_requests:
            break
        chunk_end=min(end, cursor+timedelta(minutes=280))
        payload=http.get(f'{COINBASE}/products/BTC-USD/candles', {'granularity':60,'start':cursor.isoformat(),'end':chunk_end.isoformat()})
        if isinstance(payload, list):
            rows.extend(payload)
        cursor=chunk_end
    return candles_frame(rows)

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description='Download a bounded public KXBTC15M sample and Coinbase candles.')
    parser.add_argument('--cache', default='data/historical')
    parser.add_argument('--max-contracts', type=int, default=160)
    parser.add_argument('--max-pages', type=int, default=2)
    parser.add_argument('--max-requests', type=int, default=30)
    args=parser.parse_args()
    print(json.dumps(collect(args.cache, max_contracts=args.max_contracts, max_pages=args.max_pages, max_requests=args.max_requests), indent=2, default=str))
