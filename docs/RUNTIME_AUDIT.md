# Runtime audit of the 2026-11-06 package

This note is the package collected at `2026-11-06T21:42:19Z`. A later backup, cutoff `2026-11-07T00:25:39Z`, is in [CURRENT_AUDIT.md](CURRENT_AUDIT.md). Do not read the cash and win counts below as that later file.

The attached archive is the package collected at `2026-11-06T21:42:19.797548Z` (`evidence/collected_at_utc.txt` is `2026-11-06T21:40:53Z`). Payload checksums in `SHA256SUMS` matched. Its sqlite backup is `payload/runtime_consistent.sqlite`. The last paper `status.json` write in the package is `2026-11-06T21:06:07Z`. The last depth receive is `2026-11-06T21:06:50.278633Z`. The last quote in the backup is `2026-11-06T21:06:49.341879Z`.

That file does not contain a later 24-hour tally. Cash **79.31**, open cost **1.48**, equity **80.79**, 28 resolved (10W18L), 29 fills, and 57 intents were reported separately and are not rows in this package. This note does not recompute them.

Regenerate the machine-readable summary from the backup only:

```bash
python -m research.audit_runtime \
  --sqlite /path/to/runtime_consistent.sqlite \
  --output docs/runtime_audit.json
```

The connection is `mode=ro`. `optimizes_rules` is false. No minimum-EV or probability cutoff was changed.

## Verified on this file

| Check | Result |
|---|---|
| Account | initial 100, cash **81.42** |
| Cash identity | 100 − sum of costs + sum of payouts = **81.42** |
| Positions | 26 stored, 25 settled, 9 with payout > 0, realized **−16.92** |
| Open position | still in the backup; not settled here |
| Intents | 26 filled, 27 expired, every expiry `price_through_limit` |
| Settlement conflicts | 0 |
| Fee identity | all 26 fills match `taker_charges` at multiplier 1 (cent debit, July 7 2026 table) |
| Confirm timing | all 26 confirms are later than the decision by about 3.4–4.0 seconds. None are under 3 seconds |
| Depth touch vs `depth=20` | 10,920 book sides had more than 20 levels (maximum 279). The best price was inside the first 20 levels on every one of those sides. The paper client's `depth=20` request did not drop the touch on this file |

`docs/trade_audit.json` still has the depth replay of those 26 fills: 15 asks match a later recorded book, 9 differ by at most about 3 cents, 2 have no covering book because they confirmed before the first depth snapshot. A match is a nearby poll, not the paper process's HTTP response.

## Forecast accuracy is a near-expiry snapshot

`research.audit_runtime` on the 29 resolved contracts' last stored forecast:

| Slice | n | Accuracy | Brier | Median seconds left |
|---|---|---|---|---|
| All last forecasts | 29 | 0.931 | 0.063 | 61 |
| Remaining ≤ 120s | 23 | 1.000 | 0.024 | 61 |
| Remaining > 120s | 6 | 0.667 | 0.215 | 537 |

The 93% figure is 23 contracts whose last stored probability is about one minute before the final-minute suspension, plus 6 contracts whose recording stopped earlier. It is not leakage of the official label: the label is the stored Kalshi result, and settlement conflicts are 0. It is the wrong denominator for the trades. Every one of the 26 fills was decided with more than 180 seconds left. On those fills the stored side probability is below 0.5 on **16 of 26**. Summing `probability × quantity − cost` on the 26 fills is about **+$7.00**, including the open fill. Realized result on the 25 settled fills is **−$16.92**. A price can be cheap enough to clear the existing $0.03 edge while the model still says that side loses more often than not. This sample is not large enough to replace that rule, and the rule was not replaced.

The performance tab now shows the ≤120s and >120s slices next to the last-forecast number so the 93% line is not read as fill skill.

## Recording holes

Quotes, predictions, and Coinbase observations share the same quiet stretches. Depth snapshots do too. The final minute explains about 60 seconds without a forecast, not these holes. `classify_silence` ignores gaps of 120 seconds or less.

Paper quotes, longest holes:

| Seconds | Kind in the quote file | From | To |
|---|---|---|---|
| 424.34 min | cross-contract | `2026-11-06T14:01:47Z` `…0915-15` | `2026-11-06T21:06:08Z` `…1615-15` |
| 401.63 min | cross-contract | `2026-11-06T05:21:38Z` `…0030-30` | `2026-11-06T12:03:15Z` `…0715-15` |
| 90.93 min | cross-contract | `2026-11-06T12:06:02Z` | `2026-11-06T13:36:58Z` |
| 19.09, 16.16, 9.04 min | cross-contract | shorter holes the same afternoon and night | |
| 4.26 min | same contract | `…0900-00` | the same ticker |

Depth's matching long holes are 424.00 min and 400.86 min, and the 401-minute depth hole returns to the **same** ticker (`…0030-30`) at `12:02:29Z`. The quote file's next row is the following contract, so the quote classifier says cross-contract. That label means the quote rows jump. It does not mean the series was closed. Both files are missing the samples. Depth has 15 snapshots inside the 401-minute quote window and 8 inside the 424-minute window, against a 3-second poll.

`status.json` at `21:06:07Z` still names pid **110945** and an uptime of about 21.8 hours from the `23:19:34Z` kickoff. The 401-minute hole ended near `12:03Z`, hours before the `21:06:57Z` boot. The 424-minute hole ends in the same minute as that boot, after a short burst of quotes. The boot accounts for the stop at `21:06:50Z`. It does not account for the earlier quiet hours. No traceback in this package identifies the stall. Both the paper process and the depth process went quiet together, which is not the final-minute rule and not a quote-table primary key.

## What the code does with that evidence

- `coverage_allows_entry` uses the existing gap threshold `max(5 × poll, 30s)`. After a longer blind interval, the next successful cycle stores its forecast and quote and does **not** open an intent (`coverage_gap`). The cycle after that can. Cash, the manifest, and past fills are not rewritten. The $0.03 edge is unchanged.
- The depth recorder calls the same gap note on each poll. A second call for the same previous receive time does not append another row and does not invent snapshots. An in-guest loop still cannot write that row if the machine is gone.
- `python -m research.audit_runtime` reprints cash identity, fee and latency checks, the horizon split, and the silence list from a sqlite file.

## Unresolved

- Why the paper and depth writers were quiet for those multi-hour stretches, then wrote again, is not in the package.
- The nine depth asks that differ from the fill price are different polls. They were not corrected.
- The later 24-hour cash and win/loss counts are outside this archive.
- Coinbase remains a proxy, not BRTI. No model file was trained or loaded.

## Deployment

This build was not started on the coordinator, and the running paper process was not stopped. Deploying it would add the coverage gate and the in-loop depth gap only in processes started from this commit. It would not reset the $100 ledger, would not re-arm fills, and would not change historical losses. Do that only on the machine that holds the ledger, with the existing sqlite path, after reading the manifest. Do not point this VM at that file.
