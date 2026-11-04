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

def train(d,out):
    d=validate(d)
    if d[FEATURES+['baseline']].isna().any().any():raise ValueError('Missing features')
    fit,cal,blend,test=partitions(d)
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
    test=test.copy();test['ml']=calibrator.predict_proba(model.predict_proba(test[FEATURES].to_numpy())[:,1,None])[:,1];test['ensemble']=weight*test.ml+(1-weight)*test.baseline
    columns=['baseline','ml','ensemble']+(['market_probability'] if 'market_probability' in test else [])
    result=report(test,columns);result['partition_contracts']=[x.ticker.nunique() for x in (fit,cal,blend,test)];result['ml_weight']=float(weight)
    artifact={'schema':FEATURES,'model':model,'calibrator':calibrator,'weight':float(weight),'version':'xgb-'+str(test.timestamp.max()),'test_report':result,'trained_through':str(blend.resolved_at.max())}
    Path(out).parent.mkdir(parents=True,exist_ok=True);joblib.dump(artifact,out)
    Path(out+'.report.json').write_text(json.dumps(result,indent=2,default=str));test.to_csv(out+'.test.csv',index=False)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('csv');p.add_argument('--output',default='artifacts/model.joblib');a=p.parse_args();print(json.dumps(train(pd.read_csv(a.csv),a.output),indent=2,default=str))
