import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score,brier_score_loss,log_loss,confusion_matrix,precision_score,recall_score

def metrics(y,p):
    y=np.asarray(y,dtype=int);p=np.clip(np.asarray(p,dtype=float),1e-6,1-1e-6)
    if not len(y):return {}
    bins=np.minimum((p*10).astype(int),9);reliability=[];ece=0
    for b in range(10):
        mask=bins==b
        if mask.any():
            observed=float(y[mask].mean());forecast=float(p[mask].mean());count=int(mask.sum())
            reliability.append({'bucket':b,'forecast':forecast,'observed':observed,'count':count});ece+=count/len(y)*abs(observed-forecast)
    return {'n':len(y),'accuracy':accuracy_score(y,p>=.5),'brier':brier_score_loss(y,p),'log_loss':log_loss(y,p,labels=[0,1]),'precision':precision_score(y,p>=.5,zero_division=0),'recall':recall_score(y,p>=.5,zero_division=0),'confusion_matrix':confusion_matrix(y,p>=.5,labels=[0,1]).tolist(),'ece':ece,'reliability':reliability}

def report(df, columns):
    out={'contracts':int(df.ticker.nunique()),'start':str(df.timestamp.min()),'end':str(df.timestamp.max()),'models':{}}
    for col in columns:
        d=df.dropna(subset=[col]);latest=d.sort_values('timestamp').groupby('ticker').tail(1)
        out['models'][col]={'predictions':metrics(d.label,d[col]),'contracts_last_forecast':metrics(latest.label,latest[col]),'by_remaining':{},'by_volatility':{}}
        for name,g in d.groupby(pd.cut(d.remaining,[0,120,300,600,901]),observed=True):out['models'][col]['by_remaining'][str(name)]=metrics(g.label,g[col])
        if 'sigma' in d:
            # Absolute predeclared bins, not quantiles fit on test data.
            for name,g in d.groupby(pd.cut(d.sigma,[0,.00005,.00015,np.inf]),observed=True):out['models'][col]['by_volatility'][str(name)]=metrics(g.label,g[col])
    return out
