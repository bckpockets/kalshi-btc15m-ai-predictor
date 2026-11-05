"""Taker-confirm paper execution. No order submission.

A decision may only fill against a later order book. The same snapshot that
produced the signal is never a fill. There is no queue model: displayed size
on that later book is an upper bound, not evidence the contracts were ours.

Fee identity, checked against the public General Trading Fees Table
(effective July 7, 2026) and the fee-rounding note fetched 2026-11-05:

    model = 0.07 * multiplier * contracts * price * (1 - price)
    trade_fee = ceil to $0.000001
    debit = ceil to $0.01 of (premium + trade_fee)

The published one-contract table matches this cent debit (fee at $0.50 is
$0.02, not the unrounded $0.0175). The PDF prose also says "centicent".
Centicent alignment would charge less or the same. Member status is unknown,
so the ledger uses the cent table, which is the higher published rounding.
Maker fees are not used. Flat schedules are rejected.

Coinbase is not BRTI. This module does not price the final minute.
"""
from datetime import timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN
import json
from btc15.domain import utc

EXECUTION_MODEL = 'taker-confirm-v1'
FEE_SCHEDULE = 'kalshi-general-table-cent-2026-07-07'
TAKER_FEE_TYPES = frozenset({
    'quadratic',
    'quadratic_with_maker_fees',
    'quadratic_with_combo_maker_fees',
})
LOT = Decimal('0.01')
CENT = Decimal('0.01')
MICRO = Decimal('0.000001')

def D(value):
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))

def cents(value):
    return int((D(value).quantize(CENT, rounding=ROUND_HALF_EVEN) * 100).to_integral_value())

def taker_charges(quantity, price, multiplier):
    """Cent-rounded taker debit for an immediately matched order.

    `net_fee` is the debit minus premium, matching the July 7, 2026 table
    when price and size land on that table. Raises on inputs the formula
    does not define.
    """
    qty, px, mult = D(quantity), D(price), D(multiplier)
    if qty < LOT or not (Decimal('0') < px < Decimal('1')) or mult <= 0:
        raise ValueError('Invalid taker fee inputs')
    model = Decimal('0.07') * mult * qty * px * (1 - px)
    trade_fee = model.quantize(MICRO, rounding=ROUND_CEILING)
    premium = qty * px
    debit = (premium + trade_fee).quantize(CENT, rounding=ROUND_CEILING)
    return {
        'model_fee': model,
        'trade_fee': trade_fee,
        'premium': premium,
        'debit': debit,
        'net_fee': debit - premium,
    }

def parse_ranges(ranges):
    """Return (start, end, step) Decimal tuples, or None if the grid is unusable."""
    if not ranges:
        return None
    parsed = []
    try:
        for row in ranges:
            start, end, step = D(row['start']), D(row['end']), D(row['step'])
            if step <= 0 or end <= start:
                return None
            parsed.append((start, end, step))
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return None
    return parsed or None

def _range_for(price, ranges):
    for start, end, step in ranges:
        if start <= price <= end:
            return start, end, step
    return None

def on_tick(price, ranges):
    found = _range_for(price, ranges)
    if found is None:
        return False
    start, _end, step = found
    return (price - start) % step == 0

def round_up_tick(price, ranges):
    """Next valid price at or above `price`. None if the grid cannot express it."""
    if on_tick(price, ranges):
        return price
    found = _range_for(price, ranges)
    if found is None:
        return None
    start, end, step = found
    steps = ((price - start) / step).to_integral_value(rounding=ROUND_CEILING)
    rounded = start + steps * step
    if rounded > end or rounded <= 0 or rounded >= 1 or not on_tick(rounded, ranges):
        return None
    return rounded

def _side_levels(book, side):
    rows = []
    for price, qty in (book or {}).get(side) or []:
        try:
            px, q = D(price), D(qty)
        except (ArithmeticError, ValueError):
            return None
        if q <= 0 or not px.is_finite() or not q.is_finite():
            return None
        rows.append((px, q))
    return rows

def book_top(book, ranges):
    """Best bids and the complementary asks. None when the book cannot be lifted.

    A YES ask is 1 minus the best NO bid. A NO ask is 1 minus the best YES bid.
    Size at the touch is the sum of contracts at that exact price only.
    Off-tick bids, a missing side, or a non-positive complement are rejected.
    """
    grid = parse_ranges(ranges)
    if grid is None or not isinstance(book, dict):
        return None
    yes, no = _side_levels(book, 'yes'), _side_levels(book, 'no')
    if not yes or not no:
        return None
    yes_bid = max(px for px, _q in yes)
    no_bid = max(px for px, _q in no)
    if not on_tick(yes_bid, grid) or not on_tick(no_bid, grid):
        return None
    yes_ask, no_ask = Decimal('1') - no_bid, Decimal('1') - yes_bid
    if not (Decimal('0') < yes_ask < 1 and Decimal('0') < no_ask < 1):
        return None
    if not on_tick(yes_ask, grid) or not on_tick(no_ask, grid):
        lifted_yes, lifted_no = round_up_tick(yes_ask, grid), round_up_tick(no_ask, grid)
        if lifted_yes is None or lifted_no is None:
            return None
        yes_ask, no_ask = lifted_yes, lifted_no
    return {
        'yes_bid': yes_bid,
        'no_bid': no_bid,
        'yes_ask': yes_ask,
        'no_ask': no_ask,
        'yes_bid_size': sum((q for px, q in yes if px == yes_bid), Decimal('0')),
        'no_bid_size': sum((q for px, q in no if px == no_bid), Decimal('0')),
        'yes_ask_size': sum((q for px, q in no if px == no_bid), Decimal('0')),
        'no_ask_size': sum((q for px, q in yes if px == yes_bid), Decimal('0')),
    }

def _spread_ok(top, side, risk):
    bid = top['yes_bid'] if side == 'yes' else top['no_bid']
    ask = top['yes_ask'] if side == 'yes' else top['no_ask']
    if ask < bid:
        return 'crossed'
    if ask - bid > D(risk.max_spread):
        return 'spread'
    return None

def _fresh(book_at, now, max_age):
    book_at, now = utc(book_at), utc(now)
    if book_at > now:
        return 'future_book'
    if (now - book_at).total_seconds() > max_age:
        return 'stale_book'
    return None

def affordable(budget, price, multiplier, max_qty):
    budget, price, max_qty = D(budget), D(price), D(max_qty).quantize(LOT, rounding=ROUND_FLOOR)
    if budget <= 0 or price <= 0 or max_qty < LOT:
        return Decimal('0')
    guess = min(max_qty, (budget / price).quantize(LOT, rounding=ROUND_FLOOR))
    while guess >= LOT:
        if taker_charges(guess, price, multiplier)['debit'] <= budget:
            return guess
        guess -= LOT
    return Decimal('0')

def _touch(top, side):
    if side == 'yes':
        return top['yes_ask'], top['yes_ask_size']
    return top['no_ask'], top['no_ask_size']

def propose(store, market, probability, book, decision_at, book_at, remaining, risk, multiplier, enabled, quality_ok, max_age):
    """Record a taker intent. Does not insert a position or move cash."""
    if getattr(store, 'legacy_readonly', False):
        return {'ok': False, 'reason': 'legacy_readonly'}
    if not store.fills_allowed():
        return {'ok': False, 'reason': 'disarmed'}
    if not enabled:
        return {'ok': False, 'reason': 'simulation_off'}
    if not quality_ok:
        return {'ok': False, 'reason': 'proxy_not_allowed'}
    if market is None or not market.supported_rules or market.threshold is None:
        return {'ok': False, 'reason': 'unsupported_market'}
    if remaining < 60:
        return {'ok': False, 'reason': 'final_minute'}
    if remaining < risk.cutoff:
        return {'ok': False, 'reason': 'cutoff'}
    if multiplier is None or D(multiplier) <= 0:
        return {'ok': False, 'reason': 'unsupported_fee'}
    freshness = _fresh(book_at, decision_at, max_age)
    if freshness:
        return {'ok': False, 'reason': freshness}
    if utc(book_at) > utc(decision_at):
        return {'ok': False, 'reason': 'book_after_decision'}
    top = book_top(book, market.price_ranges)
    if top is None:
        return {'ok': False, 'reason': 'unusable_book'}
    if store.open_intent(market.ticker):
        return {'ok': False, 'reason': 'intent_open'}
    if store.position_count(market.ticker) >= risk.max_trades:
        return {'ok': False, 'reason': 'max_trades'}
    available = store.available_cash()
    budget = min(D(risk.stake), available * D(risk.fraction), available)
    best = None
    for side, prob in (('yes', D(probability)), ('no', Decimal('1') - D(probability))):
        if not (Decimal('0') < prob < 1):
            continue
        problem = _spread_ok(top, side, risk)
        if problem:
            continue
        ask, size = _touch(top, side)
        if size < D(risk.min_depth):
            continue
        qty = affordable(budget, ask, multiplier, size)
        if qty < LOT:
            continue
        charges = taker_charges(qty, ask, multiplier)
        edge = prob - (charges['debit'] / qty)
        if edge < D(risk.min_ev):
            continue
        ev = prob * qty - charges['debit']
        if best is None or ev > best['ev']:
            best = {'side': side, 'ask': ask, 'qty': qty, 'reserve': charges['debit'], 'prob': prob, 'ev': ev}
    if best is None:
        return {'ok': False, 'reason': 'no_trade'}
    intent_id = store.insert_intent({
        'ticker': market.ticker,
        'decision_at': utc(decision_at).isoformat(),
        'signal_book_at': utc(book_at).isoformat(),
        'side': best['side'],
        'limit_price': format(best['ask'], 'f'),
        'quantity': format(best['qty'], 'f'),
        'reserve': format(best['reserve'], 'f'),
        'probability': format(best['prob'], 'f'),
        'fee_multiplier': format(D(multiplier), 'f'),
        'price_ranges': json.dumps(market.price_ranges),
        'status': 'open',
        'execution_model': EXECUTION_MODEL,
    })
    if intent_id is None:
        return {'ok': False, 'reason': 'cash'}
    return {'ok': True, 'reason': 'open', 'intent_id': intent_id, 'side': best['side'], 'limit_price': best['ask'], 'quantity': best['qty'], 'reserve': best['reserve']}

def confirm(store, intent, market, book, book_at, now, risk, max_age, min_latency, max_intent_age, fetched_book):
    """One taker attempt against a strictly later book, or keep waiting.

    The first fresh book at least `min_latency` after the decision is the only
    attempt. If it is not marketable, the intent expires and does not rest.
    """
    if getattr(store, 'legacy_readonly', False):
        return {'ok': False, 'reason': 'legacy_readonly'}
    current = store.get_intent(intent['id'])
    if current is None or current['status'] != 'open':
        return {'ok': False, 'reason': 'not_open', 'position_id': None if current is None else current['position_id']}
    intent = current
    decision, signal = utc(intent['decision_at']), utc(intent['signal_book_at'])
    now = utc(now)
    if intent['status'] != 'open':
        return {'ok': False, 'reason': 'not_open'}
    if now - decision > timedelta(seconds=max_intent_age):
        store.expire_intent(intent['id'], now.isoformat(), 'expired')
        return {'ok': False, 'reason': 'expired'}
    if market is None or market.ticker != intent['ticker']:
        store.expire_intent(intent['id'], now.isoformat(), 'contract_changed')
        return {'ok': False, 'reason': 'contract_changed'}
    if not market.supported_rules or market.threshold is None:
        store.expire_intent(intent['id'], now.isoformat(), 'unsupported_market')
        return {'ok': False, 'reason': 'unsupported_market'}
    remaining = market.remaining(now)
    if remaining < 60:
        store.expire_intent(intent['id'], now.isoformat(), 'final_minute')
        return {'ok': False, 'reason': 'final_minute'}
    if not fetched_book:
        return {'ok': False, 'reason': 'no_book'}
    book_at = utc(book_at)
    if book_at <= decision or book_at <= signal:
        return {'ok': False, 'reason': 'same_snapshot'}
    if (book_at - decision).total_seconds() < min_latency:
        return {'ok': False, 'reason': 'too_soon'}
    freshness = _fresh(book_at, now, max_age)
    if freshness:
        return {'ok': False, 'reason': freshness}
    top = book_top(book, market.price_ranges)
    if top is None:
        store.expire_intent(intent['id'], now.isoformat(), 'unusable_book', book_at.isoformat())
        return {'ok': False, 'reason': 'unusable_book'}
    side = intent['side']
    problem = _spread_ok(top, side, risk)
    ask, size = _touch(top, side)
    limit, wanted = D(intent['limit_price']), D(intent['quantity'])
    if problem or size < D(risk.min_depth) or ask > limit:
        store.expire_intent(intent['id'], now.isoformat(), problem or ('thin_book' if size < D(risk.min_depth) else 'price_through_limit'), book_at.isoformat())
        return {'ok': False, 'reason': problem or ('thin_book' if size < D(risk.min_depth) else 'price_through_limit')}
    available = store.available_cash() + D(intent['reserve'])
    budget = min(D(intent['reserve']), available)
    qty = affordable(budget, ask, intent['fee_multiplier'], min(size, wanted))
    if qty < LOT:
        store.expire_intent(intent['id'], now.isoformat(), 'no_size', book_at.isoformat())
        return {'ok': False, 'reason': 'no_size'}
    charges = taker_charges(qty, ask, intent['fee_multiplier'])
    if charges['debit'] > D(intent['reserve']) + CENT:
        store.expire_intent(intent['id'], now.isoformat(), 'reserve', book_at.isoformat())
        return {'ok': False, 'reason': 'reserve'}
    position_id = store.fill_intent(intent['id'], {
        'ticker': intent['ticker'],
        'timestamp': now.isoformat(),
        'side': side,
        'quantity': format(qty, 'f'),
        'price': format(ask, 'f'),
        'fee': format(charges['net_fee'], 'f'),
        'cost': format(charges['debit'], 'f'),
        'execution_model': EXECUTION_MODEL,
        'confirm_book_at': book_at.isoformat(),
    })
    if position_id is None:
        return {'ok': False, 'reason': 'not_open'}
    return {'ok': True, 'reason': 'filled', 'position_id': position_id, 'quantity': qty, 'price': ask, 'debit': charges['debit'], 'fee': charges['net_fee']}

def step_execution(store, market, probability, book, book_at, now, risk, multiplier, enabled, quality_ok, max_age, min_latency, max_intent_age, fetched_book):
    """Confirm or expire resting intents, then maybe record a new decision."""
    if getattr(store, 'legacy_readonly', False):
        return {'confirmed': None, 'proposed': {'ok': False, 'reason': 'legacy_readonly'}}
    result = None
    for intent in store.open_intents():
        if not enabled or not quality_ok:
            store.expire_intent(intent['id'], utc(now).isoformat(), 'disarmed')
            continue
        result = confirm(store, intent, market, book, book_at, now, risk, max_age, min_latency, max_intent_age, fetched_book)
    proposed = {'ok': False, 'reason': 'no_forecast'}
    if probability is not None and market is not None and fetched_book:
        remaining = market.remaining(now)
        proposed = propose(store, market, probability, book, now, book_at, remaining, risk, multiplier, enabled, quality_ok, max_age)
    return {'confirmed': result, 'proposed': proposed}

def liquidation(side, quantity, book, ranges, multiplier):
    """Taker exit value of an open position, or None if the bid cannot be priced.

    Selling hits the bid and pays the same taker schedule. This is not a mark
    at mid and not a claim that the bid is still there for a queue.
    """
    top = book_top(book, ranges)
    if top is None or multiplier is None:
        return None
    bid = top['yes_bid'] if side == 'yes' else top['no_bid']
    size = top['yes_bid_size'] if side == 'yes' else top['no_bid_size']
    qty = D(quantity)
    if size < qty or qty < LOT:
        return None
    try:
        charges = taker_charges(qty, bid, multiplier)
    except ValueError:
        return None
    return charges['premium'] - charges['net_fee']
