# Historical labels and a predictive study

This is not a trading backtest and not a change to the paper ledger. Official Kalshi `yes`/`no` results are the only labels. Coinbase one-minute closes are a proxy price for features. They are not CF Benchmarks BRTI and they are not evidence of a fill. Settled-market bid/ask fields and `expiration_value` are ignored because they are not a point-in-time book.

No model file is written. `MODEL_PATH` and the live paper database are not touched. Do not pass a cache path that contains `kalshi-paper`.

## Public sources used

Fetched 2026-11-05. No API key.

| Source | URL | Role |
|---|---|---|
| Archive cutoff | `GET https://external-api.kalshi.com/trade-api/v2/historical/cutoff` | `market_settled_ts` was `2026-09-06T00:00:00Z`. Older markets move to `/historical/markets`. This sample is after that cutoff, so it uses the live settled list. |
| Settled markets | `GET /trade-api/v2/markets?series_ticker=KXBTC15M&status=settled` | Ticker, open/close, `floor_strike`, `result`, `settlement_ts`, rules. Limit 100, cursor pagination. Documented maximum page size is 1000. |
| Historical markets and candlesticks | `GET /historical/markets` and `GET /historical/markets/{ticker}/candlesticks` | Required only for markets settled before the cutoff. Candlesticks are YES bid/ask and trade-price OHLC, not depth. This bounded run did not request them. |
| Coinbase candles | `GET https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=60` | One-minute proxy bars. The timestamp stored is the bar end (exchange start plus 60 seconds). |

Docs: [historical data](https://docs.kalshi.com/getting_started/historical_data), [historical markets](https://docs.kalshi.com/api-reference/historical/get-historical-markets), [historical candlesticks](https://docs.kalshi.com/api-reference/historical/get-historical-market-candlesticks). Fills, orders, and positions on those historical routes are account endpoints and were not called.

## Reproduce

From the repository root, with the project environment:

```bash
python -m research.history --cache data/historical --max-contracts 160 --max-pages 2 --max-requests 30
python -m research.study data/historical --output docs/historical_report.json
```

The cache is resumable: a second run reuses `data/historical/markets.jsonl` and does not page Kalshi again. Raw cache and the Coinbase file are gitignored. The command is rate-limited (about 0.35s between GETs) and stops at the page and request caps. It does not walk the full history back to the September cutoff.

## What was collected

Numbers below are the run that produced `docs/historical_report.json`.

- Market fetch `2026-11-05T23:29:44Z`: cutoff plus two pages, **160/160** contracts accepted (`finalized`, rules matched, strike present, `settlement_ts` at or after close). Labels: **89 no, 71 yes**.
- Candle repair `2026-11-05T23:30:18Z`: the first candle loop returned after one chunk; that bug was fixed before scoring. The market cache was reused. **10** Coinbase requests, **2671** one-minute bars, **0** gaps longer than 90 seconds.
- Coverage: contracts from `2026-11-04T05:15:00Z` through `2026-11-05T23:15:00Z` (42 hours). Feature rows from `2026-11-04T05:20:00Z` through `2026-11-05T23:12:00Z`.
- Three snapshots per contract, 600, 300, and 180 seconds before close. **480/480** planned rows had an exact candle end. None were interpolated. **0** feature rejections.
- Book imbalance and trade pressure are missing and stored as neutral zero.
- `floor_strike` is taken from the settled payload and treated as knowable at `open_time` because the rules define it as the pre-open average. There is no separate publication tape, so a late strike revision would not be visible.

## Scores

Predictive only. The final fold trains on earlier contracts and scores the last 32. Blend weight was chosen on the training blend slice, not on the test. Contract bootstrap resamples tickers (400 draws).

| Fold | Train / test contracts | Statistical Brier (last forecast) | Constant 0.5 Brier | Train-rate Brier | ML Brier | Blend weight |
|---|---|---|---|---|---|---|
| Earlier window | 96 / 32 | 0.057 | 0.250 | 0.250 | not fit on this fold | — |
| Final holdout | 128 / 32 | 0.120 | 0.250 | 0.242 | 0.160 | **0** (ensemble copies the statistical baseline) |

On the final 32 contracts the tree's last-forecast Brier (0.160) and log loss (0.504) are worse than the statistical baseline (0.120 and 0.378). The selected ensemble weight is zero. That is not an accuracy gain.

The statistical baseline's snapshot Brier is lower than a constant 0.5 on both folds, and the contract-resampled difference does not cover zero in this window (`baseline_minus_half` in the report). This is a description of about two days in which many contracts finish far from the strike. It is not a claim of future accuracy, not a BRTI result, and not a fill.

## Readiness

**Not ready.** No artifact is written. Reasons are in the report: proxy prices, no depth, one short regime, and no authority to load a file into the paper process.

Executable simulation is **refused**. `research.backtest` still requires historical ask, depth, quote time, and fee multiplier. Those columns were not created.

## Leakage controls

- Every row of a ticker stays in one split (`research/chrono.py`).
- Training contracts whose `resolved_at` is not before the test window opens are dropped.
- Calibration and the blend weight do not see the test contracts.
- `expiration_value`, settlement value, and settled bid/ask never enter the modeling frame.
- A missing candle drops the snapshot. It is not filled from a neighbor or from the strike.
- Walk-forward (`python -m research.walk_forward`) uses the same contract rules, still requires 250 contracts, and refuses `--write-artifact`.
