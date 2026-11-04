# Validation record — November 4, 2026

This record separates observed operation from implemented but unobserved behavior.

## Verified

- Clean Python 3.11.17 virtual environment installed all pinned `requirements.lock` dependencies successfully. Core tests also ran under the host Python 3.12 environment.
- 21 automated offline tests pass and cover UTC parsing, fixed-point API parsing, pagination, candle endpoint timestamp conversion, active-market boundaries/rollover, unsupported rules and missing threshold, official final labels, averaging variance, probability bounds/complements, future-candle exclusion, missing/stale candle rejection, fee rounding/EV, paper sizing/cash conservation/idempotent settlement, chronological contract isolation, backtest quote requirements/accounting, real XGBoost fitting on explicitly synthetic fixtures, artifact inference, historical threshold leakage checks, and the dashboard empty state.
- Real public Kalshi REST responses: series metadata, active markets, individual market, order book and recent trades. The observed series had `fee_type=quadratic` and `fee_multiplier=1`.
- The completed 10:00 ET contract was fetched with official `finalized` status, `no` result and settlement timestamp; the parser returned label 0. No spot-derived label was used.
- Real Coinbase REST responses: ticker, candles, depth and 24-hour statistics.
- Real Coinbase WebSocket ticker messages were received by the collector, with timestamps, bid/ask and trade sizes. WebSocket reconnection code exists but a controlled disconnect/recovery soak test was not performed.
- Live prediction computation succeeded for `KXBTC15M-26NOV041000-00` and later discovery/forecasting succeeded for `KXBTC15M-26NOV041015-15`. This verifies both active intervals in separate checks, not an uninterrupted rollover soak test.
- Streamlit server started. AppTest rendered five tabs without application exceptions. Final live AppTest in the pinned Python 3.11 environment showed an active contract, 14:29 countdown, official threshold, fresh Coinbase observation, statistical forecast and unavailable-ML label, without warnings. These were real changing observations, not fixed demo values.
- Source UI review findings were corrected; follow-up approved those fixes at source scope. No visual approval was issued.

## Not established

- No trained model with legitimate out-of-sample results is bundled. Synthetic fixture training proves the pipeline executes, not forecasting accuracy or profitability.
- No entitled CF Benchmarks feed was available. Final-minute conditional forecasts are intentionally disabled. The public-data proxy baseline has not been calibrated against a representative contract archive.
- No multi-day reliability, resource-growth, chaos/reconnect, cross-process, or continuous interval-rollover soak test was completed.
- Paper portfolio arithmetic is tested with mocked/synthetic official labels. No live paper positions were opened during verification, so real-time virtual settlement remains unobserved.
- Historical replay was executed on explicit synthetic test fixtures. No authentic historical order-book archive was supplied; no historical ROI is claimed.
- Chromium screenshot attempts failed because this environment prohibited socket creation. Desktop/mobile visual layout, focus behavior and screenshot quality remain unverified. No screenshots are included.
- GitHub's connected tool exposed profile/repository reads and file mutation, but no repository-creation operation. The CLI was not authenticated, and the requested repository lookup returned 404. No repository was created or pushed, and remote Actions have not run.

## Important operational boundaries

Public API snapshots have retrieval timestamps, not a guarantee that an underlying quote changed recently. Requests may be cached upstream. Forecast and paper-entry gates reject stale local observations/snapshots; actual fill availability is unknowable without execution. The UI refresh is 2 seconds, but network latency can make collection slower than the requested 1–5 seconds.

Official settlement corrections/nonbinary payouts require manual review. The cash ledger intentionally does not silently infer special settlements from spot price. Multiple server processes against one DB are unsupported; browser sessions within one process share the collector and controls. Application restarts reset the simulation toggle to off while preserving balances and positions.
