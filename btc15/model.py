from math import erf, sqrt
from pathlib import Path
import joblib
import numpy as np
from btc15.features import FEATURES

def statistical(price, threshold, sigma, remaining, basis_sigma=25.):
    """Arithmetic Brownian local approximation to the final 60-second mean.

    Before averaging starts, Var(mean W)=T-2w/3. In the last minute
    observed reference samples are required; without them suspend prediction.
    Basis uncertainty is a dollar-scale Gaussian assumption, not calibration.
    """
    if remaining < 60: raise ValueError('Final minute requires observed CF Benchmarks samples; proxy forecast suspended')
    if not all(np.isfinite(x) for x in [price,threshold,sigma,remaining,basis_sigma]) or min(price,threshold,sigma)<=0 or basis_sigma<0:
        raise ValueError('Invalid probability inputs')
    sd=sqrt((price*sigma)**2*(remaining-40)+basis_sigma**2)
    p=.5*(1+erf((price-threshold)/(sd*sqrt(2))))
    return {'probability':float(np.clip(p,.001,.999)), 'sd_usd':sd, 'lower_price':price-1.96*sd, 'upper_price':price+1.96*sd}

class Predictor:
    def __init__(self, path):
        self.artifact = None
        if Path(path).exists():
            a=joblib.load(path) # Load ONLY artifacts you trained/trust: pickle can execute code.
            if a.get('schema') != FEATURES or not a.get('test_report'):
                raise ValueError('Model lacks validated schema/report')
            self.artifact=a
    def predict(self, f, base):
        if not self.artifact: return base, None, 'statistical-proxy-v1'
        a=self.artifact
        raw=float(a['model'].predict_proba(np.array([[f[k] for k in FEATURES]]))[0,1])
        ml=float(a['calibrator'].predict_proba([[raw]])[0,1])
        return float(a['weight']*ml+(1-a['weight'])*base), ml, a['version']
