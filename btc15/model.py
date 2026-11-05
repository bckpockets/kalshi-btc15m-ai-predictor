from math import erf, sqrt
from pathlib import Path
import joblib
import numpy as np
from btc15.features import FEATURES

# Assumed dollar standard deviation of (Coinbase proxy − CF Benchmarks BRTI).
# Not fit from Coinbase alone and not a measure of model skill. Env: BASIS_SIGMA_USD.
BASIS_SIGMA_USD = 25.
# Published strikes are rounded to two decimals. The continuous approximation
# treats the YES boundary as half a cent below the official threshold.
HALF_CENT_USD = 0.005

def rolling_basis_sigma(proxy_prices, index_prices, fallback=BASIS_SIGMA_USD, min_samples=30):
    """Sample stdev of proxy minus settlement index, in dollars.

    Needs paired observations of the exchange proxy and the CF Benchmarks index.
    Coinbase-only prices are not a basis sample: without those index prints this
    returns `fallback` (default $25) and does not invent a width. `statistical()`
    is still the probability model; pass the result as `basis_sigma` only when
    the pairs are real index samples.
    """
    if proxy_prices is None or index_prices is None:
        return float(fallback)
    proxy=np.asarray(proxy_prices, dtype=float)
    index=np.asarray(index_prices, dtype=float)
    if proxy.shape!=index.shape or proxy.size<min_samples:
        return float(fallback)
    diff=proxy-index
    if not np.isfinite(diff).all():
        return float(fallback)
    sigma=float(np.std(diff, ddof=1))
    if not np.isfinite(sigma) or sigma<0:
        return float(fallback)
    return sigma

def statistical(price, threshold, sigma, remaining, basis_sigma=BASIS_SIGMA_USD):
    """Arithmetic Brownian local approximation to the final 60-second mean.

    Before averaging starts, Var(mean W)=T-2w/3. In the last minute
    observed CF Benchmarks BRTI samples are required; without them suspend
    prediction. Do not fill that minute with Coinbase prints. Coinbase is a
    proxy for BRTI, not the settlement index.

    `threshold` is the official opening strike. This function subtracts
    HALF_CENT_USD itself so callers cannot forget the two-decimal boundary.

    Basis uncertainty is a dollar-scale Gaussian assumption, not calibration.
    The default is BASIS_SIGMA_USD ($25).
    """
    if remaining < 60: raise ValueError('Final minute requires observed CF Benchmarks samples; proxy forecast suspended')
    if not all(np.isfinite(x) for x in [price,threshold,sigma,remaining,basis_sigma]) or min(price,threshold,sigma)<=0 or basis_sigma<0:
        raise ValueError('Invalid probability inputs')
    adjusted=threshold-HALF_CENT_USD
    sd=sqrt((price*sigma)**2*(remaining-40)+basis_sigma**2)
    p=.5*(1+erf((price-adjusted)/(sd*sqrt(2))))
    return {'probability':float(np.clip(p,.001,.999)), 'sd_usd':sd, 'lower_price':price-1.96*sd, 'upper_price':price+1.96*sd, 'threshold':float(threshold), 'threshold_adjusted':float(adjusted), 'basis_sigma':float(basis_sigma)}

class Predictor:
    """Trusted artifact, or the statistical baseline when none is loaded.

    No model file is bundled. In-repo fixtures have no official outcome labels,
    and synthetic tests are not market history. joblib/pickle can execute code:
    load only an artifact you trained whose schema is FEATURES and that carries
    a test_report. Otherwise predict() returns the statistical probability.
    See docs/IMPROVEMENT_PLAN.md for harvest and training steps.
    """
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
