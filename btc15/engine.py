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
from btc15.model import statistical, Predictor, BASIS_SIGMA_USD
from btc15.store import Store
from btc15.paper import Risk
from btc15.execution import coverage_allows_entry, step_execution, TAKER_FEE_TYPES, EXECUTION_MODEL, FEE_SCHEDULE

def fee_multiplier(series):
    """Quadratic multiplier, or None if series metadata is missing or unsupported.

    Cold start leaves series as None until the first successful GET. Never call
    dict methods on that missing payload.
    """
    if not isinstance(series, dict):
        return None
    if series.get('fee_type') not in TAKER_FEE_TYPES or series.get('fee_multiplier') is None:
        return None
    try:
        value=float(series['fee_multiplier'])
    except (TypeError, ValueError):
        return None
    return value if value>0 else None

def book_needed(market, at):
    """GET the order book only when paper entries or the edge panel can use it.

    Both need a forecast: recognized rules, a published threshold, and at least
    60 seconds left. The final minute has no forecast, so a book is not fetched.
    """
    if market is None or not market.supported_rules or market.threshold is None:
        return False
    return market.remaining(at)>=60

class Engine:
    """One cache_resource instance per Streamlit process, shared by sessions."""
    def __init__(self):
        load_dotenv()
        logging.basicConfig(level=os.getenv('LOG_LEVEL','INFO'))
        self.lock=threading.RLock();self.stop=threading.Event()
        self.store=Store(os.getenv('DATABASE_PATH','runtime.sqlite'),float(os.getenv('STARTING_BANKROLL','1000')))
        self.discovery=max(5., float(os.getenv('MARKET_DISCOVERY_SECONDS','60')))
        self.candles_every=max(1., float(os.getenv('CANDLE_SECONDS','60')))
        self.trade_every=max(1, int(os.getenv('TRADES_EVERY_CYCLES','5')))
        self.k=Kalshi(os.getenv('KALSHI_BASE','https://external-api.kalshi.com/trade-api/v2'), list_ttl=self.discovery)
        self.c=Coinbase();self.model=Predictor(os.getenv('MODEL_PATH','artifacts/model.joblib'))
        self.state={'error':'Connecting to public feeds…','market':None,'prediction':None,'tick':None,'candles':pd.DataFrame(),'book':{},'trades':[]}
        self.ticks=deque(maxlen=5000);self.ids=set();self.enabled=False;self.allow_proxy=False;self.risk=Risk()
        self.age=float(os.getenv('MAX_DATA_AGE','10'));self.poll=max(1,float(os.getenv('POLL_SECONDS','3')))
        self.min_latency=max(0.,float(os.getenv('EXECUTION_LATENCY_SECONDS','3')))
        self.max_intent_age=max(self.min_latency,float(os.getenv('EXECUTION_INTENT_SECONDS','20')))
        # Persist at most one Coinbase observation per exchange-timestamp second.
        self._obs=[];self._obs_flush=time.monotonic();self._obs_second=None
        version=self.model.artifact['version'] if self.model.artifact else 'statistical-proxy-v1'
        self.execution=self.store.start_run({'execution_model':EXECUTION_MODEL,'fee_schedule':FEE_SCHEDULE,'model_version':version,'model_path':os.getenv('MODEL_PATH','artifacts/model.joblib'),'database_path':self.store.path,'poll_seconds':self.poll,'max_data_age':self.age,'min_latency_seconds':self.min_latency,'max_intent_seconds':self.max_intent_age,'basis_sigma_usd':os.getenv('BASIS_SIGMA_USD','25'),'note':'Coinbase is not BRTI. No orders are submitted. Fills require a later book.'})
        self.state['execution']=self.execution
        self._last_forecast_at=None
        self.threads=[threading.Thread(target=self.run,daemon=True),threading.Thread(target=lambda:asyncio.run(coinbase_stream(self.tick,self.stop)),daemon=True)]
        for t in self.threads:t.start()
        atexit.register(self.close)
    def close(self):
        self.stop.set()
        for t in self.threads:t.join(timeout=2)
        self.flush_observations()
    def tick(self,t):
        batch=None
        with self.lock:
            if t['trade_id'] in self.ids:return
            if self.state['tick'] and t['timestamp'] < self.state['tick']['timestamp']:return
            if len(self.ticks)==self.ticks.maxlen:self.ids.discard(self.ticks[0]['trade_id'])
            self.ids.add(t['trade_id']);self.ticks.append(t);self.state['tick']=t
            # In-memory window stays complete for 60s pressure. SQLite keeps the
            # last print of each UTC second, flushed in batches.
            second=t['timestamp'].replace(microsecond=0)
            if second!=self._obs_second:
                self._obs.append(t);self._obs_second=second
            elif self._obs:
                self._obs[-1]=t
            if len(self._obs)>=30 or time.monotonic()-self._obs_flush>=5:
                batch=self._obs;self._obs=[];self._obs_flush=time.monotonic()
        if batch and not self.store.legacy_readonly:
            try:self.store.observations(batch)
            except Exception:
                logging.warning('Observation batch failed', exc_info=True)
    def flush_observations(self):
        with self.lock:
            batch=self._obs;self._obs=[];self._obs_flush=time.monotonic()
        if not batch or self.store.legacy_readonly:return
        try:self.store.observations(batch)
        except Exception:
            logging.warning('Observation flush failed', exc_info=True)
    def snapshot(self):
        with self.lock:return copy.deepcopy(self.state)
    def configure(self, enabled, allow_proxy, risk):
        with self.lock:self.enabled=enabled;self.allow_proxy=allow_proxy;self.risk=risk
    def run(self):
        candles=pd.DataFrame();imbalance=0.;next_candles=0;next_settle=0
        series=None;next_series=0;next_discovery=0;cached=[];discovered=False
        cycle=0;book={};trades=[];book_received=None;book_ticker=None
        while not self.stop.is_set():
            try:
                started=time.monotonic()
                with self.lock:tick=self.state['tick']
                if tick is None or (now()-tick['timestamp']).total_seconds()>3:self.tick(self.c.ticker())
                if time.monotonic()>=next_candles:
                    # One-minute bars do not need a 30s refresh. Imbalance and 24h stats ride along.
                    candles=self.c.candles();imbalance=self.c.book();next_candles=time.monotonic()+self.candles_every
                    try:
                        stats24=self.c.get('/products/BTC-USD/stats')
                        with self.lock:self.state['stats24']=stats24
                    except Exception:logging.warning('24h stats unavailable')
                if time.monotonic()>=next_series:
                    try:
                        body=self.k.get('/series/KXBTC15M')
                        found=body.get('series') if isinstance(body, dict) else None
                        if isinstance(found, dict):
                            series=found;next_series=time.monotonic()+300
                        else:
                            logging.warning('Series payload missing series object')
                            next_series=time.monotonic()+30
                    except Exception:
                        logging.warning('Series metadata unavailable', exc_info=True)
                        next_series=time.monotonic()+30
                wall=now();current=active_market(cached, wall) if cached else None
                # Pull the next full-list discovery forward once near rollover, then
                # the post-discovery gap (15s while close, else MARKET_DISCOVERY_SECONDS) holds.
                if current is not None and current.remaining(wall)<15 and next_discovery-time.monotonic()>15:
                    next_discovery=time.monotonic()
                if (not discovered) or time.monotonic()>=next_discovery:
                    try:
                        cached=self.k.markets(force=True);discovered=True
                        active_now=active_market(cached, now())
                        gap=self.discovery
                        if active_now is None or active_now.remaining(now())<30:gap=min(15., self.discovery)
                        next_discovery=time.monotonic()+gap
                    except Exception:
                        logging.warning('Kalshi market discovery failed', exc_info=True)
                        next_discovery=time.monotonic()+15
                current=active_market(cached, now()) if cached else None
                market=None;market_received=None
                if current is not None:
                    try:
                        refreshed=self.k.market(current.ticker)
                        received=now()
                        cached=[refreshed if m.ticker==refreshed.ticker else m for m in cached]
                        chosen=active_market(cached, received)
                        if chosen is not None and chosen.ticker==refreshed.ticker:
                            market=chosen;market_received=received
                    except Exception:
                        logging.warning('Kalshi current-ticker refresh failed', exc_info=True)
                        # Keep the last discovered row visible, but do not treat it as a fresh snapshot.
                        market=current
                        market_received=None
                fetched_book=False;book_error=''
                ticker_changed=market is not None and market.ticker!=book_ticker
                if market is None:
                    book={};trades=[];book_received=None;book_ticker=None
                elif ticker_changed:
                    book={};trades=[];book_received=None;book_ticker=market.ticker
                if market and not self.store.legacy_readonly:
                    self.store.market(market)
                    if market_received is not None and book_needed(market, market_received):
                        try:
                            book=self.k.book(market.ticker);book_received=now();fetched_book=True
                        except Exception as e:book_error=str(e)
                    if ticker_changed or cycle%self.trade_every==0:
                        try:trades=self.k.trades(market.ticker)
                        except Exception as e:book_error=book_error or str(e)
                with self.lock:tick=copy.deepcopy(self.state['tick']);recent=list(self.ticks);enabled=self.enabled;allow_proxy=self.allow_proxy;risk=copy.copy(self.risk)
                at=now()
                error=''
                prediction=None
                try:
                    if market is None:raise ValueError('No active contract returned; waiting for next interval')
                    if market_received is None or (at-market_received).total_seconds()>self.age:raise ValueError('Kalshi snapshot became stale during collection')
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
                    # Official threshold only. statistical() applies the half-cent boundary.
                    # Do not call rolling_basis_sigma here: this process has Coinbase prints,
                    # not paired CF Benchmarks BRTI samples. $25 stays the assumption.
                    basis=float(os.getenv('BASIS_SIGMA_USD', BASIS_SIGMA_USD))
                    stats=statistical(tick['price'],market.threshold,f['sigma'],remaining,basis)
                    p,ml,version=self.model.predict(f,stats['probability'])
                    bid=market.yes_bid_dollars;ask=market.yes_ask_dollars
                    midpoint=(bid+ask)/2 if bid is not None and ask is not None else None
                    prediction={'ticker':market.ticker,'timestamp':at.isoformat(),'close_time':market.close_time.isoformat(),'probability':p,'baseline':stats['probability'],'ml':ml,'market_probability':midpoint,'remaining':remaining,'version':version,'features':f,'stats':stats}
                    self.store.prediction(prediction)
                    multiplier=fee_multiplier(series)
                    if fetched_book:
                        self.store.quote(market.ticker,at.isoformat(),book,multiplier,book_received.isoformat() if book_received else None)
                except ValueError as e:error=str(e)
                coverage_ok=coverage_allows_entry(self._last_forecast_at, at, self.poll)
                if prediction is not None:
                    self._last_forecast_at=at
                if not self.store.legacy_readonly:
                    try:
                        step_execution(self.store,market,prediction['probability'] if prediction else None,book,book_received,at,risk,fee_multiplier(series),enabled,allow_proxy,self.age,self.min_latency,self.max_intent_age,bool(fetched_book and book_received is not None),coverage_ok)
                    except Exception:
                        logging.exception('Paper execution step failed')
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
            cycle+=1
            self.flush_observations()
            self.stop.wait(self.poll)
