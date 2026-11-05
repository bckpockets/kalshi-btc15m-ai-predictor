"""Synthetic unit-test fixtures only; never market history or claimed accuracy."""
import numpy as np
import pandas as pd
import pytest
from btc15.features import FEATURES
from btc15.model import Predictor
from research.train import train
from research.build_dataset import build
from tests.test_core import dataset,candles,AT

def test_training_artifact_and_inference(tmp_path):
    d=dataset(120);rng=np.random.default_rng(4)
    for f in FEATURES:
        if f not in d:d[f]=rng.normal(size=len(d))
    d['sigma']=.0001;d['baseline']=.5;d['market_probability']=.5
    path=str(tmp_path/'model.joblib');report=train(d,path)
    assert report['contracts']>0
    predictor=Predictor(path);p,ml,version=predictor.predict(d.iloc[-1].to_dict(),.5)
    assert 0<=p<=1 and 0<=ml<=1 and version.startswith('xgb-')
    assert report['partition_contracts']==[60,18,18,24]

def test_historical_builder_rejects_future_threshold():
    import pytest
    c=candles();s=pd.DataFrame([{'ticker':'A','timestamp':AT,'close_time':AT+pd.Timedelta(minutes=5),'threshold':100000,'price':100001,'threshold_published_at':AT+pd.Timedelta(seconds=1)}])
    o=pd.DataFrame([{'ticker':'A','label':1,'resolved_at':AT+pd.Timedelta(minutes=6)}])
    with pytest.raises(ValueError,match='threshold leakage'):build(c,s,o)
    s['threshold_published_at']=AT-pd.Timedelta(minutes=5)
    result=build(c,s,o);assert len(result)==1;assert 0<=result.baseline.iloc[0]<=1
    from btc15.model import statistical
    # Builder passes the official threshold; half-cent is applied once inside statistical().
    expect=statistical(100001,100000,float(result.sigma.iloc[0]),300)['probability']
    assert result.baseline.iloc[0]==pytest.approx(expect)
