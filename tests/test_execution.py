"""Deterministic taker-confirm cases. No network and no claim of a queue fill."""
from datetime import timedelta
from decimal import Decimal
import sqlite3
from btc15.execution import (
    EXECUTION_MODEL, FEE_SCHEDULE, book_top, confirm, liquidation, propose, step_execution, taker_charges,
)
from btc15.paper import entry, Risk
from btc15.store import Store
from tests.test_core import AT, market

RANGES = [
    {'start':'0.0000','end':'0.1000','step':'0.0010'},
    {'start':'0.1000','end':'0.9000','step':'0.0100'},
    {'start':'0.9000','end':'1.0000','step':'0.0010'},
]

def mkt(**kwargs):
    return market(price_ranges=RANGES, price_level_structure='tapered_deci_cent', **kwargs)

def touch(yes_bid=.40, yes_sz=100, no_bid=.58, no_sz=100):
    return {'yes':[(yes_bid, yes_sz)], 'no':[(no_bid, no_sz)]}

def armed(tmp_path, name='taker.sqlite'):
    store = Store(str(tmp_path/name))
    status = store.start_run({'model_version':'statistical-proxy-v1'})
    assert status['ok'] and status['fills_armed'] is False
    assert status['baseline_cash'] == 1000
    assert status['baseline_open_positions'] == 0
    assert status['baseline_resolved_positions'] == 0
    assert store.arm_fills()['reason'] == 'armed'
    return store

def cash(store):
    return store.frame('select cash from account').cash[0]

def test_published_fee_table_is_cent_debit_not_raw_model():
    # General Trading Fees Table, effective July 7, 2026. Fee column, not the unrounded 0.07*C*P*(1-P).
    table = [
        (Decimal('0.01'), 1, Decimal('0.01')),
        (Decimal('0.20'), 1, Decimal('0.02')),
        (Decimal('0.50'), 1, Decimal('0.02')),
        (Decimal('0.99'), 1, Decimal('0.01')),
        (Decimal('0.01'), 100, Decimal('0.07')),
        (Decimal('0.05'), 100, Decimal('0.34')),
        (Decimal('0.40'), 100, Decimal('1.68')),
        (Decimal('0.50'), 100, Decimal('1.75')),
    ]
    for price, qty, fee in table:
        got = taker_charges(qty, price, 1)
        assert got['net_fee'] == fee
        assert got['debit'] == price * qty + fee
    # Centicent rounding would have left the $0.50 one-contract fee at $0.0175. The table does not.
    assert taker_charges(1, Decimal('0.50'), 1)['trade_fee'] == Decimal('0.0175')
    assert taker_charges(1, Decimal('0.50'), 1)['net_fee'] == Decimal('0.02')

def test_complement_ask_matches_public_orderbook_note():
    # docs.kalshi.com orderbook example: best YES bid 0.42, best NO bid 0.56, YES ask 0.44.
    book = {'yes':[(0.42, 13), (0.41, 10)], 'no':[(0.56, 17), (0.45, 20)]}
    top = book_top(book, RANGES)
    assert top['yes_bid'] == Decimal('0.42')
    assert top['yes_ask'] == Decimal('0.44')
    assert top['yes_ask_size'] == Decimal('17')
    assert top['no_ask'] == Decimal('0.58')
    assert book_top({'yes':[(0.40, 10)], 'no':[(0.581, 10)]}, RANGES) is None
    assert book_top({'yes':[(0.40, 10)]}, RANGES) is None
    milli = book_top({'yes':[(0.003, 20)], 'no':[(0.997, 20)]}, RANGES)
    assert milli['yes_ask'] == Decimal('0.003')

def test_disarmed_until_baseline_is_armed(tmp_path):
    store = Store(str(tmp_path/'new.sqlite'))
    store.start_run({'model_version':'statistical-proxy-v1'})
    assert propose(store, mkt(), .8, touch(), AT, AT, 600, Risk(), 1, True, True, 10)['reason'] == 'disarmed'
    assert store.open_intents() == []
    # The optimistic same-snapshot writer must not append onto a versioned ledger.
    assert entry(store, mkt(), .8, touch(), AT, 600, Risk(), 1, True, True) is None
    assert cash(store) == 1000
    assert store.position_count(mkt().ticker) == 0

def test_legacy_file_is_not_migrated(tmp_path):
    path = tmp_path/'legacy.sqlite'
    db = sqlite3.connect(path)
    db.execute('CREATE TABLE positions(id INTEGER PRIMARY KEY, ticker TEXT, timestamp TEXT, side TEXT, quantity INTEGER, price REAL, fee REAL, cost REAL, payout REAL, resolved_at TEXT)')
    db.execute('CREATE TABLE account(id INTEGER PRIMARY KEY CHECK(id=1), initial REAL, cash REAL)')
    db.execute('INSERT INTO account VALUES(1,1000,1038.34)')
    db.execute("INSERT INTO positions VALUES(1,'OLD','t','yes',10,0.4,0.02,4.02,10,'t')")
    db.commit(); db.close()
    store = Store(str(path))
    assert store.legacy_readonly
    names = {row[0] for row in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert 'run_manifest' not in names and 'predictions' not in names and 'intents' not in names
    assert store.start_run({'model_version':'statistical-proxy-v1'})['mode'] == 'legacy'
    assert store.settle('OLD', 0, 'later') == 'legacy_readonly'
    assert store.db.execute('SELECT cash FROM account').fetchone()[0] == 1038.34
    assert store.db.execute('SELECT payout FROM positions').fetchone()[0] == 10
    assert propose(store, mkt(), .8, touch(), AT, AT, 600, Risk(), 1, True, True, 10)['reason'] == 'legacy_readonly'

def test_same_snapshot_cannot_fill_and_later_book_can(tmp_path):
    store = armed(tmp_path)
    opened = propose(store, mkt(), .8, touch(), AT, AT, 600, Risk(), 1, True, True, 10)
    assert opened['ok'] and opened['side'] == 'yes' and opened['limit_price'] == Decimal('0.42')
    assert cash(store) == 1000
    intent = store.get_intent(opened['intent_id'])
    same = confirm(store, intent, mkt(), touch(), AT, AT, Risk(), 10, 3, 20, True)
    assert same['reason'] == 'same_snapshot'
    assert store.position_count(mkt().ticker) == 0
    early = confirm(store, intent, mkt(), touch(), AT+timedelta(seconds=2), AT+timedelta(seconds=2), Risk(), 10, 3, 20, True)
    assert early['reason'] == 'too_soon'
    filled = confirm(store, intent, mkt(), touch(), AT+timedelta(seconds=3), AT+timedelta(seconds=3), Risk(), 10, 3, 20, True)
    assert filled['ok'] and filled['reason'] == 'filled'
    assert filled['price'] == Decimal('0.42')
    again = confirm(store, intent, mkt(), touch(), AT+timedelta(seconds=6), AT+timedelta(seconds=6), Risk(), 10, 3, 20, True)
    assert again['reason'] == 'not_open'
    assert store.position_count(mkt().ticker) == 1
    row = store.frame('select * from positions').iloc[0]
    assert row.execution_model == EXECUTION_MODEL
    assert store.get_intent(opened['intent_id'])['signal_book_at'] < row.confirm_book_at
    assert cash(store) == 1000 - float(filled['debit'])
    assert propose(store, mkt(), .9, touch(), AT+timedelta(seconds=6), AT+timedelta(seconds=6), 500, Risk(), 1, True, True, 10)['reason'] == 'max_trades'

def test_adverse_confirmation_expires_and_does_not_rest(tmp_path):
    store = armed(tmp_path, 'adverse.sqlite')
    opened = propose(store, mkt(), .8, touch(), AT, AT, 600, Risk(), 1, True, True, 10)
    later = AT+timedelta(seconds=3)
    missed = confirm(store, store.get_intent(opened['intent_id']), mkt(), touch(no_bid=.50), later, later, Risk(), 10, 3, 20, True)
    assert missed['reason'] == 'price_through_limit'
    assert store.get_intent(opened['intent_id'])['status'] == 'expired'
    assert cash(store) == 1000
    better = confirm(store, store.get_intent(opened['intent_id']), mkt(), touch(), later+timedelta(seconds=2), later+timedelta(seconds=2), Risk(), 10, 3, 20, True)
    assert better['reason'] == 'not_open'
    assert store.position_count(mkt().ticker) == 0

def test_partial_size_uses_only_the_later_touch(tmp_path):
    store = armed(tmp_path, 'partial.sqlite')
    opened = propose(store, mkt(), .8, touch(no_sz=100), AT, AT, 600, Risk(), 1, True, True, 10)
    assert opened['quantity'] > Decimal('12.5')
    when = AT+timedelta(seconds=3)
    filled = confirm(store, store.get_intent(opened['intent_id']), mkt(), touch(no_sz=12.5), when, when, Risk(), 10, 3, 20, True)
    assert filled['ok'] and filled['quantity'] == Decimal('12.50')
    assert store.frame('select quantity from positions').quantity[0] == 12.5

def test_crossed_stale_future_and_final_minute(tmp_path):
    store = armed(tmp_path, 'guards.sqlite')
    assert propose(store, mkt(), .8, touch(), AT, AT-timedelta(seconds=11), 600, Risk(), 1, True, True, 10)['reason'] == 'stale_book'
    assert propose(store, mkt(), .8, {'yes':[(.4,100)]}, AT, AT, 600, Risk(), 1, True, True, 10)['reason'] == 'unusable_book'
    assert propose(store, mkt(rules_primary='price rises'), .8, touch(), AT, AT, 600, Risk(), 1, True, True, 10)['reason'] == 'unsupported_market'
    assert propose(store, mkt(), .8, touch(), AT, AT, 30, Risk(), 1, True, True, 10)['reason'] == 'final_minute'
    assert propose(store, mkt(), .8, touch(), AT, AT, 80, Risk(), 1, True, True, 10)['reason'] == 'cutoff'
    assert propose(store, mkt(), .8, touch(), AT, AT, 600, Risk(), 1, False, True, 10)['reason'] == 'simulation_off'
    assert propose(store, mkt(), .8, touch(), AT, AT, 600, Risk(), None, True, True, 10)['reason'] == 'unsupported_fee'
    opened = propose(store, mkt(), .8, touch(), AT, AT, 600, Risk(), 1, True, True, 10)
    intent = store.get_intent(opened['intent_id'])
    future = AT+timedelta(seconds=3)
    assert confirm(store, intent, mkt(), touch(), future+timedelta(seconds=5), future, Risk(), 10, 3, 20, True)['reason'] == 'future_book'
    assert store.get_intent(opened['intent_id'])['status'] == 'open'
    crossed = confirm(store, intent, mkt(), touch(yes_bid=.70, no_bid=.40), future, future, Risk(), 10, 3, 20, True)
    assert crossed['reason'] == 'crossed'
    assert cash(store) == 1000

def test_restart_keeps_baseline_and_does_not_double_debit(tmp_path):
    path = tmp_path/'restart.sqlite'
    store = Store(str(path))
    store.start_run({'model_version':'statistical-proxy-v1'})
    store.arm_fills()
    opened = propose(store, mkt(), .8, touch(), AT, AT, 600, Risk(), 1, True, True, 10)
    store.db.close()
    revived = Store(str(path))
    again = revived.start_run({'model_version':'statistical-proxy-v1'})
    assert again['created'] is False and again['fills_armed'] is True
    assert again['baseline_cash'] == 1000
    when = AT+timedelta(seconds=3)
    filled = confirm(revived, revived.get_intent(opened['intent_id']), mkt(), touch(), when, when, Risk(), 10, 3, 20, True)
    assert filled['ok']
    paid = cash(revived)
    confirm(revived, revived.get_intent(opened['intent_id']), mkt(), touch(), when, when, Risk(), 10, 3, 20, True)
    assert cash(revived) == paid
    kept = revived.start_run({'model_version':'other'})
    assert kept['baseline_cash'] == 1000
    assert kept['baseline_cash'] != paid

def test_version_mismatch_and_settlement_revision(tmp_path):
    store = armed(tmp_path, 'revise.sqlite')
    store.db.execute("UPDATE run_manifest SET execution_model='optimistic-top-v1'")
    store.db.commit()
    assert store.arm_fills()['reason'] == 'version_mismatch'
    assert propose(store, mkt(), .8, touch(), AT, AT, 600, Risk(), 1, True, True, 10)['reason'] == 'disarmed'
    store.db.execute("UPDATE run_manifest SET execution_model=?", (EXECUTION_MODEL,))
    store.db.commit()
    opened = propose(store, mkt(), .8, touch(), AT, AT, 600, Risk(), 1, True, True, 10)
    when = AT+timedelta(seconds=3)
    assert confirm(store, store.get_intent(opened['intent_id']), mkt(), touch(), when, when, Risk(), 10, 3, 20, True)['ok']
    assert store.settle(mkt().ticker, 1, when.isoformat()) == 'settled'
    after = cash(store)
    payout = store.frame('select payout from positions').payout[0]
    assert store.settle(mkt().ticker, 0, (when+timedelta(seconds=1)).isoformat()) == 'conflict'
    assert cash(store) == after
    assert store.frame('select payout from positions').payout[0] == payout
    assert len(store.frame('select * from settlement_conflicts')) == 1
    assert store.settle(mkt().ticker, 1, when.isoformat()) == 'idempotent'
    assert cash(store) == after

def test_cash_is_reserved_before_a_fill_and_not_spent_twice(tmp_path):
    store = armed(tmp_path, 'reserve.sqlite')
    first = propose(store, mkt(), .8, touch(), AT, AT, 600, Risk(), 1, True, True, 10)
    assert cash(store) == 1000
    assert store.available_cash() < 1000
    other = mkt(ticker='KXBTC15M-OTHER')
    second = propose(store, other, .8, touch(), AT, AT, 600, Risk(stake=1000, fraction=1), 1, True, True, 10)
    assert second['ok']
    assert first['reserve'] + second['reserve'] <= Decimal('1000')
    assert cash(store) == 1000

def test_liquidation_is_bid_minus_taker_fee_or_unavailable():
    mark = liquidation('yes', 10, touch(yes_bid=.40, yes_sz=100), RANGES, 1)
    charges = taker_charges(10, Decimal('0.40'), 1)
    assert mark == charges['premium'] - charges['net_fee']
    assert mark < Decimal('4')
    assert liquidation('yes', 10, {'yes':[(.4, 100)]}, RANGES, 1) is None
    assert liquidation('yes', 1000, touch(), RANGES, 1) is None

def test_one_cycle_records_an_intent_and_not_a_fill(tmp_path):
    store = armed(tmp_path, 'step.sqlite')
    out = step_execution(store, mkt(), .8, touch(), AT, AT, Risk(), 1, True, True, 10, 3, 20, True)
    assert out['proposed']['ok']
    assert out['confirmed'] is None
    assert store.position_count(mkt().ticker) == 0
    assert cash(store) == 1000

def test_manifest_records_fee_schedule_constant():
    assert FEE_SCHEDULE == 'kalshi-general-table-cent-2026-07-07'
    assert EXECUTION_MODEL == 'taker-confirm-v1'
