"""Build features from independently acquired candles and timestamped snapshots."""
import argparse
import pandas as pd
from btc15.features import build_features
from btc15.model import statistical
from research.dataset import validate

def build(candles,snapshots,outcomes):
    c=candles.copy();c['timestamp']=pd.to_datetime(c.timestamp,utc=True)
    s=snapshots.copy();s['timestamp']=pd.to_datetime(s.timestamp,utc=True);s['close_time']=pd.to_datetime(s.close_time,utc=True)
    if 'threshold_published_at' not in s:raise ValueError('Threshold publication timestamp required for as-of integrity')
    s['threshold_published_at']=pd.to_datetime(s.threshold_published_at,utc=True)
    if (s.threshold_published_at>s.timestamp).any():raise ValueError('Future threshold leakage')
    rows=[]
    for _,r in s.iterrows():
        remaining=(r.close_time-r.timestamp).total_seconds()
        if remaining<60:continue
        f=build_features(c,r.timestamp,r.price,r.threshold,remaining,r.get('imbalance',0),r.get('pressure',0))
        stats=statistical(r.price,r.threshold-.005,f['sigma'],remaining)
        rows.append({**r.to_dict(),**f,'baseline':stats['probability']})
    if not rows:raise ValueError('No eligible pre-final-minute snapshots')
    outcomes=outcomes[['ticker','label','resolved_at']]
    if outcomes.ticker.duplicated().any():raise ValueError('Duplicate official outcomes')
    return validate(pd.DataFrame(rows).merge(outcomes,on='ticker',validate='many_to_one'))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('candles');p.add_argument('snapshots');p.add_argument('outcomes');p.add_argument('--output',default='dataset.csv');a=p.parse_args();build(pd.read_csv(a.candles),pd.read_csv(a.snapshots),pd.read_csv(a.outcomes)).to_csv(a.output,index=False)
