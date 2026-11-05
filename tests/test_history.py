"""Offline checks for historical alignment. No network and no synthetic settlement claims."""
from datetime import timedelta
import pandas as pd
import pytest
from btc15.domain import utc
from research.chrono import assert_no_leak, expanding_windows
from research.history import attach_prices, judge_market, planned_snapshots
from research.study import run_study
from research.walk_forward import walk_forward
from tests.test_core import AT, candles, dataset

RULES="If the simple average of the sixty seconds of CF Benchmarks' BRTI is at least the opening average"

def raw_market(label='yes', strike=100.0, expiration='999'):
    close=AT+timedelta(minutes=15)
    return {
        'ticker':'KXBTC15M-TEST','open_time':AT.isoformat(),'close_time':close.isoformat(),
        'status':'finalized','result':label,'floor_strike':strike,
        'settlement_ts':(close+timedelta(seconds=2)).isoformat(),
        'rules_primary':RULES,'expiration_value':expiration,
        'yes_bid_dollars':'0.9900','yes_ask_dollars':'0.9900',
    }

def test_official_label_is_not_the_expiration_value_or_the_spot():
    record, reason=judge_market(raw_market(label='no', strike=100, expiration='150'))
    assert reason=='accept'
    assert record['label']==0
    assert 'expiration_value' not in record
    assert 'yes_bid_dollars' not in record
    missing,_=judge_market(raw_market(label=''))
    assert missing is None

def test_missing_candle_is_not_invented():
    record,_=judge_market(raw_market())
    snaps=planned_snapshots(record)
    assert {row['remaining'] for row in snaps}=={600,300,180}
    frame=candles()
    kept, missing=attach_prices(snaps, frame)
    assert missing==len(snaps)
    assert kept==[]
    # A candle that ends exactly at the snapshot is usable. Its close is not a label.
    hit=snaps[0]
    one=pd.DataFrame({'timestamp':[hit['timestamp']],'close':[hit['threshold']+10],'open':[1],'high':[1],'low':[1],'volume':[1]})
    kept, missing=attach_prices([hit], one)
    assert missing==0 and kept[0]['price']==hit['threshold']+10

def test_contract_rows_stay_together_and_labels_predate_the_test(tmp_path):
    frame=dataset(20)
    for col in ('timestamp','close_time','resolved_at'):
        frame[col]=pd.to_datetime(frame[col], utc=True)
    windows=expanding_windows(frame, (0.5, 1.0))
    train,test=assert_no_leak(frame, windows[0]['train'], windows[0]['test'])
    assert set(train.ticker).isdisjoint(test.ticker)
    assert train.resolved_at.max()<test.timestamp.min()
    with pytest.raises(ValueError, match='250'):
        walk_forward(frame, tmp_path)

def test_study_blocks_a_tiny_cache_and_writes_no_model(tmp_path):
    record,_=judge_market(raw_market())
    cache=tmp_path/'cache'
    cache.mkdir()
    pd.DataFrame([record]).to_csv(cache/'accepted_contracts.csv', index=False)
    close=utc(record['close_time'])
    index=pd.date_range(close-pd.Timedelta(minutes=180), close, freq='min', tz='UTC')
    price=100+pd.Series(range(len(index)))*0.01
    pd.DataFrame({'timestamp':index,'open':price,'high':price,'low':price,'close':price,'volume':1}).to_csv(cache/'coinbase_1m.csv', index=False)
    out=tmp_path/'report.json'
    report=run_study(cache, out)
    assert report['artifact_written'] is False
    assert report['executable_simulation']=='refused'
    assert report['readiness']=='blocked_too_few_contracts'
    assert report['contracts']==1
    assert not (tmp_path/'model.joblib').exists()

def test_live_paper_path_is_refused(tmp_path):
    with pytest.raises(ValueError, match='paper'):
        run_study(tmp_path/'kalshi-paper-taker', tmp_path/'out.json')
