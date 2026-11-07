# Trade audit from the 2026-11-06 archive

Source: the read-only backup in the audit package, checksum `f9afe6ccda3b3c20eb2649b0b1cdda168839cfc567e61cd18b6fb8c93b5fc83a` (`payload/runtime_consistent.sqlite`), plus depth segments 0001–0005. Package checksums matched `SHA256SUMS`. This file does not change that ledger, the thresholds, or a model.

Regenerate the machine-readable rows with:

```bash
python -m research.attribute \
  --sqlite /path/to/runtime_consistent.sqlite \
  --depth /path/to/depth \
  --output docs/trade_audit.json
```

The connection is `mode=ro`. The command does not retune rules.

## Ledger at backup

| Item | Value |
|---|---|
| Execution | `taker-confirm-v1`, fee schedule `kalshi-general-table-cent-2026-07-07`, model `statistical-proxy-v1` |
| Baseline cash | 100, recorded `2026-11-05T23:19:34.950801Z`, armed `2026-11-05T23:19:38.957682Z` |
| Paper commit | `ad649e7335930913d869171b2ef4c7e5490d3f66` |
| Account | initial 100, cash **81.42** |
| Positions | **26** (25 resolved, 1 open) |
| Resolved | **9** payout > 0, **16** payout 0, realized **−16.92** |
| Open | id 26, `KXBTC15M-26NOV061615-15`, side **no**, quantity 2.97, price 0.54, fee 0.0562, cost **1.66**, unresolved |
| Equity at cost | 81.42 + 1.66 = **83.08** = 100 − 16.92 |
| Intents | 53 = 26 filled + 27 expired, every expiry reason `price_through_limit` |
| Settlement conflicts | 0 |
| Predictions | 5280 rows, 30 tickers |
| Markets with a stored result and a forecast | 29 |

`status.json` at `2026-11-06T21:06:07Z` still showed cash 83.08, 25 resolved, and 0 open. The consistent backup is later: the same 83.08 equity, with 1.66 moved from cash into the open position. Use the backup for the fill list.

## Forecast sample is not the fill sample

Last forecast per resolved market: **29** contracts, accuracy **0.931**, Brier **0.0633**. Median time left on that snapshot is **61 seconds**. Split by horizon, the 23 contracts with remaining ≤ 120s are accuracy **1.000** (Brier 0.024), and the 6 with remaining > 120s are accuracy **0.667** (Brier 0.215). The headline is the near-cutoff snapshot. Every fill in this file was decided with more than 180 seconds left. Details and the command are in [RUNTIME_AUDIT.md](RUNTIME_AUDIT.md). The package ends at the `21:06Z` backup. A later verbal tally (cash 79.31, 28 resolved, 29 fills) is not in this file.

## What the 26 fills were

All 26 are `taker-confirm-v1`. Side counts: **23 no, 3 yes**. Mean time from decision to confirm is about **3.4 seconds**. Every fill's stored side-probability still cleared the 0.03 edge rule at the fill price. **16 of 26** had side probability **below 0.5**: the ask was cheap enough versus that probability, after the cent fee, to enter, and the model's own probability then says a loss is the more common outcome. Summing `probability × quantity − debit` on these 26 fills is about **+$7.00**. Realized result on the 25 settled fills is **−$16.92**. That gap is this sample. It is not a refit, and the entry rule was not changed to chase it.

The 27 non-fills all expired `price_through_limit`: the later book was worse than the limit, so the rule did not trade. They are not extra losses.

## Depth replay

Depth file: 5956 JSON records = 1 `started` + **5955** snapshots, **28** tickers, **5518** two-sided. Span `2026-11-05T23:43:57.718534Z` through `2026-11-06T21:06:50.278633Z`. Flag counts on snapshots: 385 one-sided, 50 missing, 50 error, 3 slow, 2 empty, 2 local-after-close. **No `stopped` record.** The configured `--until` was `2026-11-06T23:20:00Z`, which this file never reached.

A replay counts only a two-sided ladder for the same ticker received **after** the decision and **at or before** the confirm time. It rebuilds the ask as `1 − best opposite bid`. It is a nearby recorder poll, not the paper process's HTTP response, not a queue, and not a print.

| Evidence | Fills |
|---|---|
| Recorded ask equals the fill price | 15 |
| Recorded ask differs (differences −0.03, −0.02, −0.01, −0.003, −0.001, or +0.01) | 9 |
| No covering two-sided book | 2 (ids 1 and 2, confirmed `2026-11-05T23:20:04Z` and `23:36:21Z`, before the first depth snapshot) |

Where the ask differs, the recorder and the paper process did not observe the same response. Those nine are not corrected and are not used to pick a new limit.

## Not claimed

The run was not uninterrupted through `2026-11-06T23:20:00Z`. Writers' last evidence is 21:06Z. Coinbase was the probability proxy, not BRTI. Nothing here says the 25 outcomes were mis-settled: conflicts are 0, and this audit did not re-derive labels from spot.
