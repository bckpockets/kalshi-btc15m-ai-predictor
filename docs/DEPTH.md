# Historical order-book depth

Checked **2026-11-05**. The question was whether the 160 settled `KXBTC15M` contracts from `2026-11-04T05:15:00Z` through `2026-11-05T23:15:00Z` have public, free, timestamped bid ladders with sizes. They do not, on the interfaces checked below. No capture process was started, and the running paper database was not opened.

Depth here means a yes-bid ladder and a no-bid ladder (or an equivalent ask ladder) with sizes at stated times. A candle's bid/ask OHLC is not that. A trade print is not that. The current order book is not that.

## Available now, and what it is not

| Source | Status | What was verified |
|---|---|---|
| `GET https://external-api.kalshi.com/trade-api/v2/markets/{ticker}/orderbook` | **Available, current book only** | Docs: https://docs.kalshi.com/api-reference/market/get-market-orderbook. For `KXBTC15M-26NOV040215-15` the public response was `{"orderbook_fp":{"yes_dollars":[],"no_dollars":[]}}` with no as-of timestamp. That is the book after settlement, not the book during the 15 minutes. |
| `GET /series/KXBTC15M/markets/{ticker}/candlesticks` | **Available, not depth** | Docs: https://docs.kalshi.com/api-reference/market/get-market-candlesticks. One request for that ticker, `period_interval=1`, over its 15-minute life, returned 15 bars. Keys were `end_period_ts`, `yes_bid`, `yes_ask`, `price`, `volume_fp`, `open_interest_fp`. `yes_bid` was OHLC dollars (`open` `0.0010`, `high` `0.5800`, `low` `0.0010`, `close` `0.5200` on the first bar). No size field. Volume is contracts traded in the minute, not size resting at a price. |
| `GET /markets/trades?ticker=` | **Available, prints only** | One row for the same ticker: `created_time` `2026-11-04T07:14:59.843567Z`, `count_fp` `915.99`, `yes_price_dollars` `0.9990`, `taker_side` `no`. A print has a size and a time. It does not show the resting ladder. |
| `GET /historical/markets/{ticker}/orderbook` and `GET /markets/{ticker}/orderbook/history` | **Not a documented endpoint** | Both returned HTTP 404 `page not found`. |
| WebSocket `orderbook_delta` | **Live only, and it needs an API key** | Docs: https://docs.kalshi.com/websockets/orderbook-updates. The channel sends `orderbook_snapshot` then deltas for the current subscription. Authentication is required. There is no documented replay of past clocks. |

Markets in this window settled after the archive cutoff (`market_settled_ts` `2026-09-06T00:00:00Z`), so their candles are still on the live candlestick route. Moving them to `GET /historical/markets/{ticker}/candlesticks` would not add sizes. That historical schema is the same OHLC shape: https://docs.kalshi.com/api-reference/historical/get-historical-market-candlesticks.

## Third parties

These are independent of Kalshi. Prices below are what their public pages showed on 2026-11-05. None of these calls used an account or a key.

### Kalshi Eye — claimed archive, study window not in the free file

- Catalog: https://kalshieye.com/data/kalshi/
- Series page: https://kalshieye.com/data/kalshi/kxbtc15m/
- Pricing: https://kalshieye.com/pricing/
- They say recording started **2026-10-12**, that each UTC day is published the next morning, and that Kalshi's own API has no past order book. They also say they are not affiliated with Kalshi.
- Full incremental L2 (`book_l2`: `ts_recv`, `ts_venue`, `ticker`, `seq`, `is_snapshot`, `side`, `price`, `size`) is on the **Full book** plan, listed at **$74.50/month** founding price and **$149** list. Top-of-book and 1-second top-10 snapshots are on the **Research** plan, **$39.50/month** founding and **$79** list.
- Free, no account: `https://kalshieye.com/api/v1/samples/book_l2.csv` (641,137 bytes, 5,000 rows) and `https://kalshieye.com/api/v1/samples/bbo.csv` (694,949 bytes, 5,000 rows).
- Those two files contain only `KXBTC15M-26NOV031900-00` and `KXBTC15M-26NOV031915-15`. L2 timestamps run from `2026-11-04T00:00:00.000000000Z` to `2026-11-04T00:00:02.697728768Z`. BBO runs to `2026-11-04T00:00:15.133824512Z`. **0 rows** fall in `2026-11-04T05:15:00Z`–`2026-11-05T23:15:00Z`. **0** of the 160 study tickers appear.
- Inside that two-second preview, 224 of 422 receive-timestamps include both a `yes` and a `no` row, and 4,966 of 5,000 BBO rows have both `yes_bid_size` and `yes_ask_size`. So the schema can carry two-sided size. It does not cover this study.
- A full sample day is on Hugging Face as https://huggingface.co/datasets/KalshiEye/kalshi-order-book-sample, dataset card license **CC-BY-NC-4.0**, described as **2026-10-13** for seven series. That is public and not this window. It was not downloaded. Their docs also say a free account can download `KXBTC15M` for **2026-10-25**. That still is not Nov 4–5, and it needs an account, so it was not requested.
- Whether their paid files are complete for these 160 tickers was **not verified**. They publish an incidents dataset for recorder gaps; reading it for these dates requires the paid or account download.

### DepthFeed — historical route blocked without a key

- Docs: https://depthfeed.com/docs
- `GET https://api.depthfeed.com/v3/demo` returned HTTP 200 at `2026-11-05T23:36:09Z` with a Kalshi `yes` and `no` ladder and sizes. The book timestamp converted to `2026-11-05T23:36:06.982Z`. That is a live demo, not the study window.
- `GET /v3/kalshi/KXBTC15M-26NOV040215-15/snapshots?limit=1` with no key returned **401** `AUTH_MISSING`.
- The docs say historical snapshots require an API key from a free or paid account, and that a market which closed before their capture has candles but no snapshots. Their capture start for `KXBTC15M` was **not verified**. Dollar prices for Quant/Desk were not on the pages used here, so no price is stated.

### ProbSights — not reached

- Docs describe `https://api.probsights.com/v1/historical/kalshi/orderbook` with `yes_bids` and `no_bids`: https://docs.probsights.com/api-reference/kalshi-orderbook
- One unauthenticated request to that host timed out during the TLS handshake. Coverage, license, and price for this window are **unverified**. No retry loop was run.

## What was not done

No account was created, no key was used, and nothing was purchased. The running paper process and its database were not changed. A prospective recorder was not deployed.

A recorder that would cover later contracts, and only later contracts, would append the public `orderbook_fp` ladders the collector already fetches for the active ticker, with the local receive time, into a new file that is not the paper database. It cannot reconstruct Nov 4–5. It would still be a sampled book, not proof of a queue position or of a trade at that size. It should not be turned on without a separate request.
