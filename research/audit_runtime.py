"""Read-only checks for paper fills, forecast horizon, and recording silence.

Nothing here retunes a threshold or writes a ledger. A last-forecast accuracy
number is not the traded subset. A long timestamp hole is silence in the
file, not a claim that the 15-minute series was closed.
"""
from decimal import Decimal
import json
import sqlite3
from pathlib import Path
from btc15.domain import utc
from btc15.execution import D, taker_charges
from research.evaluate import metrics

DUST = Decimal('0.000000001')

def price_relation(left, right):
    """Compare two stored prices. Float dust is not a different book."""
    if right is None:
        return 'absent'
    try:
        a, b = D(left), D(right)
    except (ArithmeticError, ValueError):
        return 'absent'
    if a == b:
        return 'exact'
    if abs(a - b) <= DUST:
        return 'float_dust'
    return 'differs'

def cohort_name(decision_at, stop_at, deploy_at):
    """Split a cumulative ledger. Times are the operator's clocks, not inferred."""
    moment = utc(decision_at)
    if moment < utc(stop_at):
        return 'before_recorded_stop'
    if moment < utc(deploy_at):
        return 'resume_before_deploy'
    return 'after_deploy'

def simple_score(probabilities, outcomes):
    if not probabilities:
        return {'n': 0, 'brier': None, 'accuracy': None, 'mean_probability': None, 'outcome_rate': None}
    pairs = [(float(p), int(y)) for p, y in zip(probabilities, outcomes)]
    n = len(pairs)
    return {
        'n': n,
        'brier': sum((p - y) ** 2 for p, y in pairs) / n,
        'accuracy': sum((p >= 0.5) == bool(y) for p, y in pairs) / n,
        'mean_probability': sum(p for p, _ in pairs) / n,
        'outcome_rate': sum(y for _, y in pairs) / n,
    }

def _rows(items):
    out = []
    for item in items:
        if isinstance(item, dict):
            out.append(item)
        else:
            out.append({'remaining': item.remaining, 'probability': item.probability, 'label': item.label})
    return out

def _score(rows):
    if not rows:
        return {'n': 0, 'accuracy': None, 'brier': None, 'median_remaining': None}
    remaining = sorted(float(row['remaining']) for row in rows)
    mid = remaining[len(remaining) // 2]
    scored = metrics([row['label'] for row in rows], [row['probability'] for row in rows])
    return {
        'n': scored['n'],
        'accuracy': scored['accuracy'],
        'brier': scored['brier'],
        'median_remaining': mid,
    }

def horizon_summary(rows):
    """Split contract rows by how long remained when the probability was stored.

    `near_cutoff` is remaining <= 120 seconds, which includes the last stored
    forecast just before the final minute is suspended. `earlier` is the rest.
    Neither slice is a fill.
    """
    rows = _rows(rows)
    near = [row for row in rows if float(row['remaining']) <= 120]
    earlier = [row for row in rows if float(row['remaining']) > 120]
    return {
        'schema': 'forecast-horizon-v1',
        'optimizes_rules': False,
        'all_last': _score(rows),
        'near_cutoff_remaining_le_120s': _score(near),
        'earlier_remaining_gt_120s': _score(earlier),
        'note': 'Last-forecast accuracy is not the accuracy of the contracts that were traded.',
    }

def classify_silence(events, minimum_seconds=120):
    """Classify gaps between ordered samples.

    Gaps at or below `minimum_seconds` match the final-minute suspension plus
    a short discovery pause, so they are not reported as holes. A longer gap
    with the same ticker is silence inside one contract. A longer gap with
    different tickers only says the file has no samples in between.
    """
    ordered = sorted(events, key=lambda row: utc(row['at']))
    holes = []
    for left, right in zip(ordered, ordered[1:]):
        start, end = utc(left['at']), utc(right['at'])
        seconds = (end - start).total_seconds()
        if seconds <= minimum_seconds:
            continue
        same = left.get('ticker') is not None and left.get('ticker') == right.get('ticker')
        holes.append({
            'kind': 'same_contract_silence' if same else 'cross_contract_silence',
            'seconds': seconds,
            'from': start.isoformat(),
            'to': end.isoformat(),
            'from_ticker': left.get('ticker'),
            'to_ticker': right.get('ticker'),
            'inactive_series': False,
        })
    return holes

def reconcile_fill(fill, multiplier='1'):
    """Check one stored fill against the cent fee schedule and the 3s rule.

    `fill` uses the ledger columns plus decision_at. Prices are not rewritten.
    """
    reasons = []
    try:
        charges = taker_charges(fill['quantity'], fill['price'], multiplier)
    except (ValueError, ArithmeticError) as exc:
        return {'ok': False, 'reasons': ['fee_inputs', str(exc)]}
    if abs(charges['debit'] - D(fill['cost'])) > Decimal('0.001'):
        reasons.append('cost')
    if abs(charges['net_fee'] - D(fill['fee'])) > Decimal('0.001'):
        reasons.append('fee')
    decision, confirm = utc(fill['decision_at']), utc(fill['confirm_book_at'])
    latency = (confirm - decision).total_seconds()
    if confirm <= decision:
        reasons.append('not_later_book')
    elif latency < 3:
        reasons.append('shorter_than_3s')
    return {
        'ok': not reasons,
        'reasons': reasons,
        'latency_seconds': latency,
        'debit': format(charges['debit'], 'f'),
        'net_fee': format(charges['net_fee'], 'f'),
    }

def cash_identity(initial, cash, costs, payouts):
    expected = D(initial) - sum((D(item) for item in costs), Decimal('0')) + sum((D(item) for item in payouts if item is not None), Decimal('0'))
    return {'ok': abs(expected - D(cash)) <= Decimal('0.001'), 'expected_cash': format(expected, 'f'), 'cash': format(D(cash), 'f')}

def traded_subset(fills):
    """Decision-time side probability versus settlement. Does not choose a new cutoff."""
    settled = [row for row in fills if row.get('payout') is not None]
    wins = sum(1 for row in settled if D(row['payout']) > 0)
    below = sum(1 for row in fills if D(row['probability']) < Decimal('0.5'))
    model_ev = sum((D(row['probability']) * D(row['quantity']) - D(row['cost']) for row in fills), Decimal('0'))
    realized = sum((D(row['payout']) - D(row['cost']) for row in settled), Decimal('0'))
    return {
        'schema': 'traded-subset-v1',
        'optimizes_rules': False,
        'fills': len(fills),
        'settled': len(settled),
        'payout_positive': wins,
        'side_probability_below_half': below,
        'model_ev': format(model_ev, 'f'),
        'realized': format(realized, 'f'),
        'note': 'Side probability below 0.5 can still clear a price edge. This sample does not set a new minimum.',
    }

def _connect(path):
    uri = Path(path).resolve().as_uri() + '?mode=ro'
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    return db

def _quote_ask(quote, side):
    if not quote:
        return None
    return quote['yes_ask'] if side == 'yes' else quote['no_ask']

def from_sqlite_readonly(database, stop_at='2026-11-06T21:06:50+00:00', deploy_at='2026-11-07T00:25:29.099953+00:00'):
    """Summarize one ledger file. The connection is mode=ro.

    A quote whose observed_at equals the intent's book time is the paper
    process's own touch. It is not a depth-recorder poll.
    """
    db = _connect(database)
    try:
        initial, cash = db.execute('SELECT initial, cash FROM account WHERE id=1').fetchone()
        positions = [dict(row) for row in db.execute('SELECT * FROM positions ORDER BY id')]
        intents = {row['id']: dict(row) for row in db.execute('SELECT * FROM intents')}
        conflicts = db.execute('SELECT COUNT(*) FROM settlement_conflicts').fetchone()[0]
        last = [dict(row) for row in db.execute('''
            SELECT p.ticker, p.probability, p.remaining, p.timestamp, m.result AS label
            FROM predictions p
            JOIN markets m USING(ticker)
            JOIN (SELECT ticker, MAX(timestamp) ts FROM predictions GROUP BY ticker) x
              ON p.ticker=x.ticker AND p.timestamp=x.ts
            WHERE m.result IS NOT NULL
        ''')]
        entry_population = [dict(row) for row in db.execute('''
            SELECT p.ticker, p.probability, p.remaining, m.result AS label
            FROM predictions p
            JOIN markets m USING(ticker)
            JOIN (
                SELECT ticker, MAX(timestamp) ts FROM predictions WHERE remaining >= 180 GROUP BY ticker
            ) x ON p.ticker=x.ticker AND p.timestamp=x.ts
            WHERE m.result IS NOT NULL
        ''')]
        quote_rows = [dict(row) for row in db.execute(
            'SELECT ticker, timestamp, yes_ask, no_ask, observed_at FROM quotes ORDER BY timestamp'
        )]
        decisions = {}
        if positions:
            for row in db.execute('''
                SELECT pos.id, pred.probability AS yes_p, pred.remaining, pred.timestamp AS pred_ts, m.result
                FROM positions pos
                JOIN intents i ON i.id=pos.intent_id
                LEFT JOIN markets m ON m.ticker=pos.ticker
                LEFT JOIN predictions pred ON pred.ticker=pos.ticker AND pred.timestamp=(
                    SELECT MAX(timestamp) FROM predictions WHERE ticker=pos.ticker AND timestamp<=i.decision_at
                )
            '''):
                decisions[row['id']] = dict(row)
    finally:
        db.close()
    by_observed = {}
    for row in quote_rows:
        if row.get('observed_at'):
            by_observed[(row['ticker'], row['observed_at'])] = row
    checks = []
    traded = []
    for row in positions:
        intent = intents.get(row.get('intent_id'))
        if intent is None:
            checks.append({'id': row.get('id'), 'ok': False, 'reasons': ['missing_intent']})
            continue
        item = {
            'quantity': row['quantity'], 'price': row['price'], 'fee': row['fee'], 'cost': row['cost'],
            'decision_at': intent['decision_at'], 'confirm_book_at': row.get('confirm_book_at') or row.get('timestamp'),
        }
        check = reconcile_fill(item)
        check['id'] = row['id']
        signal = by_observed.get((row['ticker'], intent.get('signal_book_at')))
        confirm = by_observed.get((row['ticker'], item['confirm_book_at']))
        decision = decisions.get(row['id']) or {}
        check['cohort'] = cohort_name(intent['decision_at'], stop_at, deploy_at)
        check['decision_remaining'] = decision.get('remaining')
        check['decision_yes_probability'] = decision.get('yes_p')
        check['paper_decision_quote'] = price_relation(intent.get('limit_price'), _quote_ask(signal, row.get('side')))
        check['paper_confirm_quote'] = price_relation(row.get('price'), _quote_ask(confirm, row.get('side')))
        check['paper_quote_is_depth_poll'] = False
        checks.append(check)
        traded.append({
            'probability': intent['probability'], 'quantity': row['quantity'], 'cost': row['cost'], 'payout': row['payout'],
            'cohort': check['cohort'],
        })
    identity = cash_identity(initial, cash, [row['cost'] for row in positions], [row['payout'] for row in positions])
    latest_quote = quote_rows[-1]['timestamp'] if quote_rows else None
    earliest_quote = quote_rows[0]['timestamp'] if quote_rows else None
    cohorts = {}
    for name in ('before_recorded_stop', 'resume_before_deploy', 'after_deploy'):
        subset = [row for row in traded if row['cohort'] == name]
        settled = [row for row in subset if row.get('payout') is not None]
        cohorts[name] = traded_subset(subset)
        cohorts[name]['settled_side'] = simple_score(
            [row['probability'] for row in settled],
            [1 if D(row['payout']) > 0 else 0 for row in settled],
        )
    return {
        'schema': 'runtime-audit-v1',
        'optimizes_rules': False,
        'writes': False,
        'source_span': {'first_quote': earliest_quote, 'last_quote': latest_quote},
        'cutoff_note': 'Figures use only rows in this file. A later verbal tally is not in the file.',
        'settlement_conflicts': conflicts,
        'cash_identity': identity,
        'fills_ok': sum(1 for row in checks if row.get('ok')),
        'fills_bad': [row for row in checks if not row.get('ok')],
        'horizon': horizon_summary(last),
        'silence': classify_silence([{'at': row['timestamp'], 'ticker': row['ticker']} for row in quote_rows]),
        'traded_subset': traded_subset(traded),
        'cohorts': cohorts,
        'entry_horizon_population': simple_score(
            [row['probability'] for row in entry_population],
            [row['label'] for row in entry_population],
        ),
        'paper_decision_quotes': {
            'exact': sum(row.get('paper_decision_quote') == 'exact' for row in checks),
            'float_dust': sum(row.get('paper_decision_quote') == 'float_dust' for row in checks),
            'differs': sum(row.get('paper_decision_quote') == 'differs' for row in checks),
            'absent': sum(row.get('paper_decision_quote') == 'absent' for row in checks),
            'note': 'Matched on quotes.observed_at equal to signal_book_at. Compared with the intent limit, which is the decision ask.',
        },
        'paper_confirm_quotes': {
            'exact': sum(row.get('paper_confirm_quote') == 'exact' for row in checks),
            'float_dust': sum(row.get('paper_confirm_quote') == 'float_dust' for row in checks),
            'differs': sum(row.get('paper_confirm_quote') == 'differs' for row in checks),
            'absent': sum(row.get('paper_confirm_quote') == 'absent' for row in checks),
            'note': 'Matched on quotes.observed_at equal to confirm_book_at. This is the paper process book, not a depth poll.',
        },
        'bounds': {'stop_at': stop_at, 'deploy_at': deploy_at},
    }

def attach_depth(report, database, depth_dir):
    """Add asynchronous depth labels. They do not replace the paper quote."""
    from research.attribute import from_sqlite_readonly as attribute_readonly
    attributed = attribute_readonly(database, depth_dir)
    by_id = {row['position_id']: row for row in attributed['fills']}
    linked = []
    for row in by_id.values():
        replay = row.get('replay') or {}
        linked.append({
            'position_id': row['position_id'],
            'depth_evidence': row['evidence'],
            'fill_minus_recorded_ask': replay.get('fill_minus_ask'),
            'depth_received_at': replay.get('response_received_at'),
            'same_http_response_as_fill': False,
            'depth_rows_in_window': (row.get('depth_counts') or {}).get('two_sided'),
        })
    report['depth_async'] = {
        'evidence_counts': attributed['evidence_counts'],
        'same_http_response_as_fill': False,
        'note': 'A differing ask is another poll in (decision, confirm]. It is not the paper process response.',
        'fills': linked,
    }
    return report

def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description='Read-only fill, horizon, and silence audit. Does not retune rules.')
    parser.add_argument('--sqlite', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--depth', help='Optional depth directory. Compared asynchronously. Not the paper book.')
    parser.add_argument('--stop-at', default='2026-11-06T21:06:50+00:00')
    parser.add_argument('--deploy-at', default='2026-11-07T00:25:29.099953+00:00')
    args = parser.parse_args(argv)
    report = from_sqlite_readonly(args.sqlite, stop_at=args.stop_at, deploy_at=args.deploy_at)
    if args.depth:
        attach_depth(report, args.sqlite, args.depth)
    Path(args.output).write_text(json.dumps(report, indent=2))
    print(json.dumps({
        'fills_ok': report['fills_ok'],
        'fills_bad': len(report['fills_bad']),
        'cash_ok': report['cash_identity']['ok'],
        'paper_confirm': report['paper_confirm_quotes'],
        'cohorts': {name: row['fills'] for name, row in report['cohorts'].items()},
        'silence': len(report['silence']),
        'horizon_n': report['horizon']['all_last']['n'],
    }))

if __name__ == '__main__':
    main()
