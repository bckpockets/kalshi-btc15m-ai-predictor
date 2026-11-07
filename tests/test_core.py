from datetime import datetime,timedelta,timezone
import numpy as np
import pandas as pd
import pytest
from btc15.domain import Market,active_market,official_label,utc
from btc15.engine import book_needed,fee_multiplier
from btc15.features import build_features
from btc15.model import statistical,rolling_basis_sigma,BASIS_SIGMA_USD
from btc15.paper import fee,economics,entry,Risk
from btc15.store import Store
from research.dataset import partitions,validate
from research.backtest import backtest

AT=datetime(2026,1,1,12,5,tzinfo=timezone.utc)

def market(**kwargs):
    return Market(**({'ticker':'KXBTC15M-TEST','open_time':AT-timedelta(minutes=5),'close_time':AT+timedelta(minutes=10),'status':'active','floor_strike':100000,'yes_bid_dollars':'.40','yes_ask_dollars':'.42','no_bid_dollars':'.58','no_ask_dollars':'.60','rules_primary':"If the simple average of the sixty seconds of CF Benchmarks' BRTI is at least the opening average"}|kwargs))

def candles():
    p=100000+np.sin(np.arange(60))*20+np.arange(60)
    return pd.DataFrame({'timestamp':pd.date_range(end=AT,periods=60,freq='min'),'close':p,'volume':np.arange(60)+1,'open':p,'low':p-1,'high':p+1})

def test_rollover():
    a=market();b=market(ticker='NEXT',open_time=a.close_time,close_time=a.close_time+timedelta(minutes=15))
    assert active_market([b,a],AT)==a
    assert active_market([a,b],a.close_time)==b
    assert active_market([a],a.close_time) is None

def test_labels_require_official_final_result():
    assert official_label(market(result='yes')) is None
    assert official_label(market(status='settled',result='yes'))==1
    assert official_label(market(status='finalized',result='no'))==0
    assert official_label(market(status='settled',result='void')) is None

def test_missing_threshold_rules_timezone():
    assert market(floor_strike=None).threshold is None
    assert not market(rules_primary='price rises').supported_rules
    assert market().supported_rules
    with pytest.raises(ValueError):utc(datetime(2026,1,1))
    assert market().yes_bid_dollars==.4

@pytest.mark.parametrize('distance',[-500,-10,0,10,500])
def test_probabilities(distance):
    s=statistical(100000+distance,100000,.0001,300)
    p=s['probability'];assert 0<p<1;assert p+(1-p)==1
    # Half-cent lives inside statistical(); equal spot and official strike is not the boundary.
    boundary=statistical(100000-.005,100000,.0001,300)['probability']
    assert boundary==pytest.approx(.5,abs=1e-9)
    if distance<0:assert p<boundary
    else:assert p>=boundary

def test_half_cent_applied_once_and_basis_fallback():
    official=statistical(100000,100000,.0001,300)
    shifted=statistical(100000,100000-.005,.0001,300)
    assert official['threshold_adjusted']==pytest.approx(100000-.005)
    assert official['probability']>.5
    assert shifted['probability']>official['probability']
    assert official['basis_sigma']==BASIS_SIGMA_USD
    assert rolling_basis_sigma(None,None)==BASIS_SIGMA_USD
    assert rolling_basis_sigma([1,2,3],[1,2,3])==BASIS_SIGMA_USD
    proxy=np.arange(40,dtype=float)+100
    assert rolling_basis_sigma(proxy,proxy-5)==pytest.approx(0)
    width=rolling_basis_sigma(proxy,proxy-np.linspace(0,10,40))
    assert width>0
    # Coinbase-only noise is not a substitute for paired index samples.
    assert rolling_basis_sigma(proxy,None)==BASIS_SIGMA_USD

def test_average_variance_and_missing_reference():
    s=statistical(100,100,.01,100,basis_sigma=0)
    assert s['sd_usd']==pytest.approx(np.sqrt(60))
    with pytest.raises(ValueError,match='Final minute'):statistical(100,100,.01,59)
    with pytest.raises(ValueError):statistical(float('nan'),100,.01,100)

def test_no_future_candles():
    d=candles();f=build_features(d,AT,100000,100000,300)
    future=d.tail(1).copy();future['timestamp']=AT+timedelta(minutes=1);future['close']=9999999
    assert build_features(pd.concat([d,future]),AT,100000,100000,300)==f
    assert 0<=f['rsi']<=100
    with pytest.raises(ValueError):build_features(d.tail(10),AT,100000,100000,300)
    with pytest.raises(ValueError):build_features(d,AT+timedelta(minutes=5),100000,100000,300)
    with pytest.raises(ValueError):build_features(d.drop(index=30),AT,100000,100000,300)

def test_fee_ev():
    assert fee(100,.5)==1.75
    assert fee(1,.5)==.02
    e=economics(.7,.6,1)
    assert e['ev']==pytest.approx(.08)
    assert e['edge']==pytest.approx(e['ev'])
    assert e['break_even']==pytest.approx(.62)

def test_risk_defaults_match_sidebar():
    r=Risk()
    assert (r.stake,r.fraction,r.min_ev,r.max_spread,r.min_depth,r.max_trades,r.cutoff)==(10,.02,.03,.10,10,1,90)

def test_paper_accounting_idempotent(tmp_path):
    s=Store(str(tmp_path/'test.sqlite'));m=market();book={'yes':[(.4,100)],'no':[(.58,100)]}
    assert entry(s,m,.8,book,AT,600,Risk(),1) is None
    fill=entry(s,m,.8,book,AT,600,Risk(),1,True,True);assert fill
    cost=fill[-1]['cost'];q=fill[2]
    assert s.frame('select cash from account').cash[0]==pytest.approx(1000-cost)
    assert entry(s,m,.8,book,AT,600,Risk(),1,True,True) is None
    s.settle(m.ticker,1,AT.isoformat());s.settle(m.ticker,1,AT.isoformat())
    assert s.frame('select cash from account').cash[0]==pytest.approx(1000-cost+q)

def test_pending_ignores_open_markets(tmp_path):
    s=Store(str(tmp_path/'pending.sqlite'))
    closed=market(ticker='CLOSED',open_time=AT-timedelta(minutes=20),close_time=AT-timedelta(minutes=5))
    edge=market(ticker='EDGE',open_time=AT-timedelta(minutes=15),close_time=AT)
    s.market(market());s.market(closed);s.market(edge)
    assert s.pending(AT-timedelta(minutes=6))==[]
    assert s.pending(AT)==['CLOSED','EDGE']
    s.settle('CLOSED',0,AT.isoformat())
    assert s.pending(AT)==['EDGE']
    t={'trade_id':'a','timestamp':AT,'price':1}
    s.observations([t,{'trade_id':'b','timestamp':AT,'price':2}])
    s.observation(t)
    assert len(s.frame('select * from observations'))==2

def test_book_and_fee_guards():
    assert book_needed(None,AT) is False
    assert book_needed(market(),AT) is True
    late=market(open_time=AT-timedelta(minutes=14,seconds=30),close_time=AT+timedelta(seconds=30))
    assert book_needed(late,AT) is False
    assert book_needed(market(rules_primary='price rises'),AT) is False
    assert book_needed(market(floor_strike=None),AT) is False
    assert fee_multiplier(None) is None
    assert fee_multiplier({'fee_type':'quadratic','fee_multiplier':1})==1
    assert fee_multiplier({'fee_type':'quadratic_with_maker_fees','fee_multiplier':1})==1
    assert fee_multiplier({'fee_type':'flat','fee_multiplier':1}) is None
    assert fee_multiplier({'fee_type':'quadratic','fee_multiplier':0}) is None

def test_depth_cutoff_and_cash_cap(tmp_path):
    s=Store(str(tmp_path/'test.sqlite'));m=market()
    assert entry(s,m,.8,{'no':[(.58,1)]},AT,600,Risk(),1,True,True) is None
    assert entry(s,m,.8,{'no':[(.58,100)]},AT,60,Risk(),1,True,True) is None
    fill=entry(s,m,.8,{'no':[(.58,100)]},AT,600,Risk(stake=500,fraction=.01),1,True,True)
    assert fill[-1]['cost']<=10

def dataset(n=120):
    rows=[]
    for i in range(n):
        close=AT+timedelta(minutes=15*i)
        for seconds in [120,240]:rows.append({'ticker':str(i),'timestamp':close-timedelta(seconds=seconds),'close_time':close,'resolved_at':close+timedelta(minutes=1),'label':i%2,'remaining':seconds})
    return pd.DataFrame(rows)

def test_chronological_partitions_no_contract_leakage():
    d=validate(dataset());groups=partitions(d)
    for a,b in zip(groups,groups[1:]):
        assert set(a.ticker).isdisjoint(b.ticker)
        assert a.resolved_at.max()<b.timestamp.min()
    bad=dataset();bad.loc[0,'timestamp']=bad.loc[0,'close_time']
    with pytest.raises(ValueError):validate(bad)

def test_backtest_requires_historical_quotes():
    with pytest.raises(ValueError,match='Historical'):backtest(dataset())

def test_backtest_no_double_spend_and_resolution():
    d=dataset(3);d['yes_ask']=.4;d['no_ask']=.6;d['yes_depth']=10;d['no_depth']=10;d['fee_multiplier']=1;d['ensemble']=.9;d['quote_timestamp']=d.timestamp
    r=backtest(d)
    assert r['trades']==3
    assert r['ending_cash']==pytest.approx(1000-3*.42+1)
