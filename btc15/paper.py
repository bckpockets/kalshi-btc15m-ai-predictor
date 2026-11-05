"""Virtual fees, edge and risk limits. No order submission and no paid API.

Quadratic fee, used when the series reports fee_type=quadratic:

    fee = ceil_to_cent(0.07 × quantity × price × (1 − price) × multiplier)

`economics()['edge']` is expected profit per contract (payout is $1), which is
what `Risk.min_ev` compares against. `ev` is that quantity times contract count.

Risk defaults match the sidebar and README: stake $10, 2% of cash, $0.03
minimum EV per contract, $0.10 maximum spread, depth 10, one entry per
interval, 90-second cutoff. STARTING_BANKROLL ($1000) is the new-account
initial in Store, not a field on Risk.
"""
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
import math

@dataclass
class Risk:
    stake: float=10
    fraction: float=.02
    min_ev: float=.03
    max_spread: float=.10
    min_depth: int=10
    max_trades: int=1
    cutoff: float=90

def fee(quantity,price,multiplier=1.):
    if quantity<1 or not 0<price<1 or multiplier<=0: raise ValueError('Invalid fee inputs')
    p=Decimal(str(price))
    return float((Decimal('.07')*Decimal(quantity)*p*(1-p)*Decimal(str(multiplier))).quantize(Decimal('.01'),rounding=ROUND_CEILING))

def economics(probability,price,quantity=1,multiplier=1.):
    f=fee(quantity,price,multiplier);cost=quantity*price+f
    return {'fee':f,'cost':cost,'win_profit':quantity-cost,'loss':cost,'ev':quantity*probability-cost,'break_even':cost/quantity,'edge':probability-cost/quantity}

def entry(store,m,p,book,at,remaining,risk,fee_multiplier,enabled=False,quality_ok=False):
    if not enabled or not quality_ok or remaining<risk.cutoff: return None
    candidates=[]
    for side,prob,bid in [('yes',p,m.yes_bid_dollars),('no',1-p,m.no_bid_dollars)]:
        levels=book.get('no' if side=='yes' else 'yes',[])
        if not levels or bid is None: continue
        opposite,depth=max(levels);ask=round(1-opposite,6)
        if not 0<ask<1 or ask<bid or ask-bid>risk.max_spread or depth<risk.min_depth: continue
        candidates.append((prob,side,ask,int(depth)))
    with store.lock,store.db:
        cash=store.db.execute('SELECT cash FROM account WHERE id=1').fetchone()[0]
        count=store.db.execute('SELECT COUNT(*) FROM positions WHERE ticker=?',(m.ticker,)).fetchone()[0]
        if count>=risk.max_trades: return None
        budget=min(risk.stake,cash*risk.fraction,cash)
        best=None
        for prob,side,ask,depth in candidates:
            q=min(depth,math.floor(budget/ask))
            while q>0 and economics(prob,ask,q,fee_multiplier)['cost']>budget: q-=1
            if not q: continue
            e=economics(prob,ask,q,fee_multiplier)
            if e['edge']>=risk.min_ev and (best is None or e['ev']>best[-1]['ev']): best=(side,ask,q,e)
        if best is None: return None
        side,ask,q,e=best
        store.db.execute('INSERT INTO positions(ticker,timestamp,side,quantity,price,fee,cost) VALUES(?,?,?,?,?,?,?)',(m.ticker,at.isoformat(),side,q,ask,e['fee'],e['cost']))
        store.db.execute('UPDATE account SET cash=cash-? WHERE id=1',(e['cost'],))
        return best
