# Current ledger audit — cutoff 2026-11-07T00:25:39Z

Source: the sanitized archive whose file hash is `8a9d5dd6be1f1838ee957522da27fcc4172123a816111e285e59d46553ad97d1` (4,915,989 bytes). Every file in its `SHA256SUMS` matched, including `runtime_consistent.sqlite` (`8c0e6d9ffda8f67bf261a7fe8928ce1c01ee5e7e3c2b2f53de46492daad911d4`). `MANIFEST.json` cutoff is `2026-11-07T00:25:39.252764+00:00` (18:25:39 America/Chicago). Last quote and prediction in that sqlite: `2026-11-07T00:25:38.423855+00:00`.

`verification.json` is a few seconds later (`2026-11-07T00:25:45Z`, 6,073 quotes). This note uses the sqlite, not that later count. The archive does not include writes after 00:25:39Z.

Deployment `75920e7` is recorded as started `2026-11-07T00:25:29.099953Z`, paper-only, reset false, tests 73 passed. Processes at verification: paper pid **70391** (`research.resume --attach`) and depth pid **70392** (`--until 2026-11-14T00:40:00Z`). This audit did not start, stop, or redeploy them.

```bash
python -m research.audit_runtime \
  --sqlite /path/to/runtime_consistent.sqlite \
  --depth /path/to/depth \
  --output docs/current_audit.json
```

`optimizes_rules` is false. No edge cutoff was changed.

## Cumulative ledger, then the cohorts

Baseline cash **100**, armed `2026-11-05T23:19:38.957682Z`, `taker-confirm-v1`, `statistical-proxy-v1`. Cash **77.02**. One open cost **1.57**. Equity at cost **78.59** = 77.02 + 1.57 = 100 − 21.41. Settlement conflicts **0**. Intents: 33 filled, 37 expired, every expiry `price_through_limit`. All 33 fills match the July 7 2026 cent fee. Every confirm is later than the decision; mean latency **3.44s**; none are under 3 seconds.

The open position from the 21:06Z backup (id 26) is settled in this file with payout 0. That is why the pre-stop cohort is no longer 9 wins and 16 losses.

| Cohort | Clock | Fills | Settled | Payout > 0 | Realized | Side p < 0.5 |
|---|---|---|---|---|---|---|
| Before the 21:06Z stop | decision < `2026-11-06T21:06:50Z` | 26 | 26 | 9 | **−18.58** | 16 |
| Resume, before this deploy | `21:06:50Z` ≤ decision < `2026-11-07T00:25:29Z` | 7 | 6 | 2 | **−2.83** | 5 |
| After `75920e7` | decision ≥ `2026-11-07T00:25:29Z` | **0** | 0 | 0 | 0 | 0 |

Model value `probability × quantity − cost` is about **+$7.00** on the 26 pre-stop fills and about **+$2.91** on the 7 resume fills. The resume figure includes the still-open fill. Realized dollars do not. None of this is a new threshold, and it is not evidence the book is profitable.

There is no post-deploy fill cohort in this file. The process had been up for about ten seconds at the cutoff. Cash 77.02 before and after the deploy, as recorded, is the same cumulative ledger, not a new sample.

## The paper book is in the quote row

For every fill, `quotes.observed_at` equals `signal_book_at` and equals `confirm_book_at`.

| Comparison | Exact | Float dust (≤ 1e-9) | Different price | Missing |
|---|---|---|---|---|
| Decision quote ask vs intent limit | 22 | 11 | 0 | 0 |
| Confirm quote ask vs fill price | 23 | 10 | 0 | 0 |

Float dust is binary residue from the quote column, which stores `1 − bid` as a float. It is not a one-tick disagreement. Those 33 confirm rows are the paper process's own book. They are not depth-recorder polls.

## Depth is a different poll

On the depth segments in this archive (6,167 snapshots, `2026-11-05T23:43:57Z` through `2026-11-07T00:25:35Z`, one `gap` row):

| Asynchronous label | Fills |
|---|---|
| Recorded ask equals the fill | 15 |
| Recorded ask differs | 10 |
| No two-sided snapshot in (decision, confirm] | 8 |

The 10 differences are one to three cents: three at −0.01, three at +0.01, and one each at −0.02, −0.03, −0.001, and −0.003. Each of those windows has a single two-sided depth row, received about one to three seconds before the paper confirm. That is as precise as two unsynchronized polls get. It is not the paper HTTP response, and it does not contradict the quote row that matches the fill.

The 8 missing books are the two confirms before depth started, plus six resume-window confirms that fall in holes. The depth file's own gap is `2026-11-06T22:08:37.921184Z` → `2026-11-07T00:25:29.346688Z` (**8211.4s**, 136.9 minutes). `previous_received_at` is copied from a segment line. No polls were inserted for that interval.

## Horizon, not a 90% edge

Last stored forecast on resolved contracts in this file: **36** contracts, accuracy **0.917**, Brier **0.060**, median **61s** left. Remaining ≤ 120s: **28** contracts, accuracy **1.000**, Brier **0.022**. Remaining > 120s: **8** contracts, accuracy **0.625**, Brier **0.193**.

At the entry horizon (last forecast that still had at least 180 seconds left): **34** contracts, Brier **0.103**, accuracy **0.824**, mean probability **0.496**, outcome rate **0.559**.

The 32 settled fills, scored on the side probability stored at the decision: Brier **0.154**, mean side probability **0.48**, outcome rate **0.34**. Decisions sat between **160s** and **881s** left (median **744s**). The near-expiry 100% figure is a different clock. The traded subset is also a selected slice of the entry-horizon population, not that population. Sample sizes 32 and 34 do not support a new minimum.

## Coverage still in the file

Quote holes longer than five minutes are still present, including the earlier **402, 91, and 424** minute stretches, then **48** minutes from `21:06:49Z` to `21:55:16Z` (the fork) and **79** minutes from `22:08:35Z` to `23:27:20Z` inside the recorded depth gap. A 15-minute series was not "inactive" for those hours. The file simply has no samples. The in-guest recorder cannot write a gap row while the machine is gone; this archive's one gap row was written when depth started again at the deploy.

## Not claimed

The cumulative result is a loss of **$21.41** at cost on settled fills, with **$1.57** still open. That is not profitability and not a live-trading clearance. Coinbase is the proxy, not BRTI. No model artifact was loaded. No order was sent.

## What changed in this pass

`research.audit_runtime` now joins the paper quote on `observed_at`, splits the three clocks above, and keeps depth in a separate `depth_async` block with `same_http_response_as_fill: false`. No fee, latency, or edge rule changed. No column was added to the ledger, so pid 70391 does not need a restart for this report.

## Still unresolved

- Why the writers were quiet for the multi-hour holes before 21:06Z.
- Whether a one-to-three-cent depth difference is a moved book or a different level inside the same second. The polls are not paired, so that is not decidable here.
- Any post-deploy fill. This cutoff has none. Another backup after more contracts would be a new sample, not a reason to refit on these 33.
