# Improvement plan

Research terminal only. Nothing here is a claim of forecast skill, a profitable rule, or investment advice. The statistical baseline stays the live fallback. No paid data feed and no order-placement API is in scope.

Paper execution is versioned separately from this forecast note. Live fills use `taker-confirm-v1` and a new database. See [EXECUTION.md](EXECUTION.md). Do not treat an older same-snapshot ledger as that model.

## Accuracy

The contract settles on the final 60-second CF Benchmarks BRTI average versus the pre-open average, not on a Coinbase print. Coinbase is a **proxy** for BRTI. It is not the settlement index, and it is not used to fill the last minute.

- `statistical()` owns the half-cent rounding boundary (`official_threshold − 0.005`). Callers pass the published strike. The engine and `research/build_dataset.py` no longer pre-adjust, so the shift cannot be forgotten or applied twice.
- `BASIS_SIGMA_USD` stays **$25**. That number is an assumed dollar width for proxy-versus-index basis. It is not fit on Coinbase and it is not a calibration result. `rolling_basis_sigma()` can replace it only when someone supplies paired proxy and **index** samples (at least 30 finite pairs). Without those pairs it returns $25. The live collector does not call it, because this process never sees entitled BRTI samples.
- The final minute still raises and the UI still clears the forecast when fewer than 60 seconds remain. That gate is not loosened.
- Probabilities stay clipped to [0.001, 0.999]. Missing threshold, unrecognized rules, stale ticks, and candle gaps still suspend output.

### Model slot

No XGBoost artifact is shipped. `artifacts/model.joblib` is gitignored. `Predictor` loads a file only when `schema` equals `FEATURES` and `test_report` is present; otherwise, and when the file is absent, `predict()` returns the statistical probability (`statistical-proxy-v1`).

In-repo files are not a training set:

- `docs/live-candles.json` is Coinbase candle history, unlabeled.
- `docs/live-markets.json` is one open-market snapshot, not a settled result.
- `docs/live-series.json` is fee and rules metadata.
- Tests build synthetic frames. Those frames prove the trainer runs. They are not market history and must not be written to `artifacts/model.joblib`.

The trainer also refuses fewer than 100 distinct resolved contracts. That minimum is mechanical. It is not evidence the model is reliable.

### How to harvest labels and train

1. Run the app and let it record forecasts plus official Kalshi `settled`/`finalized` results. Do not label from spot or from Coinbase.
2. Export after resolutions exist:

```bash
python -m research.dataset runtime.sqlite dataset.csv
```

3. Fit only on that file (or another file you built with `research/build_dataset.py`, which requires `threshold_published_at` so the strike was knowable at the snapshot). Prefer thousands of contracts across volatility regimes.

```bash
python -m research.train dataset.csv --output artifacts/model.joblib
```

4. Restart the app so `MODEL_PATH` loads once. The blend may put zero weight on the tree. The statistical baseline is always an input to the blend and the fallback if you remove the file.

Do not train on a settled-market dump that lacks an as-of publication time for `floor_strike`. A strike observed only after the fact is leakage, and a model fit on it would be untrustworthy even if the file had a schema and a report.

## Request cost

Public GET only, with the existing serial throttle and bounded retries. No API keys.

| Work | Cadence | Why |
|---|---|---|
| Current Kalshi ticker (`/markets/{ticker}`) | Every collector cycle (default 3s) | Quotes and threshold for the active contract |
| Full `KXBTC15M` open list | About every 60s (`MARKET_DISCOVERY_SECONDS`); about every 15s if nothing is active or the active contract is inside 30s of close | Rollover discovery without listing the series every poll. `Kalshi.markets()` also caches for that TTL unless `force=True` |
| Series metadata | About every 300s; retry about 30s on failure | Fee schedule. A missing payload stays `None` and does not crash the cycle |
| Coinbase candles, top-of-book imbalance, 24h stats | About every 60s (`CANDLE_SECONDS`) | One-minute bars do not need a 30s pull |
| Kalshi order book | Every cycle **only** when rules are recognized, the threshold is published, and at least 60s remain | Paper entries and the edge panel both need that book. The final minute, a missing strike, or unknown rules skip it |
| Kalshi trades | Every `TRADES_EVERY_CYCLES` cycles (default 5), and once when the ticker changes | Display-only expander |
| Settlement GETs | About every 60s, and only for stored markets whose `close_time` is already due and not yet in `resolutions` | Stops re-fetching the live open contract forever |
| Coinbase observations to SQLite | At most one row per exchange-timestamp second, batched | The in-memory deque still keeps the live window used for trade pressure. A chatty WebSocket does not commit every print |

Coinbase WebSocket remains the price path, with REST ticker fallback when the latest print is older than 3 seconds.

## Code quality

- Dead `html` and `os` imports removed from `app.py`. The UI still coalesces a missing `series` to `{}` before `.get`.
- The collector treats a missing series object as “no fee schedule” instead of calling `.get` on `None` during cold start or a bad payload.
- `.gitignore` ignores `.env`, SQLite files, virtualenvs, and `artifacts/`. `.env.example` lists the public settings the README already described.
- Virtual fee math and `Risk` defaults were checked against the existing unit tests and left in place. See [PAPER.md](PAPER.md). There is still no real-money execution.

## What this plan does not do

- It does not tune the baseline to look more certain, and it does not report a win rate from synthetic data.
- It does not add BRTI, authenticated Kalshi sockets, or any paid source.
- It does not submit orders. Paper mode writes a local SQLite ledger only, and it stays off until both sidebar switches are on.
