import json
import logging
import sqlite3
import threading
import pandas as pd
from decimal import Decimal, ROUND_HALF_EVEN
from btc15.execution import EXECUTION_MODEL, FEE_SCHEDULE

CENT = Decimal('0.01')

def _dec(value):
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_EVEN)

def _cents(value):
    return int((_dec(value) * 100).to_integral_value())

class Store:
    def __init__(self,path,initial=1000.):
        self.lock=threading.RLock()
        self.path=path
        self.db=sqlite3.connect(path,check_same_thread=False,timeout=20)
        self.legacy_readonly=self._detect_legacy()
        if self.legacy_readonly:
            return
        self.db.execute('PRAGMA journal_mode=WAL')
        self._executescript('''
        CREATE TABLE IF NOT EXISTS predictions(ticker TEXT, timestamp TEXT, close_time TEXT, probability REAL, baseline REAL, ml REAL, market_probability REAL, remaining REAL, version TEXT, features TEXT, PRIMARY KEY(ticker,timestamp));
        CREATE TABLE IF NOT EXISTS markets(ticker TEXT PRIMARY KEY, raw TEXT, result INTEGER);
        CREATE TABLE IF NOT EXISTS positions(id INTEGER PRIMARY KEY, ticker TEXT, timestamp TEXT, side TEXT, quantity INTEGER, price REAL, fee REAL, cost REAL, payout REAL, resolved_at TEXT);
        CREATE TABLE IF NOT EXISTS account(id INTEGER PRIMARY KEY CHECK(id=1), initial REAL, cash REAL);
        CREATE TABLE IF NOT EXISTS resolutions(ticker TEXT PRIMARY KEY, resolved_at TEXT);
        CREATE TABLE IF NOT EXISTS quotes(ticker TEXT, timestamp TEXT, yes_ask REAL, no_ask REAL, yes_depth REAL, no_depth REAL, fee_multiplier REAL, PRIMARY KEY(ticker,timestamp));
        CREATE TABLE IF NOT EXISTS observations(trade_id TEXT PRIMARY KEY, timestamp TEXT, raw TEXT);
        ''')
        self._add_column('quotes','observed_at','observed_at TEXT')
        self._add_column('resolutions','label','label INTEGER')
        self._add_column('positions','execution_model','execution_model TEXT')
        self._add_column('positions','intent_id','intent_id INTEGER')
        self._add_column('positions','confirm_book_at','confirm_book_at TEXT')
        self._executescript('''
        CREATE TABLE IF NOT EXISTS run_manifest(
            id INTEGER PRIMARY KEY CHECK(id=1),
            execution_model TEXT NOT NULL,
            fee_schedule TEXT NOT NULL,
            started_at TEXT NOT NULL,
            database_path TEXT,
            model_version TEXT,
            baseline_cash REAL,
            baseline_open_positions INTEGER,
            baseline_open_cost REAL,
            baseline_resolved_positions INTEGER,
            baseline_recorded_at TEXT,
            fills_armed INTEGER NOT NULL DEFAULT 0,
            armed_at TEXT,
            provenance TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS intents(
            id INTEGER PRIMARY KEY,
            ticker TEXT NOT NULL,
            decision_at TEXT NOT NULL,
            signal_book_at TEXT NOT NULL,
            side TEXT NOT NULL,
            limit_price TEXT NOT NULL,
            quantity TEXT NOT NULL,
            reserve TEXT NOT NULL,
            probability TEXT NOT NULL,
            fee_multiplier TEXT NOT NULL,
            price_ranges TEXT NOT NULL,
            status TEXT NOT NULL,
            confirm_book_at TEXT,
            position_id INTEGER,
            reason TEXT,
            execution_model TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settlement_conflicts(
            id INTEGER PRIMARY KEY,
            ticker TEXT NOT NULL,
            at TEXT NOT NULL,
            stored_label INTEGER,
            incoming_label INTEGER NOT NULL);
        ''')
        if initial<=0:raise ValueError('Initial bankroll must be positive')
        self.db.execute('INSERT OR IGNORE INTO account VALUES(1,?,?)',(initial,initial))
        self.db.commit()
    def _executescript(self,sql):
        self.db.executescript(sql)
    def _table(self,name):
        return self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone() is not None
    def _columns(self,table):
        if not self._table(table):return []
        return [row[1] for row in self.db.execute(f'PRAGMA table_info({table})')]
    def _add_column(self,table,name,ddl):
        if name not in self._columns(table):
            self.db.execute(f'ALTER TABLE {table} ADD COLUMN {ddl}')
    def _detect_legacy(self):
        """A file with paper positions or drifted cash and no v2 manifest is frozen.

        Opening it does not add tables, alter columns, or change journal mode.
        """
        if not self._table('positions'):
            return False
        manifest_rows=0
        if self._table('run_manifest'):
            manifest_rows=self.db.execute('SELECT COUNT(*) FROM run_manifest').fetchone()[0]
        if manifest_rows:
            return False
        positions=self.db.execute('SELECT COUNT(*) FROM positions').fetchone()[0]
        if positions:
            return True
        if self._table('account'):
            row=self.db.execute('SELECT initial,cash FROM account WHERE id=1').fetchone()
            if row and _cents(row[0])!=_cents(row[1]):
                return True
        return False
    def _row(self,sql,params=()):
        cur=self.db.execute(sql,params)
        row=cur.fetchone()
        if row is None:return None
        return dict(zip([d[0] for d in cur.description],row))
    def _rows(self,sql,params=()):
        cur=self.db.execute(sql,params)
        names=[d[0] for d in cur.description]
        return [dict(zip(names,row)) for row in cur.fetchall()]
    def frame(self,sql,params=()):
        with self.lock: return pd.read_sql_query(sql,self.db,params=params)
    def _write_blocked(self):
        return self.legacy_readonly
    def market(self,m):
        if self._write_blocked():return
        from btc15.domain import official_label
        with self.lock,self.db:
            self.db.execute('INSERT INTO markets VALUES(?,?,?) ON CONFLICT(ticker) DO UPDATE SET raw=excluded.raw,result=COALESCE(excluded.result,markets.result)',(m.ticker,m.model_dump_json(),official_label(m)))
    def quote(self,ticker,at,book,multiplier,observed_at=None):
        if self._write_blocked():return
        values=[]
        for opposite in ('no','yes'):
            levels=book.get(opposite,[])
            values.append((1-max(levels)[0],max(levels)[1]) if levels else (None,None))
        with self.lock,self.db:
            self.db.execute('INSERT OR IGNORE INTO quotes VALUES(?,?,?,?,?,?,?,?)',(ticker,at,*[values[0][0],values[1][0],values[0][1],values[1][1]],multiplier,observed_at))
    def prediction(self,p):
        if self._write_blocked():return
        with self.lock,self.db:
            self.db.execute('INSERT OR IGNORE INTO predictions VALUES(?,?,?,?,?,?,?,?,?,?)',tuple(p[k] for k in ['ticker','timestamp','close_time','probability','baseline','ml','market_probability','remaining','version'])+(json.dumps(p['features']),))
    def observation(self,t):
        self.observations([t])
    def observations(self,ticks):
        if self._write_blocked() or not ticks:return
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
        if self._write_blocked():return 'legacy_readonly'
        with self.lock,self.db:
            row=self._row('SELECT resolved_at,label FROM resolutions WHERE ticker=?',(ticker,))
            if row:
                stored=row['label']
                if stored is not None and int(stored)!=int(label):
                    self.db.execute('INSERT INTO settlement_conflicts(ticker,at,stored_label,incoming_label) VALUES(?,?,?,?)',(ticker,at,int(stored),int(label)))
                    return 'conflict'
                if stored is None:
                    return 'legacy_resolution'
                self._pay_unpaid(ticker,int(label),at)
                return 'idempotent'
            self.db.execute('INSERT INTO resolutions(ticker,resolved_at,label) VALUES(?,?,?)',(ticker,at,int(label)))
            self._pay_unpaid(ticker,int(label),at)
            return 'settled'
    def _pay_unpaid(self,ticker,label,at):
        rows=self.db.execute('SELECT id,side,quantity FROM positions WHERE ticker=? AND payout IS NULL',(ticker,)).fetchall()
        for ident,side,q in rows:
            won=(side=='yes')==bool(label)
            payout=_dec(q) if won else Decimal('0')
            self.db.execute('UPDATE positions SET payout=?,resolved_at=? WHERE id=?',(float(payout),at,ident))
            self._add_cash(payout)
    def _cash(self):
        return _dec(self.db.execute('SELECT cash FROM account WHERE id=1').fetchone()[0])
    def _add_cash(self,delta):
        updated=self._cash()+_dec(delta)
        self.db.execute('UPDATE account SET cash=? WHERE id=1',(float(updated),))
    def manifest_present(self):
        if not self._table('run_manifest'):return False
        with self.lock:
            return self.db.execute('SELECT COUNT(*) FROM run_manifest').fetchone()[0]>0
    def _manifest(self):
        if not self._table('run_manifest'):return None
        return self._row('SELECT * FROM run_manifest WHERE id=1')
    def execution_status(self):
        if self.legacy_readonly:
            return {'ok':False,'mode':'legacy','fills_armed':False,'execution_model':None,'fee_schedule':FEE_SCHEDULE,'reason':'Legacy paper rows are present and this file is not rewritten.'}
        row=None
        with self.lock:
            row=self._manifest()
        if not row:
            return {'ok':False,'mode':'uninitialized','fills_armed':False,'execution_model':EXECUTION_MODEL,'fee_schedule':FEE_SCHEDULE,'reason':'Baseline has not been recorded.'}
        if row['execution_model']!=EXECUTION_MODEL:
            return {'ok':False,'mode':'version_mismatch','fills_armed':False,'execution_model':row['execution_model'],'fee_schedule':row['fee_schedule'],'reason':'Manifest version does not match this process.'}
        return {'ok':True,'mode':'active','fills_armed':bool(row['fills_armed']),'execution_model':row['execution_model'],'fee_schedule':row['fee_schedule'],'started_at':row['started_at'],'baseline_recorded_at':row['baseline_recorded_at'],'armed_at':row['armed_at'],'baseline_cash':row['baseline_cash'],'baseline_open_positions':row['baseline_open_positions'],'baseline_open_cost':row['baseline_open_cost'],'baseline_resolved_positions':row['baseline_resolved_positions'],'model_version':row['model_version'],'database_path':row['database_path'],'provenance':row['provenance']}
    def fills_allowed(self):
        status=self.execution_status()
        return bool(status.get('ok') and status.get('fills_armed'))
    def start_run(self,provenance):
        """Record the pre-fill baseline. Does not arm fills and does not reset cash."""
        if self.legacy_readonly:
            return self.execution_status()
        from btc15.domain import now
        with self.lock,self.db:
            row=self._manifest()
            if row:
                if row['execution_model']!=EXECUTION_MODEL:
                    return {'ok':False,'mode':'version_mismatch','fills_armed':False,'execution_model':row['execution_model'],'reason':'Manifest version does not match this process.','created':False}
                return {'ok':True,'mode':'active','created':False,'fills_armed':bool(row['fills_armed']),'execution_model':row['execution_model'],'baseline_cash':row['baseline_cash'],'baseline_open_positions':row['baseline_open_positions'],'baseline_open_cost':row['baseline_open_cost'],'baseline_resolved_positions':row['baseline_resolved_positions'],'baseline_recorded_at':row['baseline_recorded_at']}
            account=self.db.execute('SELECT initial,cash FROM account WHERE id=1').fetchone()
            positions=self.db.execute('SELECT COUNT(*),COALESCE(SUM(CASE WHEN payout IS NULL THEN cost ELSE 0 END),0),SUM(CASE WHEN payout IS NULL THEN 0 ELSE 1 END) FROM positions').fetchone()
            open_n,open_cost,resolved=positions
            resolved=resolved or 0
            if open_n or resolved or _cents(account[0])!=_cents(account[1]):
                self.legacy_readonly=True
                return {'ok':False,'mode':'legacy','fills_armed':False,'reason':'Refusing to attach a new manifest to an existing paper ledger.','created':False}
            stamp=now().isoformat()
            self.db.execute('''INSERT INTO run_manifest(id,execution_model,fee_schedule,started_at,database_path,model_version,baseline_cash,baseline_open_positions,baseline_open_cost,baseline_resolved_positions,baseline_recorded_at,fills_armed,provenance)
                VALUES(1,?,?,?,?,?,?,?,?,?,?,0,?)''',(EXECUTION_MODEL,FEE_SCHEDULE,stamp,self.path,provenance.get('model_version'),float(account[1]),0,0.0,0,stamp,json.dumps(provenance)))
        return self.execution_status()|{'created':True}
    def arm_fills(self):
        """Allow intents only after a committed baseline. Does not reset the ledger."""
        if self.legacy_readonly:
            return {'ok':False,'reason':'legacy_readonly'}
        from btc15.domain import now
        with self.lock,self.db:
            row=self._manifest()
            if not row or not row['baseline_recorded_at']:
                return {'ok':False,'reason':'no_baseline'}
            if row['execution_model']!=EXECUTION_MODEL:
                return {'ok':False,'reason':'version_mismatch'}
            if row['fills_armed']:
                return {'ok':True,'reason':'already_armed','armed_at':row['armed_at']}
            stamp=now().isoformat()
            self.db.execute('UPDATE run_manifest SET fills_armed=1,armed_at=? WHERE id=1',(stamp,))
        return {'ok':True,'reason':'armed','armed_at':stamp}
    def position_count(self,ticker):
        with self.lock:
            return self.db.execute('SELECT COUNT(*) FROM positions WHERE ticker=?',(ticker,)).fetchone()[0]
    def available_cash(self):
        with self.lock:
            cash=self._cash()
            reserved=Decimal('0')
            if self._table('intents'):
                for (amount,) in self.db.execute("SELECT reserve FROM intents WHERE status='open'"):
                    reserved+=Decimal(amount)
            return cash-reserved
    def open_intent(self,ticker):
        if not self._table('intents'):return None
        with self.lock:
            return self._row("SELECT * FROM intents WHERE ticker=? AND status='open' ORDER BY id",(ticker,))
    def open_intents(self):
        if not self._table('intents'):return []
        with self.lock:
            return self._rows("SELECT * FROM intents WHERE status='open' ORDER BY id")
    def get_intent(self,ident):
        with self.lock:
            return self._row('SELECT * FROM intents WHERE id=?',(ident,))
    def insert_intent(self,fields):
        if self._write_blocked():return None
        with self.lock,self.db:
            if self.available_cash()<Decimal(fields['reserve']):
                return None
            cur=self.db.execute('''INSERT INTO intents(ticker,decision_at,signal_book_at,side,limit_price,quantity,reserve,probability,fee_multiplier,price_ranges,status,execution_model)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',(fields['ticker'],fields['decision_at'],fields['signal_book_at'],fields['side'],fields['limit_price'],fields['quantity'],fields['reserve'],fields['probability'],fields['fee_multiplier'],fields['price_ranges'],fields['status'],fields['execution_model']))
            return cur.lastrowid
    def expire_intent(self,ident,at,reason,confirm_book_at=None):
        if self._write_blocked():return False
        with self.lock,self.db:
            cur=self.db.execute("UPDATE intents SET status='expired',reason=?,confirm_book_at=COALESCE(?,confirm_book_at) WHERE id=? AND status='open'",(reason,confirm_book_at,ident))
            return cur.rowcount==1
    def fill_intent(self,ident,fill):
        """Convert one open intent into one position. A second call does not debit again."""
        if self._write_blocked():return None
        with self.lock,self.db:
            intent=self._row('SELECT * FROM intents WHERE id=?',(ident,))
            if intent is None:return None
            if intent['status']=='filled':return intent['position_id']
            if intent['status']!='open':return None
            debit=_dec(fill['cost'])
            if _cents(debit)>_cents(self._cash()):return None
            cur=self.db.execute('INSERT INTO positions(ticker,timestamp,side,quantity,price,fee,cost,execution_model,intent_id,confirm_book_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(fill['ticker'],fill['timestamp'],fill['side'],float(Decimal(fill['quantity'])),float(Decimal(fill['price'])),float(Decimal(fill['fee'])),float(debit),fill['execution_model'],ident,fill['confirm_book_at']))
            self._add_cash(-debit)
            self.db.execute("UPDATE intents SET status='filled',position_id=?,confirm_book_at=?,reason='filled' WHERE id=?",(cur.lastrowid,fill['confirm_book_at'],ident))
            return cur.lastrowid
