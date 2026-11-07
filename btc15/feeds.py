import asyncio
import json
import logging
import random
import threading
import time
import httpx
import pandas as pd
from websockets.asyncio.client import connect
from btc15.domain import Market, utc, now
from datetime import timedelta

class Rest:
    """Serial rate limiting and bounded retries; all requests are GET-only."""
    def __init__(self, base):
        self.client = httpx.Client(base_url=base, timeout=8, headers={'User-Agent':'BTC15-research/0.1'})
        self.lock = threading.Lock()
        self.last = 0.0
    def get(self, path, **params):
        with self.lock:
            for attempt in range(4):
                time.sleep(max(0, .25-(time.monotonic()-self.last)))
                self.last = time.monotonic()
                try:
                    r = self.client.get(path, params=params)
                    if r.status_code == 429 or r.status_code >= 500:
                        time.sleep(min(8, float(r.headers.get('Retry-After', 2**attempt))))
                        continue
                    r.raise_for_status()
                    return r.json()
                except (httpx.TimeoutException, httpx.NetworkError):
                    if attempt == 3:
                        raise
                    time.sleep(2**attempt+random.random()*.2)
            raise RuntimeError('Provider retry budget exhausted')
    def close(self):
        self.client.close()

class Kalshi(Rest):
    def __init__(self, base, list_ttl=60.):
        super().__init__(base)
        self.list_ttl=float(list_ttl)
        self._markets_cache=None
    def markets(self, status='open', force=False):
        """Open KXBTC15M list, served from a short TTL cache unless force=True.

        The collector should still refresh the current ticker every cycle and
        call this only for discovery. The cache is a backstop against a full
        list on every poll.
        """
        now_m=time.monotonic()
        cached=self._markets_cache
        if not force and cached and cached[0]==status and now_m-cached[1]<self.list_ttl:
            return list(cached[2])
        cursor = ''
        result = []
        while True:
            body = self.get('/markets', series_ticker='KXBTC15M', status=status, limit=100, cursor=cursor)
            result.extend(Market.model_validate(m) for m in body['markets'])
            cursor = body.get('cursor','') or ''
            if not cursor:
                break
        self._markets_cache=(status, now_m, result)
        return list(result)
    def market(self, ticker):
        return Market.model_validate(self.get('/markets/'+ticker)['market'])
    def book(self, ticker):
        book = self.get('/markets/'+ticker+'/orderbook', depth=20)['orderbook_fp']
        return {side: sorted([(float(p),float(q)) for p,q in book.get(side+'_dollars',[])], reverse=True) for side in ('yes','no')}
    def trades(self, ticker):
        return self.get('/markets/trades', ticker=ticker, limit=20).get('trades',[])

class Coinbase(Rest):
    def __init__(self):
        super().__init__('https://api.exchange.coinbase.com')
    def candles(self, granularity=60):
        end=now().replace(second=0,microsecond=0)
        start=end-timedelta(seconds=granularity*240)
        data = self.get('/products/BTC-USD/candles', granularity=granularity,start=start.isoformat(),end=end.isoformat())
        df = pd.DataFrame(data, columns=['timestamp','low','high','open','close','volume'])
        df['timestamp'] = pd.to_datetime(df.timestamp, unit='s', utc=True)+pd.Timedelta(seconds=granularity)
        return df.sort_values('timestamp').drop_duplicates('timestamp')
    def ticker(self):
        data = self.get('/products/BTC-USD/ticker')
        return {'timestamp':utc(data['time']), 'price':float(data['price']), 'bid':float(data['bid']), 'ask':float(data['ask']), 'size':float(data['size']), 'trade_id':str(data['trade_id']), 'side':'unknown', 'source':'Coinbase REST'}
    def book(self):
        d = self.get('/products/BTC-USD/book', level=2)
        b = sum(float(x[1]) for x in d['bids'][:20]); a = sum(float(x[1]) for x in d['asks'][:20])
        return (b-a)/(b+a) if b+a else 0

async def coinbase_stream(on_tick, stop):
    delay = 1
    while not stop.is_set():
        try:
            async with connect('wss://ws-feed.exchange.coinbase.com', ping_interval=20, close_timeout=2, open_timeout=8) as ws:
                await ws.send(json.dumps({'type':'subscribe','product_ids':['BTC-USD'],'channels':['ticker']}))
                delay = 1
                while not stop.is_set():
                    try:
                        d = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                    except asyncio.TimeoutError:
                        continue
                    if d.get('type') == 'ticker':
                        on_tick({'timestamp':utc(d['time']), 'price':float(d['price']), 'bid':float(d['best_bid']), 'ask':float(d['best_ask']), 'size':float(d['last_size']), 'trade_id':str(d['trade_id']), 'side':d.get('side','unknown'), 'source':'Coinbase WebSocket'})
        except Exception:
            logging.exception('Coinbase stream disconnected; REST fallback remains available')
            for _ in range(delay*10):
                if stop.is_set(): return
                await asyncio.sleep(.1)
            delay = min(30, delay*2)
