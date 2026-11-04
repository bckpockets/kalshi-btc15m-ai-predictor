"""Import strict as-of snapshots. Official labels are joined only for evaluation."""
import json
import pandas as pd
from btc15.features import FEATURES,build_features
from btc15.model import statistical

def validate(df):
    d=df.copy()
    for k in ['timestamp','close_time','resolved_at']:d[k]=pd.to_datetime(d[k],utc=True)
    if d.duplicated(['ticker','timestamp']).any():raise ValueError('Duplicate prediction snapshot')
    if not ((d.timestamp<d.close_time)&(d.resolved_at>=d.close_time)).all():raise ValueError('Invalid as-of chronology')
    if not d.label.isin([0,1]).all():raise ValueError('Labels must be official yes/no outcomes')
    if (d.groupby('ticker').label.nunique()>1).any():raise ValueError('Conflicting contract labels')
    if (d.groupby('ticker').close_time.nunique()>1).any():raise ValueError('Conflicting close times')
    return d.sort_values(['close_time','timestamp'])

def partitions(d):
    contracts=d.groupby('ticker').agg(start=('timestamp','min'),end=('resolved_at','max')).sort_values('start')
    if len(contracts)<100:raise ValueError('Need at least 100 distinct resolved contracts; thousands recommended')
    ids=contracts.index.tolist();n=len(ids)
    groups=[ids[:int(n*.5)],ids[int(n*.5):int(n*.65)],ids[int(n*.65):int(n*.8)],ids[int(n*.8):]]
    frames=[d[d.ticker.isin(g)].copy() for g in groups]
    # Purge labels unavailable when the next partition starts.
    for i in range(3):frames[i]=frames[i][frames[i].resolved_at < frames[i+1].timestamp.min()]
    if any(x.empty for x in frames):raise ValueError('Empty partition after temporal purging')
    return frames

def export_database(path,out):
    from btc15.store import Store
    s=Store(path)
    d=s.frame('SELECT p.*,m.result AS label,r.resolved_at,q.observed_at AS quote_timestamp,q.yes_ask,q.no_ask,q.yes_depth,q.no_depth,q.fee_multiplier FROM predictions p JOIN markets m USING(ticker) JOIN resolutions r USING(ticker) LEFT JOIN quotes q ON p.ticker=q.ticker AND p.timestamp=q.timestamp WHERE m.result IS NOT NULL')
    if d.empty:raise ValueError('No official resolved observations available')
    feats=pd.json_normalize(d.features.map(json.loads))
    for k in feats:
        if k not in d:d[k]=feats[k].to_numpy()
    validate(d).to_csv(out,index=False)

if __name__=='__main__':
    import argparse
    a=argparse.ArgumentParser();a.add_argument('database');a.add_argument('output');x=a.parse_args();export_database(x.database,x.output)
