# Paper simulation

Virtual ledger only. Turning these controls on does not place Kalshi orders. There is no order endpoint in this repository. This note is an operating checklist, not investment advice and not a statement that the proxy has an edge.

## Switches

In the sidebar, under Research controls, both of these start **off** on every server process:

1. **Enable paper simulation**
2. **Allow unvalidated proxy forecasts**

The second box exists because the bundled path is the uncalibrated statistical proxy. Checking it allows hypothetical fills from that proxy. It does not mark the proxy as validated. A loaded artifact with a `test_report` is still an experimental blend; the report is descriptive of the file you trained, not a promise about the next contract.

Settings are process-wide (`st.cache_resource`). Use one server and one database.

## Bankroll

`STARTING_BANKROLL` defaults to **$1000**. It is applied only when the SQLite account row is created. An existing `runtime.sqlite` keeps its cash. There is no deposit or reset control in the UI. For a clean baseline, point `DATABASE_PATH` at a new file (or move the old one aside) and start the app once.

Open positions are valued at **cost** in the equity figure (cash plus open debit). When the current book can lift the whole position at the bid, the portfolio also shows a separate liquidation value after the taker fee. If the bid, tick grid, or fee schedule is missing, that mark is omitted rather than replaced with cost.

Fills, when armed, are `taker-confirm-v1`: one later book, one price level, no queue. The procedure and the limits are in [EXECUTION.md](EXECUTION.md). Do not point this process at an existing paper file.

## Fee and risk (checked, not retuned)

Series fees are used only when the fetched series has `fee_type=quadratic` and a positive `fee_multiplier`. Otherwise entries are blocked. The formula in `btc15/paper.py` is:

```text
fee = ceil_to_cent(0.07 × quantity × price × (1 − price) × multiplier)
edge = probability − (price + fee/quantity)
```

`Risk.min_ev` compares against that per-contract `edge`, not against total dollars for the whole order. Unit checks that still pass: `fee(100, 0.5) == 1.75`, `fee(1, 0.5) == 0.02` (0.0175 rounded up to the next cent), and `economics(0.7, 0.6, 1)` has EV about $0.08 and break-even about 0.62.

`Risk` defaults, matching the sidebar:

| Limit | Default |
|---|---|
| Maximum spend per entry | $10 |
| Maximum cash per entry | 2% |
| Minimum EV per contract | $0.03 |
| Maximum spread | $0.10 |
| Minimum displayed depth | 10 contracts |
| Maximum entries per interval | 1 |
| Entry cutoff | 90 seconds remaining |

Entries also require a fresh book (this cycle, within `MAX_DATA_AGE`) and a forecast. Final-minute forecasts stay off, so the ledger does not trade that minute on Coinbase.

## Separate database

Use a file that has never held paper rows. Leave any already-running ledger process and its `DATABASE_PATH` alone. This repository does not migrate, reset, or reinterpret that file. A path such as `/workspace/kalshi-paper/runtime.sqlite` is an operator machine path, not a database in this workspace, and it is not a deployment target.

```bash
mkdir -p "$HOME/kalshi-paper-taker"
# DATABASE_PATH must not be the legacy file.
DATABASE_PATH="$HOME/kalshi-paper-taker/runtime.sqlite" \
STARTING_BANKROLL=1000 \
streamlit run app.py
```

No API keys. Do not set `MODEL_PATH` to a pickle you did not train. With the file missing, the forecast version is `statistical-proxy-v1`.

## Kickoff checklist

Record the baseline **before** arming fills and **before** enabling either switch.

- [ ] New sqlite path, process not sharing the legacy database.
- [ ] Sidebar shows execution `taker-confirm-v1`, fee schedule `kalshi-general-table-cent-2026-07-07`, baseline cash $1000.00, open positions 0, resolved 0, fills disarmed.
- [ ] SQL below matches that screen. If it does not, stop. Do not click Arm.
- [ ] Click **Arm taker fills** only after the baseline is written down.
- [ ] Then enable **Enable paper simulation** and **Allow unvalidated proxy forecasts**.
- [ ] Confirm the final minute shows no forecast, and that an intent row appears before any position row.
- [ ] A position's `confirm_book_at` is strictly after the intent's `signal_book_at`.

```sql
SELECT execution_model, fee_schedule, model_version, fills_armed, armed_at,
       baseline_cash, baseline_open_positions, baseline_open_cost,
       baseline_resolved_positions, baseline_recorded_at, database_path, provenance
FROM run_manifest WHERE id=1;
SELECT initial, cash FROM account WHERE id=1;
SELECT COUNT(*) AS open_positions, COALESCE(SUM(cost),0) AS open_cost
  FROM positions WHERE payout IS NULL;
SELECT COUNT(*) AS resolved_positions FROM positions WHERE payout IS NOT NULL;
```

Expected before Arm: `fills_armed=0`, cash `1000`, open `0`, resolved `0`. `entry()` in `btc15/paper.py` is the old same-snapshot helper. The engine does not call it, and it refuses to write once a manifest exists.

## Baseline on a new database

Record this before any resolved contract. Expected starting state:

| Field | Expected at start |
|---|---|
| Starting / virtual cash | $1000.00 |
| Open positions | none (count 0, cost $0) |
| Equity at cost | $1000.00 |
| Realized return | 0% |
| Resolved position history | none |
| Resolved forecast rows | none |
| Win rate | not defined (no resolved hypothetical fills) |
| Calibration (Brier, log loss, ECE) | not defined until official labels exist |

Copy from the Paper portfolio and Observed performance tabs, or from SQLite:

```sql
SELECT initial, cash FROM account WHERE id=1;
SELECT COUNT(*) AS open_positions, COALESCE(SUM(cost),0) AS open_cost
  FROM positions WHERE payout IS NULL;
SELECT COUNT(*) AS resolved_positions FROM positions WHERE payout IS NOT NULL;
SELECT COUNT(*) AS resolved_forecasts FROM markets WHERE result IS NOT NULL;
```

## 24-hour check

Use the same new database. Do not reset cash between the baseline and the check. Compare fills with official Kalshi results only. A higher virtual equity is not evidence of an edge. One day is a small sample, the proxy is uncalibrated, and the fill win rate is conditional on spread, depth, edge, and cutoff. Forecast calibration stays on the Observed performance tab. The execution model does not include a queue.

Fill this after about 24 hours of uptime (downtime means missed intervals, not zero outcomes):

| Metric | At start (UTC) | At +24h (UTC) |
|---|---|---|
| Clock time (UTC) |  |  |
| Database path |  |  |
| Model version | statistical-proxy-v1 or artifact version |  |
| Virtual cash | 1000.00 if new DB |  |
| Open positions (count / cost) | 0 / 0 |  |
| Equity at cost (cash + open cost) | 1000.00 if new DB |  |
| Resolved hypothetical fills (count) | 0 |  |
| Win rate on those fills (payout > 0) | n/a |  |
| Realized return | 0% |  |
| Max realized-equity drawdown | n/a |  |
| Distinct resolved contracts with a stored forecast | 0 |  |
| Contract-level accuracy / Brier / log loss / ECE | n/a |  |

Resolved-contract count and calibration come from the Observed performance tab (official `yes`/`no` only). Win rate and equity come from the Paper portfolio tab and count only hypothetical fills this ledger actually opened. Those two sets are not the same: many resolved contracts will have no paper fill if spread, depth, EV, cutoff, or the off switches blocked entry.

If the process was down, write the gap in the notes. Do not backfill labels from Coinbase.
