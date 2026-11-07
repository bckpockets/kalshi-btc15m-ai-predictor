"""Horizon, silence, and fill identity. No threshold is fit to a sample."""
from datetime import datetime, timedelta, timezone
from btc15.execution import taker_charges
from research.audit_runtime import (
    cash_identity, classify_silence, cohort_name, from_sqlite_readonly, horizon_summary,
    price_relation, reconcile_fill, traded_subset,
)

AT = datetime(2026, 11, 6, 14, 1, 47, tzinfo=timezone.utc)

def test_near_cutoff_accuracy_is_not_the_earlier_slice():
    rows = [{'remaining': 61, 'probability': 0.9, 'label': 1} for _ in range(4)]
    rows.append({'remaining': 520, 'probability': 0.9, 'label': 0})
    report = horizon_summary(rows)
    assert report['optimizes_rules'] is False
    assert report['near_cutoff_remaining_le_120s']['n'] == 4
    assert report['near_cutoff_remaining_le_120s']['accuracy'] == 1
    assert report['earlier_remaining_gt_120s']['accuracy'] == 0
    assert report['all_last']['accuracy'] != report['earlier_remaining_gt_120s']['accuracy']

def test_long_same_ticker_gap_is_silence_and_a_short_gap_is_not():
    events = [
        {'at': AT, 'ticker': 'KXBTC15M-26NOV060915-15'},
        {'at': AT + timedelta(seconds=60), 'ticker': 'KXBTC15M-26NOV060915-15'},
        {'at': AT + timedelta(minutes=424), 'ticker': 'KXBTC15M-26NOV061615-15'},
        {'at': AT + timedelta(minutes=424, seconds=3), 'ticker': 'KXBTC15M-26NOV060030-30'},
        {'at': AT + timedelta(minutes=825), 'ticker': 'KXBTC15M-26NOV060030-30'},
    ]
    holes = classify_silence(events)
    assert [row['kind'] for row in holes] == ['cross_contract_silence', 'same_contract_silence']
    assert all(row['inactive_series'] is False for row in holes)
    assert holes[1]['seconds'] > 400 * 60

def test_fill_reconcile_accepts_the_cent_schedule_and_rejects_a_fast_confirm():
    charges = taker_charges('2.00', '0.40', '1')
    fill = {
        'quantity': '2.00', 'price': '0.40', 'fee': format(charges['net_fee'], 'f'),
        'cost': format(charges['debit'], 'f'),
        'decision_at': AT.isoformat(), 'confirm_book_at': (AT + timedelta(seconds=3)).isoformat(),
    }
    assert reconcile_fill(fill)['ok'] is True
    fill['confirm_book_at'] = (AT + timedelta(seconds=2)).isoformat()
    assert 'shorter_than_3s' in reconcile_fill(fill)['reasons']
    assert cash_identity(100, '81.42', ['18.58'], ['0'])['ok'] is True
    subset = traded_subset([
        {'probability': '0.40', 'quantity': '2', 'cost': '1', 'payout': '0'},
        {'probability': '0.80', 'quantity': '2', 'cost': '1', 'payout': '2'},
    ])
    assert subset['optimizes_rules'] is False
    assert subset['side_probability_below_half'] == 1
    assert subset['payout_positive'] == 1

def test_float_dust_is_not_a_different_book_and_cohorts_follow_the_clocks():
    assert price_relation('0.16', 0.16000000000000003) == 'float_dust'
    assert price_relation('0.48', '0.47') == 'differs'
    assert price_relation('0.48', None) == 'absent'
    stop, deploy = '2026-11-06T21:06:50+00:00', '2026-11-07T00:25:29+00:00'
    assert cohort_name('2026-11-06T12:00:00+00:00', stop, deploy) == 'before_recorded_stop'
    assert cohort_name('2026-11-06T22:00:00+00:00', stop, deploy) == 'resume_before_deploy'
    assert cohort_name('2026-11-07T00:25:29+00:00', stop, deploy) == 'after_deploy'

def test_paper_quote_at_confirm_time_is_the_fill_book(tmp_path):
    from btc15.paper import Risk
    from tests.test_execution import AT as WHEN
    from tests.test_execution import armed, mkt, touch
    store = armed(tmp_path, 'provenance.sqlite')
    book = touch()
    from btc15.execution import confirm, propose
    opened = propose(store, mkt(), .8, book, WHEN, WHEN, 600, Risk(), 1, True, True, 10)
    later = WHEN + timedelta(seconds=3)
    filled = confirm(store, store.get_intent(opened['intent_id']), mkt(), book, later, later, Risk(), 10, 3, 20, True)
    assert filled['ok']
    store.quote(mkt().ticker, WHEN.isoformat(), book, 1, WHEN.isoformat())
    store.quote(mkt().ticker, later.isoformat(), book, 1, later.isoformat())
    store.db.close()
    report = from_sqlite_readonly(tmp_path / 'provenance.sqlite')
    assert report['writes'] is False
    assert report['paper_confirm_quotes']['differs'] == 0
    assert report['paper_confirm_quotes']['absent'] == 0
    assert report['paper_confirm_quotes']['exact'] + report['paper_confirm_quotes']['float_dust'] == 1
    assert report['fills_ok'] == 1
