import json
import logging
import sqlite3
import threading
import pandas as pd

class Store:
    def __init__(self,path,initial=1000.):
        self.lock=threading.RLock()
        self.db=sqlite3.connect(path,check_same_thread=False,timeout=20)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS predictions(ticker TEXT, timestamp TEXT, close_time TEXT, probability REAL, baseline REAL, ml REAL, market_probability REAL, remaining REAL, version TEXT, features TEXT, PRIMARY KEY(ticker,timestamp));
        CREATE TABLE IF NOT EXISTS markets(ticker TEXT PRIMARY KEY, raw TEXT, result INTEGER);
        CREATE TABLE IF NOT EXISTS positions(id INTEGER PRIMARY KEY, ticker TEXT, timestamp TEXT, side TEXT, quantity INTEGER, price REAL, fee REAL, cost REAL, payout REAL, resolved_at TEXT);
        CREATE TABLE IF NOT EXISTS account(id INTEGER PRIMARY KEY CHECK(id=1), initial REAL, cash REAL);
        CREATE TABLE IF NOT EXISTS resolutions(ticker TEXT PRIMARY KEY, resolved_at TEXT);
        CREATE TABLE IF NOT EXISTS quotes(ticker TEXT, timestamp TEXT, yes_ask REAL, no_ask REAL, yes_depth REAL, no_depth REAL, fee_multiplier REAL, PRIMARY KEY(ticker,timestamp));
        CREATE TABLE IF NOT EXISTS observations(trade_id TEXT PRIMARY KEY, timestamp TEXT, raw TEXT);
        ''')
        if 'observed_at' not in [row[1] for row in self.db.execute('PRAGMA table_info(quotes)')]:
            self.db.execute('ALTER TABLE quotes ADD COLUMN observed_at TEXT')
        if initial<=0:raise ValueError('Initial bankroll must be positive')
        self.db.execute('INSERT OR IGNORE INTO account VALUES(1,?,?)',(initial,initial));self.db.commit()
    def frame(self,sql,params=()):
        with self.lock: return pd.read_sql_query(sql,self.db,params=params)
    def market(self,m):
        from btc15.domain import official_label
        with self.lock,self.db:
            self.db.execute('INSERT INTO markets VALUES(?,?,?) ON CONFLICT(ticker) DO UPDATE SET raw=excluded.raw,result=COALESCE(excluded.result,markets.result)',(m.ticker,m.model_dump_json(),official_label(m)))
    def quote(self,ticker,at,book,multiplier,observed_at=None):
        values=[]
        for opposite in ('no','yes'):
            levels=book.get(opposite,[])
            values.append((1-max(levels)[0],max(levels)[1]) if levels else (None,None))
        with self.lock,self.db:
            self.db.execute('INSERT OR IGNORE INTO quotes VALUES(?,?,?,?,?,?,?,?)',(ticker,at,*[values[0][0],values[1][0],values[0][1],values[1][1]],multiplier,observed_at))
    def prediction(self,p):
        with self.lock,self.db:
            self.db.execute('INSERT OR IGNORE INTO predictions VALUES(?,?,?,?,?,?,?,?,?,?)',tuple(p[k] for k in ['ticker','timestamp','close_time','probability','baseline','ml','market_probability','remaining','version'])+(json.dumps(p['features']),))
    def observation(self,t):
        self.observations([t])
    def observations(self,ticks):
        if not ticks:return
        rows=[]
        for t in ticks:
            ts=t['timestamp']
            rows.append((t['trade_id'], ts.isoformat() if hasattr(ts,'isoformat') else str(ts), json.dumps(t,default=str)))
        with self.lock,self.db:
            self.db.executemany('INSERT OR IGNORE INTO observations VALUES(?,?,?)', rows)
    def pending(self, at=None):
        """Unresolved markets already at or past close_time.

        Currently open contracts are not settlement candidates. Polling them
        forever was a request leak; they stay out of this list until close_time.
        """
        from btc15.domain import utc, now as domain_now
        cutoff=domain_now() if at is None else utc(at)
        with self.lock:
            rows=self.db.execute('SELECT ticker, raw FROM markets WHERE ticker NOT IN (SELECT ticker FROM resolutions) ORDER BY ticker').fetchall()
        found=[]
        for ticker, raw in rows:
            try:
                close=utc(json.loads(raw)['close_time'])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                logging.warning('Market %s has no readable close_time; not a settlement candidate', ticker)
                continue
            if close<=cutoff:found.append(ticker)
        return found
    def settle(self,ticker,label,at):
        if label not in (0,1): raise ValueError('Official binary result required')
        with self.lock,self.db:
            self.db.execute('INSERT OR IGNORE INTO resolutions VALUES(?,?)',(ticker,at))
            rows=self.db.execute('SELECT id,side,quantity FROM positions WHERE ticker=? AND payout IS NULL',(ticker,)).fetchall()
            for ident,side,q in rows:
                payout=q*int((side=='yes')==bool(label))
                self.db.execute('UPDATE positions SET payout=?,resolved_at=? WHERE id=?',(payout,at,ident))
                self.db.execute('UPDATE account SET cash=cash+? WHERE id=1',(payout,))
