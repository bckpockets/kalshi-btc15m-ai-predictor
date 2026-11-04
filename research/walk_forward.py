"""Expanding-window evaluations. The final global holdout is never used in folds."""
import argparse,json
from pathlib import Path
import pandas as pd
from research.dataset import validate
from research.train import train

def walk_forward(d,output):
    d=validate(d);ids=d.groupby('ticker').timestamp.min().sort_values().index.tolist()
    if len(ids)<250:raise ValueError('Walk-forward requires at least 250 contracts')
    reports=[]
    # Growing prefixes yield sequential held-out windows. Keep global final 20% untouched.
    for i,fraction in enumerate([.5,.65,.8]):
        subset=d[d.ticker.isin(ids[:int(len(ids)*fraction)])]
        reports.append(train(subset,str(Path(output)/f'fold-{i}.joblib')))
    final=train(d,str(Path(output)/'model.joblib'))
    result={'walk_forward':reports,'final_unseen_test':final}
    Path(output,'walk-forward.json').write_text(json.dumps(result,indent=2,default=str))
    return result
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('csv');p.add_argument('--output',default='artifacts');a=p.parse_args();walk_forward(pd.read_csv(a.csv),a.output)
