"""Describe recorded paper fills. This does not retune thresholds or rewrite a ledger.

A fill is compared with a depth snapshot only when that snapshot is for the
same ticker, two-sided, and received after the decision and at or before the
confirm time. Otherwise the evidence is missing. Receive time is not an
exchange event time, and a matching ask is not a queue or a print.
"""
from decimal import Decimal
import json
from pathlib import Path
from btc15.domain import utc
from btc15.execution import D, taker_charges

def _best(levels):
    chosen = None
    for price, size in levels or []:
        px, qty = Decimal(price), Decimal(size)
        if qty <= 0:
            continue
        if chosen is None or px > chosen[0]:
            chosen = (px, qty, price, size)
    return chosen

def executable(ladders, side):
    if not isinstance(ladders, dict):
        return None
    yes, no = _best(ladders.get('yes_dollars')), _best(ladders.get('no_dollars'))
    if yes is None or no is None:
        return None
    if side == 'yes':
        return {'ask': Decimal('1') - no[0], 'ask_size': no[1], 'bid': yes[0], 'raw_ask_size': no[3]}
    if side == 'no':
        return {'ask': Decimal('1') - yes[0], 'ask_size': yes[1], 'bid': no[0], 'raw_ask_size': yes[3]}
    return None

def _edge(probability, price, quantity, multiplier):
    charges = taker_charges(quantity, price, multiplier)
    qty = D(quantity)
    return {'debit': charges['debit'], 'net_fee': charges['net_fee'], 'edge': D(probability) - (charges['debit'] / qty)}

def _covering(snapshots, ticker, decision, confirm):
    chosen = None
    counts = {'rows': 0, 'two_sided': 0, 'one_sided': 0, 'empty': 0, 'missing_or_error': 0}
    for row in snapshots:
        if row.get('event') not in (None, 'snapshot'):
            continue
        if row.get('ticker') != ticker:
            continue
        received = row.get('response_received_at')
        if not received:
            continue
        moment = utc(received)
        if not (decision < moment <= confirm):
            continue
        counts['rows'] += 1
        flags = row.get('flags') or {}
        if flags.get('one_sided'):
            counts['one_sided'] += 1
        if flags.get('empty'):
            counts['empty'] += 1
        if flags.get('missing') or flags.get('error') or flags.get('malformed') or not row.get('orderbook_fp'):
            counts['missing_or_error'] += 1
            continue
        book = executable(row.get('orderbook_fp'), 'yes')
        if book is None:
            counts['one_sided'] += 1
            continue
        counts['two_sided'] += 1
        chosen = row
    return chosen, counts

def attribute(fills, intents, snapshots):
    """Return a report. Inputs are not modified and no sqlite file is opened."""
    by_id = {row.get('id'): row for row in intents}
    trades = []
    for fill in fills:
        intent = by_id.get(fill.get('intent_id'))
        item = {
            'position_id': fill.get('id'),
            'intent_id': fill.get('intent_id'),
            'ticker': fill.get('ticker'),
            'side': fill.get('side'),
            'quantity': fill.get('quantity'),
            'fill_price': fill.get('price'),
            'fee': fill.get('fee'),
            'cost': fill.get('cost'),
            'payout': fill.get('payout'),
            'execution_model': fill.get('execution_model'),
            'evidence': 'missing',
            'replay': None,
        }
        if intent is None:
            item['missing'] = ['intent']
            trades.append(item)
            continue
        decision, confirm = utc(intent['decision_at']), utc(fill.get('confirm_book_at') or fill.get('timestamp'))
        probability = D(intent['probability'])
        multiplier = D(intent['fee_multiplier'])
        quantity = D(fill['quantity'])
        item['probability'] = format(probability, 'f')
        item['decision_at'] = intent['decision_at']
        item['confirm_book_at'] = fill.get('confirm_book_at')
        item['seconds_decision_to_confirm'] = (confirm - decision).total_seconds()
        item['limit_price'] = intent['limit_price']
        item['edge_at_limit'] = {key: format(value, 'f') for key, value in _edge(probability, intent['limit_price'], quantity, multiplier).items()}
        item['edge_at_fill'] = {key: format(value, 'f') for key, value in _edge(probability, fill['price'], quantity, multiplier).items()}
        snapshot, counts = _covering(snapshots, fill.get('ticker'), decision, confirm)
        item['depth_counts'] = counts
        if snapshot is None:
            item['evidence'] = 'missing'
            item['replay_note'] = 'No two-sided recorded book for this ticker was received after the decision and at or before the confirm time.'
        else:
            touch = executable(snapshot['orderbook_fp'], fill.get('side'))
            difference = D(fill['price']) - touch['ask']
            item['evidence'] = 'recorded_book_exact' if difference == 0 else 'recorded_book_differs'
            item['replay'] = {
                'response_received_at': snapshot.get('response_received_at'),
                'local_receive_time_only': True,
                'same_http_response_as_fill': False,
                'executable_ask': format(touch['ask'], 'f'),
                'displayed_size': format(touch['ask_size'], 'f'),
                'fill_minus_ask': format(difference, 'f'),
                'queue_or_print': False,
            }
        trades.append(item)
    expired = [{'id': row.get('id'), 'ticker': row.get('ticker'), 'reason': row.get('reason'), 'status': row.get('status')} for row in intents if row.get('status') == 'expired']
    return {
        'schema': 'trade-attribution-v1',
        'execution_model': 'taker-confirm-v1',
        'optimizes_rules': False,
        'forecast_sample': 'not this report; fill results are a filtered subset',
        'fills': trades,
        'expired_attempts': expired,
        'evidence_counts': {
            'recorded_book_exact': sum(row['evidence'] == 'recorded_book_exact' for row in trades),
            'recorded_book_differs': sum(row['evidence'] == 'recorded_book_differs' for row in trades),
            'missing': sum(row['evidence'] == 'missing' for row in trades),
        },
    }

def from_sqlite_readonly(database, depth_dir):
    """Read a ledger copy and depth segments. The connection is mode=ro."""
    import sqlite3, gzip
    path = Path(database).resolve()
    uri = f'file:{path}?mode=ro'
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    try:
        fills = [dict(row) for row in db.execute('SELECT * FROM positions ORDER BY id')]
        intents = [dict(row) for row in db.execute('SELECT * FROM intents ORDER BY id')]
    finally:
        db.close()
    snapshots = []
    for file in sorted(Path(depth_dir).glob('segment-*.jsonl*')):
        opener = gzip.open if file.name.endswith('.gz') else open
        with opener(file, 'rt', encoding='utf-8') as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    snapshots.append(json.loads(text))
                except json.JSONDecodeError:
                    continue
    return attribute(fills, intents, snapshots)

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Attribute paper fills against recorded books. Read-only.')
    parser.add_argument('--sqlite', required=True)
    parser.add_argument('--depth', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    report = from_sqlite_readonly(args.sqlite, args.depth)
    Path(args.output).write_text(json.dumps(report, indent=2))
    print(json.dumps({'fills': len(report['fills']), 'evidence': report['evidence_counts']}))
