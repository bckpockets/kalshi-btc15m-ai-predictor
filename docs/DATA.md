# Historical data contract

All timestamps are timezone-aware UTC ISO 8601. Never label a Coinbase observation as BRTI. Do not use revised reference values to pretend they were observable earlier.

## candles.csv

`timestamp,open,high,low,close,volume` — timestamp is **end** of one-minute candle. Coinbase's original timestamps identify starts; the live collector adds 60 seconds. Minimum 30 completed candles, up to 120 used for features. Prices and volumes must be numeric and finite. Missing minutes cause rejection rather than interpolation.

## snapshots.csv

Required: `ticker,timestamp,close_time,price,threshold,threshold_published_at`.

- `price`: observed Coinbase/proxy price at timestamp.
- `threshold`: Kalshi's official published opening threshold available at that timestamp.
- `threshold_published_at`: first known publication time; must be <= timestamp.
- Optional `imbalance,pressure`: contemporaneous values; omitted values use neutral zero, which is an explicit missing-input limitation.
- Optional `market_probability`: contemporaneous YES bid/ask midpoint, for evaluation only.
- For replay: `quote_timestamp,yes_ask,no_ask,yes_depth,no_depth,fee_multiplier`. Asks in dollars, depth in contract units, multiplier from the applicable historical series schedule.

The builder does not invent snapshots, price history or fees. It skips final-minute forecasts and raises on malformed features. The caller must ensure the spot-price timestamp and every market input were actually observable at the snapshot timestamp. Source-specific revision histories are not automatically audited.

## outcomes.csv

`ticker,label,resolved_at` — one row per contract. Label is 1 for official final YES and 0 for official final NO. `resolved_at` is the time the result became available, not a predicted close. No labels inferred from spot price, preliminary indications, or BRTI alone. Void/nonbinary contracts should be excluded and separately audited.

The live exporter records the time this application observed the official final result. This is conservatively later than actual resolution when the application was offline.

## Engineered dataset

Training uses columns in `btc15.features.FEATURES`, plus `ticker,timestamp,close_time,resolved_at,label,baseline`. Optional market probabilities and executable quotes carry through the held-out CSV. Within a contract, close time and label must be consistent. Duplicate ticker/timestamp pairs are rejected. Neither timestamp nor ticker is a model input.

## Evaluation interpretation

Snapshots from one contract are not independent samples. Report distinct contracts alongside snapshot counts. The contract-level report uses the last eligible forecast per contract; this changes the horizon distribution and is not directly comparable to every snapshot. ECE uses ten fixed probability bins. Volatility regime boundaries are predeclared absolute per-square-root-second values, not fit on the test set.

Test fixtures generate synthetic numbers only inside tests. They validate execution and accounting, not predictive power. Test artifacts are temporary and are not shipped as trained models.
