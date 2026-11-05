# Execution audit — taker-confirm-v1

Hypothetical ledger only. This file does not claim that a displayed book can be hit in production, and it is not investment advice. Forecast skill and fill accounting are different questions. Coinbase is not the CF Benchmarks BRTI. The final minute stays suspended. `statistical()` remains the fallback. No untrusted XGBoost file is loaded.

Checked on **2026-11-05**. Sources:

- Kalshi order book note, fetched 2026-11-05: <https://docs.kalshi.com/getting_started/orderbook_responses>. The book is bids only. A YES ask is `$1 − best NO bid`. A NO ask is `$1 − best YES bid`. Worked example: YES bid `$0.42`, NO bid `$0.56`, YES ask `$0.44`.
- Kalshi fee schedule PDF, effective **July 7, 2026**: <https://kalshi.com/docs/kalshi-fee-schedule.pdf>. Taker fee `round up(M × 0.07 × C × P × (1−P))`. The general table charges `$0.02` on one contract at `$0.50` and `$1.75` on 100 contracts at `$0.50`. Trading fees apply to orders that match immediately. Resting orders are not charged that taker formula. The prose also says the round-up aligns fee plus premium to a **centicent** (`$0.0001`).
- Fee rounding note, fetched 2026-11-05: <https://docs.kalshi.com/getting_started/fee_rounding>. Model fee is ceiled to `$0.000001`. A further rounding fee aligns the balance to `$0.01` for a non-direct member and `$0.0001` for a direct member.
- Series metadata carries `fee_type` and `fee_multiplier`. `quadratic`, `quadratic_with_maker_fees`, and `quadratic_with_combo_maker_fees` share the taker formula above. Maker schedules are not used. `flat` is rejected because the multiplier does not determine it.

The ledger implements the **cent** debit (premium + 6-decimal trade fee, ceiled to `$0.01`). That matches the published table, including `$0.02` rather than the raw `$0.0175` at `$0.50`. It does **not** assume direct-member centicent rounding, which would cost the same or less. Member status is unknown, and no order was sent to compare an invoice.

Price grids come from the market payload `price_ranges`. The November 4, 2026 KXBTC15M snapshot used `tapered_deci_cent` (`0.001` outside `$0.10–$0.90`, `0.01` inside). The engine does not hard-code that grid. A missing or off-tick book fails closed.

## What changed

| Severity | Finding | What the code does now |
|---|---|---|
| High | A fill could use the same book that produced the signal | Intent first. Fill only on a strictly later book |
| High | No latency. The next poll could be treated as instantaneous | Default 3s minimum (`EXECUTION_LATENCY_SECONDS`). Earlier books do not count as the attempt |
| High | A missed price could sit like a resting order | The first eligible later book is the only attempt. If it is through the limit, crossed, thin, or unusable, the intent expires |
| High | Opening an old paper file could mix optimistic rows with a new model, or ALTER that file | Positions, or cash that no longer equals the initial balance, with no manifest: read-only. No new tables, no journal-mode change, no settlement rewrite |
| High | YES/NO asks taken from the wrong side would buy the bid | Ask is the complement of the opposite best bid. Both sides must be present. Off-tick prices are rejected |
| Medium | Depth was an integer truncation of one level, with no partial on a later size | Later touch only, summed at that exact price, floored to `0.01` contracts, never rounded up. Quantity cannot exceed that later size or the reserved debit |
| Medium | Cash could be promised twice before a fill | Open intents reserve their debit. Account cash does not move until a fill |
| Medium | Fee helper ceiled the model fee to a cent and could disagree with premium alignment on sub-cent prices | Live path uses the cent debit above. `fee()` remains for the old helper and the old unit tests |
| Medium | A second official result could be confusing | The first stored label is kept. A different label is appended to `settlement_conflicts` and does not change payout or cash. NULL legacy labels are not backfilled |
| Medium | Equity was only cost, but easy to read as a mark | Cost equity stays the accounting identity. Liquidation is bid minus taker fee, and only if that bid's size covers the position. Otherwise the UI says the mark is unavailable |
| Low | Fill win rate can be read as calibration | The portfolio caption separates them. Filters select the sample |
| Low | `research/backtest.py` still replays one contract on the same row | Unchanged numbers. Its limitations string says it is not `taker-confirm-v1` |

`entry()` in `btc15/paper.py` is the previous same-snapshot fill. The engine does not call it. Once `run_manifest` exists, `entry()` returns without writing.

## Model rules

`EXECUTION_MODEL = taker-confirm-v1`.

`FEE_SCHEDULE = kalshi-general-table-cent-2026-07-07`.

A new database gets one manifest row at process start: cash, open count, open cost, resolved count, forecast version, provenance JSON. `fills_armed` stays 0. **Arm taker fills** is a second commit. It does not reset cash or edit the baseline. A restart reads the same row.

Fills also require both switches, recognized BRTI rules, a published threshold, at least 90 seconds left by default (and never inside the final 60), a quadratic-family multiplier, a fresh book (`MAX_DATA_AGE`), and a price grid. One filled position per ticker by default. Intents older than `EXECUTION_INTENT_SECONDS` (default 20) expire.

## What is verified

- `pytest -q`: 40 passed on 2026-11-05 in this workspace (`.venv`, Python 3.12). Includes the previous suite and `tests/test_execution.py`.
- Fee table rows listed above match `taker_charges` exactly, including the `$0.50` / `$0.02` case.
- Complement math matches the public order-book example (`0.42` / `0.56` → YES ask `0.44`).
- Same-snapshot and sub-latency books do not insert a position or move cash. A book at least 3s later can. A second confirm does not debit again.
- A worse later ask expires the intent. A still-later better book does not fill that expired intent.
- Later size `12.5` fills `12.5`, not the larger signal size.
- Crossed, stale, one-sided, off-tick, flat fee, unsupported rules, cutoff, final minute, and switches-off do not fill.
- A sqlite file seeded with a position and cash `1038.34` stays free of `run_manifest`, `intents`, and `predictions`. Settlement does not change its payout.
- After a fill, `start_run` again reports the original baseline cash of `1000`, not the post-fill cash.
- Conflicting settlement label does not change cash or payout.
- Streamlit `AppTest` empty state still renders five tabs with simulation off.
- `statistical()` still rejects the final minute. No model artifact is added.

## What is not verified

- No order was placed. No fee invoice, membership tier, or waiver was observed.
- Direct-member centicent rounding was not implemented and not measured.
- The later book still showing size is not proof a real taker would be matched, and it is not proof of queue position. There is no trade-id check that the ask printed.
- Only the best price is used. The book is not walked.
- Default 3s is one collector poll, not a measured gateway or matching delay. A quote can die between snapshots.
- The operator report of **4:49 CST** (16:49 America/Chicago, 22:49 UTC) on 2026-11-05 — 7 settled, 6 wins, 1 loss, `+$47.96`, cash `$1038.34`, one open position `$9.62` — was not recomputed here. That database is not in this VM. Those fills were not checked for live feasibility. Do not import them into `taker-confirm-v1`.
- No 24-hour shadow run was executed in this workspace.
- `research/backtest.py` remains a different, more optimistic replay.

## Safe runtime procedure

1. Leave the existing process running. Do not change its environment. Do not open its sqlite file with this build. In particular, do not set `DATABASE_PATH` to `/workspace/kalshi-paper/runtime.sqlite` or any other live ledger.
2. Create an empty directory and a new file name, for example `$HOME/kalshi-paper-taker/runtime.sqlite`.
3. Start with `STARTING_BANKROLL=1000` and no API keys. Leave `MODEL_PATH` unset or pointed at a missing file unless you trained that artifact yourself.
4. Before any switch and before **Arm taker fills**, read the Paper tab and the manifest query in [PAPER.md](PAPER.md). Require cash `1000`, open `0`, resolved `0`, `fills_armed=0`, model `statistical-proxy-v1` if no artifact is loaded, execution `taker-confirm-v1`.
5. Write those values down. If they differ, stop.
6. Click **Arm taker fills**. Confirm `armed_at` is set and the baseline numbers did not change.
7. Only then enable **Enable paper simulation** and **Allow unvalidated proxy forecasts**.
8. On the next check, count intents, positions, and `settlement_conflicts` separately. A conflict means the official label changed after the first stored result and the cash was left as it was.

This procedure does not move, copy, or repair an older ledger.
