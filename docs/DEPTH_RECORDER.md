# Prospective order-book recorder

Public REST snapshots of the active `KXBTC15M` book. This is a separate process from the paper ledger. It does not read or write `runtime.sqlite` or `MODEL_PATH`, and it does not attach to a running paper process.

A record is the ladder the HTTP response contained when this process received it. `response_received_at` is local receive time. The order-book payload has no exchange timestamp, and none is invented. Polls are not every matching-engine event, not a queue, and not a backfill of Nov 4–5.

## Command

Stop time for the remaining paper window:

```bash
python -m research.depth_recorder \
  --output "$HOME/kalshi-depth-prospective" \
  --until 2026-11-06T23:20:00Z \
  --poll-seconds 3
```

`--until` is required and must be timezone-aware. The process exits after a final `stopped` record and an `fsync`. `SIGINT` or `SIGTERM` does the same, once the current sleep (at most one poll) ends.

The output path is refused if it is a sqlite file or contains `kalshi-paper`. Do not point it at the paper database directory.

## What each snapshot contains

Schema `depth-snapshot-v1`.

- Ticker, and the market `open_time`, `close_time`, `price_level_structure`, and `price_ranges` from the last metadata GET (local `metadata_received_at`).
- `orderbook_fp.yes_dollars` and `no_dollars` as returned: `[price_string, size_string]` pairs, not floats. Array order is the API's order. The best bid is the maximum price, not necessarily the first element.
- `request_started_at`, `response_received_at`, and `latency_ms` from the local monotonic clock.
- Flags: `empty`, `one_sided`, `malformed`, `missing`, `error`, `local_after_close`, `slow_response` (latency over `--slow-response-ms`, default 5000). `local_after_close` compares local receive time with the metadata `close_time`. It is not an exchange staleness bit.
- `unchanged_from_previous` when the ladder strings match the previous successful ladder for that ticker. The row is still appended, because the receive time is new evidence.
- Provenance: `kalshi-public-rest` and the request path. Default book query is `depth=0`, which the public docs define as all levels the endpoint returns.

Discovery uses `GET /markets?series_ticker=KXBTC15M&status=open` when there is no active contract or fewer than 20 seconds remain. The current market metadata is refreshed about every `--metadata-seconds` (default 60). Failures are recorded and the pause backs off up to 30 seconds. A torn last line is left on disk and a new segment is opened. Segments gzip when they pass `--segment-bytes` (default 8 MiB) only after the gzip copy matches. The plain segment is then removed; older segments are not deleted to enforce a quota. At `--max-total-bytes` (default 512 MiB) the recorder writes a stop record if it still fits and exits.

## Measured sample

A 12-second run on this VM at `2026-11-05T23:42:16Z` stop, poll 3 seconds, wrote 4 snapshots of `KXBTC15M-26NOV051845-45`. Both sides were present. Sizes stayed strings (for example `"0.0010"`, `"971503.00"`). Mean record size **7665 bytes**, maximum **7666**. Latency was about 53 ms. The files are in `data/depth-prospective/` and are gitignored.

From the first snapshot's receive time through `2026-11-06T23:20:00Z` is about 85,075 seconds, or **28,358** polls at 3 seconds. At the measured mean that is about **207 MiB** of snapshot JSON. The default 512 MiB cap is above that sample. It is not a guarantee: a deeper book would be larger. Give the output volume at least **600 MiB** free. This sample is four books, not a day.

## Deploy beside the paper process

The runtime executor starts this. It was not started here as a 24-hour job.

- One process, one output directory. Two writers on the same directory will interleave lines.
- Same Python environment as the repo (`httpx` is already a dependency). No API key and no third-party data account.
- Network egress to `https://external-api.kalshi.com`.
- Do not restart the paper runner. Do not set `DATABASE_PATH` or `MODEL_PATH` for this process.
- Suggested directory: `$HOME/kalshi-depth-prospective` or another path outside the paper sqlite.
- Confirm the first stdout JSON shows `"stopped"` only when the process is meant to end. A long run stays in the foreground until `--until` or a signal.
- After it stops, `stopped.reason` in the last record should be `until`.
