"""Attribution fixtures. They are not the November ledger and do not change rules."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from research.attribute import attribute

AT = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)

def snap(ticker, at, yes, no, **flags):
    base = {'empty': False, 'one_sided': False, 'malformed': False, 'missing': False, 'error': False}
    base.update(flags)
    return {'schema': 'depth-snapshot-v1', 'event': 'snapshot', 'ticker': ticker, 'response_received_at': at.isoformat(),
            'flags': base, 'orderbook_fp': {'yes_dollars': yes, 'no_dollars': no}}

def test_exact_book_is_reported_and_inputs_stay_put():
    decision, confirm = AT, AT + timedelta(seconds=3)
    intent = {'id': 7, 'ticker': 'KXBTC15M-A', 'status': 'filled', 'decision_at': decision.isoformat(),
              'probability': '0.80', 'fee_multiplier': '1', 'limit_price': '0.42', 'side': 'yes'}
    fill = {'id': 1, 'intent_id': 7, 'ticker': 'KXBTC15M-A', 'side': 'yes', 'quantity': '10', 'price': '0.42',
            'fee': '0.18', 'cost': '4.38', 'payout': None, 'execution_model': 'taker-confirm-v1',
            'confirm_book_at': confirm.isoformat(), 'timestamp': confirm.isoformat()}
    book = snap('KXBTC15M-A', confirm, [['0.4000', '20.00']], [['0.5800', '15.00']])
    before = json.dumps(fill)
    report = attribute([fill], [intent], [book])
    assert fill['price'] == '0.42' and json.dumps(fill) == before
    row = report['fills'][0]
    assert row['evidence'] == 'recorded_book_exact'
    assert Decimal(row['replay']['executable_ask']) == Decimal('0.42')
    assert row['replay']['queue_or_print'] is False
    assert row['replay']['same_http_response_as_fill'] is False
    assert float(row['edge_at_fill']['edge']) > 0

def test_one_sided_or_absent_books_are_missing_evidence():
    decision, confirm = AT, AT + timedelta(seconds=3)
    intent = {'id': 1, 'ticker': 'KXBTC15M-A', 'status': 'filled', 'decision_at': decision.isoformat(),
              'probability': '0.40', 'fee_multiplier': '1', 'limit_price': '0.30', 'side': 'no'}
    fill = {'id': 1, 'intent_id': 1, 'ticker': 'KXBTC15M-A', 'side': 'no', 'quantity': '5', 'price': '0.30',
            'fee': '0.08', 'cost': '1.58', 'payout': 0, 'execution_model': 'taker-confirm-v1',
            'confirm_book_at': confirm.isoformat(), 'timestamp': confirm.isoformat()}
    one = snap('KXBTC15M-A', confirm, [['0.7000', '10.00']], [], one_sided=True)
    report = attribute([fill], [intent], [one])
    assert report['fills'][0]['evidence'] == 'missing' and report['fills'][0]['replay'] is None
    empty = attribute([fill], [intent], [])
    assert empty['evidence_counts']['missing'] == 1

def test_expired_attempts_are_not_rewritten_as_fills():
    intent = {'id': 3, 'ticker': 'KXBTC15M-A', 'status': 'expired', 'reason': 'price_through_limit',
              'decision_at': AT.isoformat(), 'probability': '0.9', 'fee_multiplier': '1', 'limit_price': '0.2', 'side': 'yes'}
    report = attribute([], [intent], [])
    assert report['fills'] == []
    assert report['expired_attempts'] == [{'id': 3, 'ticker': 'KXBTC15M-A', 'reason': 'price_through_limit', 'status': 'expired'}]
    assert report['optimizes_rules'] is False
