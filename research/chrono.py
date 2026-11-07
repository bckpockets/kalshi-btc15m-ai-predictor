"""Chronological contract splits. Every row of a ticker stays in one side."""
import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss

def contract_order(d):
    table=d.groupby('ticker').agg(start=('timestamp','min'),resolved=('resolved_at','max'),label=('label','first'))
    if (d.groupby('ticker').label.nunique()>1).any():
        raise ValueError('Conflicting contract labels')
    return table.sort_values(['start','resolved','ticker'])

def purge_train(train_ids, test_ids, order):
    """Drop training contracts whose label was not knowable before the test window."""
    if not test_ids:
        return list(train_ids)
    opening=order.loc[list(test_ids),'start'].min()
    kept=[ticker for ticker in train_ids if order.loc[ticker,'resolved']<opening]
    return kept

def slice_frame(d, ids):
    return d[d.ticker.isin(ids)].copy()

def expanding_windows(d, fractions):
    """Non-overlapping test blocks on a chronological contract list.

    `fractions` are the cuts, including the final holdout start. Example
    (0.5, 0.65, 0.8, 1.0) yields tests [50,65), [65,80), [80,100).
    Training ids are strictly earlier and purged by resolution time.
    """
    order=contract_order(d)
    ids=order.index.tolist(); n=len(ids)
    cuts=[int(n*f) for f in fractions]
    if cuts[0]<1 or cuts[-1]!=n or any(b<=a for a,b in zip(cuts,cuts[1:])):
        raise ValueError('Walk-forward cuts are empty or unordered')
    windows=[]
    for start,end in zip(cuts,cuts[1:]):
        test_ids=ids[start:end]
        train_ids=purge_train(ids[:start], test_ids, order)
        if not train_ids or not test_ids:
            raise ValueError('Empty walk-forward window after purge')
        if set(train_ids)&set(test_ids):
            raise ValueError('Contract appears on both sides of a split')
        windows.append({'train':train_ids,'test':test_ids})
    return windows

def assert_no_leak(d, train_ids, test_ids):
    order=contract_order(d)
    train,test=slice_frame(d,train_ids),slice_frame(d,test_ids)
    if set(train.ticker)&set(test.ticker):
        raise ValueError('Contract rows crossed the split')
    if not (train.resolved_at.max()<test.timestamp.min()):
        raise ValueError('A training label resolves after the test window opens')
    if not (order.loc[list(test_ids),'start'].min()>order.loc[list(train_ids),'resolved'].max()):
        raise ValueError('Purge did not enforce label time')
    return train,test

def paired_brier_delta(d, left, right, draws=400, seed=1):
    """Bootstrap mean of Brier(left) - Brier(right) by resampling contracts.

    Negative values mean `left` has the lower Brier on that draw.
    """
    rng=np.random.default_rng(seed)
    tickers=d.ticker.drop_duplicates().to_numpy()
    groups={ticker:frame for ticker,frame in d.groupby('ticker')}
    deltas=[]
    for _ in range(draws):
        pick=rng.choice(tickers, size=len(tickers), replace=True)
        sample=pd.concat([groups[ticker] for ticker in pick], ignore_index=True)
        y=sample.label.to_numpy(dtype=int)
        a=np.clip(sample[left].to_numpy(dtype=float),1e-6,1-1e-6)
        b=np.clip(sample[right].to_numpy(dtype=float),1e-6,1-1e-6)
        deltas.append(float(brier_score_loss(y,a)-brier_score_loss(y,b)))
    qs=np.quantile(deltas,[.05,.5,.95])
    return {'left':left,'right':right,'p05':float(qs[0]),'p50':float(qs[1]),'p95':float(qs[2]),'draws':draws,'covers_zero':bool(qs[0]<=0<=qs[2])}

def contract_bootstrap(d, columns, draws=400, seed=0):
    """Resample contracts, not rows. Returns 5/50/95 percent Brier quantiles."""
    rng=np.random.default_rng(seed)
    tickers=d.ticker.drop_duplicates().to_numpy()
    groups={ticker:frame for ticker,frame in d.groupby('ticker')}
    out={}
    for col in columns:
        scores=[]
        for _ in range(draws):
            pick=rng.choice(tickers, size=len(tickers), replace=True)
            sample=pd.concat([groups[ticker] for ticker in pick], ignore_index=True)
            p=np.clip(sample[col].to_numpy(dtype=float),1e-6,1-1e-6)
            scores.append(float(brier_score_loss(sample.label.to_numpy(dtype=int), p)))
        qs=np.quantile(scores,[.05,.5,.95])
        out[col]={'p05':float(qs[0]),'p50':float(qs[1]),'p95':float(qs[2]),'draws':draws,'unit':'contract'}
    return out
