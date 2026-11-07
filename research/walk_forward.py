"""Expanding-window evaluations. Test contracts are never fit, calibrated, or used to pick the blend."""
import argparse,json
from pathlib import Path
import pandas as pd
from research.chrono import expanding_windows,slice_frame,assert_no_leak
from research.dataset import validate
from research.train import fit_eval

def walk_forward(d, output, write_artifact=False):
    d=validate(d)
    if d.ticker.nunique()<250:
        raise ValueError('Walk-forward requires at least 250 contracts')
    windows=expanding_windows(d, (0.50,0.65,0.80,1.0))
    reports=[]
    for i,window in enumerate(windows):
        train,test=assert_no_leak(d, window['train'], window['test'])
        # Internal fit/calibration/blend stay inside the training contracts.
        ids=train.groupby('ticker').timestamp.min().sort_values().index.tolist()
        a,b=int(len(ids)*0.70),int(len(ids)*0.85)
        parts=[ids[:a],ids[a:b],ids[b:]]
        if any(len(p)<1 for p in parts):
            raise ValueError('Training window is too small to separate calibration')
        fit,cal,blend=slice_frame(train,parts[0]),slice_frame(train,parts[1]),slice_frame(train,parts[2])
        result,_,_=fit_eval(fit,cal,blend,test)
        result['fold']=i
        result['train_contracts']=int(train.ticker.nunique())
        result['test_contracts']=int(test.ticker.nunique())
        reports.append(result)
    result={'protocol':'expanding chronological contracts; final window is the last 20 percent','walk_forward':reports,'artifact_written':False}
    out=Path(output); out.mkdir(parents=True,exist_ok=True)
    if write_artifact:
        raise ValueError('Refusing to write a model file from walk-forward. Pass an explicit training command after reviewing the JSON report.')
    (out/'walk-forward.json').write_text(json.dumps(result,indent=2,default=str))
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('csv')
    p.add_argument('--output',default='artifacts')
    p.add_argument('--write-artifact',action='store_true')
    a=p.parse_args()
    walk_forward(pd.read_csv(a.csv), a.output, write_artifact=a.write_artifact)
