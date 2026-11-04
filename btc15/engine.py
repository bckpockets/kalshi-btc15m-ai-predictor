import asyncio
import atexit
import copy
import logging
import os
import threading
import time
from collections import deque
import pandas as pd
from dotenv import load_dotenv
from btc15.domain import now, active_market, official_label
from btc15.feeds import Kalshi, Coinbase, coinbase_stream
from btc15.features import build_features
from btc15.model import statistical, Predictor
from btc15.store import Store
from btc15.paper import Risk, entry

class Engine:
    """One cache_resource instance per Streamlit process, shared by sessions."""
    def __init__(self):
        load_dotenv()
        logging.basicConfig(level=os.getenv('LOG_LEVEL','INFO'))
        self.lock=threading.RLock();self.stop=threading.Event()
        self.store=Store(os.getenv('DATABASE_PATH','runtime.sqlite'),float(os.getenv('STARTING_BANKROLL','1000')))
        self.k=Kalshi(os.getenv('KALSHI_BASE','https://external-api.kalshi.com/trade-api/v2'))
        self.c=Coinbase();self.model=Predictor(os.getenv('MODEL_PATH','artifacts/model.joblib'))
        self.state={'error':'Connecting to public feeds…','market':None,'prediction':None,'tick':None,'candles':pd.DataFrame(),'book':{},'trades':[]}
        self.ticks=deque(maxlen=5000);self.ids=set();self.enabled=False;self.allow_proxy=False;self.risk=Risk()
        self.age=float(os.getenv('MAX_DATA_AGE','10'));self.poll=max(1,float(os.getenv('POLL_SECONDS','3')))
        self.threads=[threading.Thread(target=self.run,daemon=True),threading.Thread(target=lambda:asyncio.run(coinbase_stream(self.tick,self.stop)),daemon=True)]
        for t in self.threads:t.start()
        atexit.register(self.close)
    def close(self):
        self.stop.set()
        for t in self.threads:t.join(timeout=2)
    def tick(self,t):
        with self.lock:
            if t['trade_id'] in self.ids:return
            if self.state['tick'] and t['timestamp'] < self.state['tick']['timestamp']:return
            if len(self.ticks)==self.ticks.maxlen:self.ids.discard(self.ticks[0]['trade_id'])
            self.ids.add(t['trade_id']);self.ticks.append(t);self.state['tick']=t
        self.store.observation(t)
    def snapshot(self):
        with self.lock:return copy.deepcopy(self.state)
    def configure(self, enabled, allow_proxy, risk):
        with self.lock:self.enabled=enabled;self.allow_proxy=allow_proxy;self.risk=risk
    def run(self):
        candles=pd.DataFrame();next_candles=0;next_settle=0;series=None;next_series=0
        while not self.stop.is_set():
            try:
                started=time.monotonic()
                with self.lock:tick=self.state['tick']
                if tick is None or (now()-tick['timestamp']).total_seconds()>3:self.tick(self.c.ticker())
                if time.monotonic()>=next_candles:
                    candles=self.c.candles();imbalance=self.c.book();next_candles=time.monotonic()+30
                    try:
                        stats24=self.c.get('/products/BTC-USD/stats')
                        with self.lock:self.state['stats24']=stats24
                    except Exception:logging.warning('24h stats unavailable')
                if time.monotonic()>=next_series:
                    series=self.k.get('/series/KXBTC15M')['series'];next_series=time.monotonic()+300
                market=active_market(self.k.markets(),now());market_received=now();book_received=None
                book={};trades=[];book_error='';prediction=None
                if market:
                    self.store.market(market)
                    try:
                        book=self.k.book(market.ticker);book_received=now();trades=self.k.trades(market.ticker)
                    except Exception as e:book_error=str(e)
                with self.lock:tick=copy.deepcopy(self.state['tick']);recent=list(self.ticks);enabled=self.enabled;allow_proxy=self.allow_proxy;risk=copy.copy(self.risk)
                at=now()
                error=''
                try:
                    if market is None:raise ValueError('No active contract returned; waiting for next interval')
                    if (at-market_received).total_seconds()>self.age:raise ValueError('Kalshi snapshot became stale during collection')
                    if not market.supported_rules:raise ValueError('Unrecognized contract rules: prediction suspended')
                    if market.threshold is None:raise ValueError('Official opening threshold not yet published')
                    age=(at-tick['timestamp']).total_seconds()
                    if age<0 or age>self.age:raise ValueError('Coinbase observation is stale or future-dated')
                    remaining=market.remaining(at)
                    # Ticker side denotes maker side: maker sell means aggressive buyer.
                    recent=[t for t in recent if 0 <= (at-t['timestamp']).total_seconds() <= 60]
                    total=sum(t['size'] for t in recent if t['side'] in ('buy','sell'))
                    pressure=sum(t['size']*(1 if t['side']=='sell' else -1) for t in recent if t['side'] in ('buy','sell'))/total if total else 0
                    f=build_features(candles,at,tick['price'],market.threshold,remaining,imbalance,pressure)
                    stats=statistical(tick['price'],market.threshold-.005,f['sigma'],remaining,float(os.getenv('BASIS_SIGMA_USD','25')))
                    p,ml,version=self.model.predict(f,stats['probability'])
                    bid=market.yes_bid_dollars;ask=market.yes_ask_dollars
                    midpoint=(bid+ask)/2 if bid is not None and ask is not None else None
                    prediction={'ticker':market.ticker,'timestamp':at.isoformat(),'close_time':market.close_time.isoformat(),'probability':p,'baseline':stats['probability'],'ml':ml,'market_probability':midpoint,'remaining':remaining,'version':version,'features':f,'stats':stats}
                    self.store.prediction(prediction)
                    fee_ok=series.get('fee_type')=='quadratic' and series.get('fee_multiplier') is not None
                    self.store.quote(market.ticker,at.isoformat(),book,float(series['fee_multiplier']) if fee_ok else None,book_received.isoformat() if book_received else None)
                    if fee_ok and book_received and (at-book_received).total_seconds()<=self.age:entry(self.store,market,p,book,at,remaining,risk,float(series['fee_multiplier']),enabled,allow_proxy)
                except ValueError as e:error=str(e)
                with self.lock:self.state.update(market=market,tick=tick,candles=candles,book=book,trades=trades,prediction=prediction,error=error,book_error=book_error,series=series,updated=at,market_received=market_received,book_received=book_received,latency_ms=(time.monotonic()-started)*1000)
                if time.monotonic()>=next_settle:
                    for ticker in self.store.pending():
                        if self.stop.is_set():break
                        m=self.k.market(ticker);self.store.market(m);label=official_label(m)
                        if label is not None:self.store.settle(ticker,label,now().isoformat())
                    next_settle=time.monotonic()+60
            except Exception as e:
                logging.exception('Collector cycle failed')
                with self.lock:self.state.update(error=f'{type(e).__name__}: {e}',prediction=None)
            self.stop.wait(self.poll)
