import argparse
import json
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss
from xgboost import XGBClassifier
from btc15.features import FEATURES
from research.dataset import validate,partitions
from research.evaluate import report

def contract_base_rate(frame):
    """One label per contract. Snapshot repeats must not change the rate."""
    return float(frame.groupby('ticker').label.first().mean())

def fit_eval(fit,cal,blend,test):
    """Fit only on fit/cal/blend. `test` is scored and never used to choose weights."""
    for frame in (fit,cal,blend,test):
        if set(frame.ticker) & set(test.ticker) and frame is not test:
            raise ValueError('Test contracts leaked into a fitting partition')
    if fit[FEATURES+['baseline']].isna().any().any():raise ValueError('Missing features')
    for x in (fit,cal):
        if x.label.nunique()!=2:raise ValueError('Both outcomes required in fit and calibration partitions')
    model=XGBClassifier(n_estimators=120,max_depth=3,learning_rate=.04,subsample=.8,colsample_bytree=.8,random_state=42,n_jobs=2,eval_metric='logloss')
    # Each contract contributes equal total weight despite repeated forecasts.
    weights=1/fit.groupby('ticker').ticker.transform('size')
    model.fit(fit[FEATURES].to_numpy(),fit.label,sample_weight=weights*len(weights)/weights.sum())
    calibrator=LogisticRegression().fit(model.predict_proba(cal[FEATURES].to_numpy())[:,1,None],cal.label,sample_weight=1/cal.groupby('ticker').ticker.transform('size'))
    b=calibrator.predict_proba(model.predict_proba(blend[FEATURES].to_numpy())[:,1,None])[:,1]
    weights=1/blend.groupby('ticker').ticker.transform('size')
    weight=min(np.linspace(0,1,21),key=lambda w:brier_score_loss(blend.label,w*b+(1-w)*blend.baseline,sample_weight=weights))
    scored=test.copy()
    scored['ml']=calibrator.predict_proba(model.predict_proba(scored[FEATURES].to_numpy())[:,1,None])[:,1]
    scored['ensemble']=weight*scored.ml+(1-weight)*scored.baseline
    scored['constant_half']=0.5
    scored['train_base_rate']=contract_base_rate(fit)
    columns=['baseline','constant_half','train_base_rate','ml','ensemble']+(['market_probability'] if 'market_probability' in scored else [])
    result=report(scored,columns)
    result['partition_contracts']=[x.ticker.nunique() for x in (fit,cal,blend,scored)]
    result['ml_weight']=float(weight)
    result['train_base_rate']=scored['train_base_rate'].iloc[0]
    artifact={'schema':FEATURES,'model':model,'calibrator':calibrator,'weight':float(weight),'version':'xgb-'+str(scored.timestamp.max()),'test_report':result,'trained_through':str(blend.resolved_at.max()),'deployment':'manual only; this file is not loaded by the collector'}
    return result,artifact,scored

def train(d,out):
    d=validate(d)
    if d[FEATURES+['baseline']].isna().any().any():raise ValueError('Missing features')
    fit,cal,blend,test=partitions(d)
    result,artifact,scored=fit_eval(fit,cal,blend,test)
    Path(out).parent.mkdir(parents=True,exist_ok=True);joblib.dump(artifact,out)
    Path(out+'.report.json').write_text(json.dumps(result,indent=2,default=str));scored.to_csv(out+'.test.csv',index=False)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('csv');p.add_argument('--output',default='artifacts/model.joblib');a=p.parse_args();print(json.dumps(train(pd.read_csv(a.csv),a.output),indent=2,default=str))
