import numpy as np
import pandas as pd

FEATURES = ['return_1','return_5','return_15','distance','distance_vol','remaining','momentum','acceleration','rsi','macd','ma_deviation','bollinger','sigma','volume_z','imbalance','pressure','hour_sin','hour_cos']

def build_features(candles, at, price, threshold, remaining, imbalance=0., pressure=0.):
    """Candles are indexed by END time; incomplete/future candles never enter features."""
    d = candles.loc[candles.timestamp <= at].sort_values('timestamp').drop_duplicates('timestamp').tail(120)
    if len(d) < 30 or (at-d.timestamp.iloc[-1]).total_seconds() > 150:
        raise ValueError('Need at least 30 recent completed one-minute candles')
    gaps = d.timestamp.diff().dt.total_seconds().dropna()
    if (gaps > 90).any():
        raise ValueError('Gaps in volatility window')
    p = d.close.astype(float); r = np.log(p).diff().dropna()
    sigma = max(float(r.std(ddof=1)/np.sqrt(60)), 1e-7)
    delta=p.diff(); up=delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean().iloc[-1]; down=(-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean().iloc[-1]
    rsi = 100*up/(up+down) if up+down else 50
    mean=float(p.tail(20).mean()); std=float(p.tail(20).std())
    v=d.volume.astype(float); vs=float(v.tail(30).std())
    f={'return_1':float(np.log(p.iloc[-1]/p.iloc[-2])), 'return_5':float(np.log(p.iloc[-1]/p.iloc[-6])), 'return_15':float(np.log(p.iloc[-1]/p.iloc[-16])), 'distance':price-threshold, 'distance_vol':(price-threshold)/(price*sigma*np.sqrt(max(remaining,1))), 'remaining':remaining, 'momentum':float(r.tail(5).mean()), 'acceleration':float(r.iloc[-1]-r.iloc[-2]), 'rsi':float(rsi), 'macd':float(p.ewm(span=12,adjust=False).mean().iloc[-1]-p.ewm(span=26,adjust=False).mean().iloc[-1]), 'ma_deviation':price/mean-1, 'bollinger':(price-mean)/(2*std) if std else 0, 'sigma':sigma,'volume_z':(float(v.iloc[-1])-float(v.tail(30).mean()))/vs if vs else 0, 'imbalance':imbalance,'pressure':pressure,'hour_sin':float(np.sin(at.hour*2*np.pi/24)), 'hour_cos':float(np.cos(at.hour*2*np.pi/24))}
    if not np.isfinite(list(f.values())).all(): raise ValueError('Nonfinite features')
    return f
