"""Score a collected cache. This does not write a model and does not simulate fills."""
import hashlib
import json
from pathlib import Path
import pandas as pd
from btc15.features import build_features
from research.build_dataset import build
from research.chrono import expanding_windows, assert_no_leak, contract_bootstrap, paired_brier_delta
from research.evaluate import metrics
from research.history import assert_safe_cache, attach_prices, planned_snapshots
from research.train import contract_base_rate, fit_eval

def _buildable(candles, snaps):
    kept=0
    failed=[]
    ok=[]
    for snap in snaps:
        try:
            build_features(candles, snap['timestamp'], snap['price'], snap['threshold'], snap['remaining'])
        except ValueError:
            failed.append(snap['ticker'])
            continue
        ok.append(snap); kept+=1
    return ok, failed

def modeling_frame(cache):
    cache=Path(cache)
    assert_safe_cache(cache)
    contracts=pd.read_csv(cache/'accepted_contracts.csv')
    candles=pd.read_csv(cache/'coinbase_1m.csv')
    candles['timestamp']=pd.to_datetime(candles.timestamp, utc=True)
    snaps=[]
    for record in contracts.to_dict('records'):
        snaps.extend(planned_snapshots(record))
    priced, missing_candle=attach_prices(snaps, candles)
    usable, failed=_buildable(candles, priced)
    if not usable:
        raise ValueError('No snapshots had a complete Coinbase window')
    outcomes=contracts.loc[contracts.ticker.isin({row['ticker'] for row in usable}), ['ticker','label','resolved_at']]
    frame=build(candles, pd.DataFrame(usable), outcomes)
    banned={'expiration_value','settlement_value','yes_bid_dollars','result'}
    present=sorted(banned & set(frame.columns))
    if present:
        raise ValueError('Modeling frame contains settlement or quote fields: '+','.join(present))
    return frame, {'planned':len(snaps),'missing_candle':missing_candle,'feature_rejected':len(failed),'rows':int(len(frame)),'contracts':int(frame.ticker.nunique())}

def _baseline_block(train, test):
    scored=test.copy()
    scored['constant_half']=0.5
    scored['train_base_rate']=contract_base_rate(train)
    columns=['baseline','constant_half','train_base_rate']
    return scored, {name:{'snapshots':metrics(scored.label, scored[name]),'last_per_contract':metrics(scored.sort_values('timestamp').groupby('ticker').tail(1).label, scored.sort_values('timestamp').groupby('ticker').tail(1)[name])} for name in columns}

def run_study(cache, output):
    cache, output=Path(cache), Path(output)
    assert_safe_cache(cache)
    assert_safe_cache(output)
    frame, coverage=modeling_frame(cache)
    manifest_path=cache/'manifest.json'
    if manifest_path.exists():
        coverage['collector']=json.loads(manifest_path.read_text())
    n=int(frame.ticker.nunique())
    report={
        'kind':'predictive_backtest_not_execution',
        'artifact_written':False,
        'model_path':None,
        'paper_configuration_changed':False,
        'executable_simulation':'refused',
        'executable_reason':'No historical order-book depth, quote timestamp, or fee multiplier was collected. Candle closes are not fills.',
        'contracts':n,
        'rows':int(len(frame)),
        'label_counts':{str(k):int(v) for k,v in frame.groupby('ticker').label.first().value_counts().items()},
        'time_start':str(frame.timestamp.min()),
        'time_end':str(frame.timestamp.max()),
        'coverage':coverage,
        'imbalance_and_pressure':'missing; builder uses neutral zero. This is not a measured book.',
        'threshold_assumption':'floor_strike from the settled market, treated as knowable at open_time because the rules define it as the pre-open average. No separate publication tape was available.',
        'readiness':'not_ready',
        'readiness_reasons':[
            'Coinbase prices are a proxy, not CF Benchmarks BRTI, and are not the settlement index.',
            'Official labels are Kalshi yes/no only. expiration_value was not used as a feature or a label.',
            'No executable quotes were available, so this is not a trading simulation.',
            'The sample is one short recent window. A lower Brier on that window is not an accuracy claim.',
            'No model file is written. The live paper process and MODEL_PATH are unchanged.',
        ],
    }
    digest=hashlib.sha256(frame[['ticker','timestamp','label','baseline']].to_csv(index=False).encode()).hexdigest()
    report['frame_fingerprint']=digest
    if n<40:
        report['readiness']='blocked_too_few_contracts'
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, default=str))
        return report
    fractions=(0.8, 1.0) if n<100 else (0.6, 0.8, 1.0)
    windows=expanding_windows(frame, fractions)
    folds=[]
    for i, window in enumerate(windows):
        train,test=assert_no_leak(frame, window['train'], window['test'])
        scored, block=_baseline_block(train, test)
        fold={'test_contracts':int(test.ticker.nunique()),'train_contracts':int(train.ticker.nunique()),'baselines':block,'ml':None}
        fold['bootstrap_brier']=contract_bootstrap(scored, ['baseline','constant_half','train_base_rate'], draws=400, seed=i)
        fold['baseline_minus_half']=paired_brier_delta(scored, 'baseline', 'constant_half', draws=400, seed=10+i)
        if n>=100 and i==len(windows)-1:
            ids=train.groupby('ticker').timestamp.min().sort_values().index.tolist()
            a,b=int(len(ids)*0.70),int(len(ids)*0.85)
            try:
                result,_,_=fit_eval(train[train.ticker.isin(ids[:a])], train[train.ticker.isin(ids[a:b])], train[train.ticker.isin(ids[b:])], test)
                fold['ml']={
                    'weight':result['ml_weight'],
                    'holdout_contracts':result['partition_contracts'][-1],
                    'models':{name:result['models'][name]['contracts_last_forecast'] for name in result['models']},
                }
                fold['ml_note']='Held-out scores only. Weight was chosen on the blend contracts, not on this test. Artifact was not saved.'
            except ValueError as exc:
                fold['ml_error']=str(exc)
        folds.append(fold)
    report['folds']=folds
    if n<100:
        report['readiness']='blocked_below_trainer_minimum'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(_without_reliability(report), indent=2, default=str))
    return report

def _without_reliability(value):
    if isinstance(value, dict):
        return {key:_without_reliability(item) for key,item in value.items() if key!='reliability'}
    if isinstance(value, list):
        return [_without_reliability(item) for item in value]
    return value

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser()
    p.add_argument('cache')
    p.add_argument('--output', default='docs/historical_report.json')
    args=p.parse_args()
    print(json.dumps({k:run_study(args.cache, args.output)[k] for k in ('readiness','contracts','rows','artifact_written','executable_simulation')}, indent=2))
