"""Replay saved executable quotes; never reconstruct depth from current snapshots."""
import argparse,json
import pandas as pd
from btc15.paper import economics
from research.dataset import validate

def backtest(d,initial=1000.,min_edge=.03):
    d=validate(d);required=['yes_ask','no_ask','yes_depth','no_depth','quote_timestamp','fee_multiplier','ensemble']
    if any(k not in d for k in required):raise ValueError('Historical executable quotes, depth, fee multiplier and OOS ensemble required')
    cash=initial;open_positions=[];traded=set();events=[];equity=[initial]
    def settle(at):
        nonlocal cash,open_positions
        keep=[]
        for x in open_positions:
            if x['resolved_at']<=at:
                cash+=x['payout'];events.append(x)
            else:keep.append(x)
        open_positions=keep
    for _,r in d.sort_values('timestamp').iterrows():
        settle(r.timestamp)
        quote=pd.to_datetime(r.quote_timestamp,utc=True)
        if r.ticker in traded or r.remaining<90 or not 0 <= (r.timestamp-quote).total_seconds()<=10:continue
        choices=[]
        for side,p in [('yes',r.ensemble),('no',1-r.ensemble)]:
            ask=float(r[side+'_ask']);depth=float(r[side+'_depth'])
            if not 0<ask<1 or depth<1:continue
            e=economics(p,ask,1,r.fee_multiplier)
            if e['edge']>=min_edge and e['cost']<=min(10,.02*cash):choices.append((e['ev'],side,e))
        if choices:
            _,side,e=max(choices);cash-=e['cost'];traded.add(r.ticker)
            open_positions.append({'ticker':r.ticker,'timestamp':str(r.timestamp),'resolved_at':r.resolved_at,'side':side,'cost':e['cost'],'payout':int((side=='yes')==bool(r.label))})
        equity.append(cash+sum(x['cost'] for x in open_positions))
    settle(pd.Timestamp.max.tz_localize('UTC'));equity.append(cash)
    peak=pd.Series(equity).cummax();drawdown=((peak-pd.Series(equity))/peak).max()
    return {'trades':len(events),'ending_cash':cash,'roi':cash/initial-1,'max_cost_basis_drawdown':float(drawdown),'win_rate':sum(x['payout'] for x in events)/len(events) if events else None,'equity_cost_basis':equity,'transactions':events,'limitations':'Research replay only. It is not taker-confirm-v1: one contract, same-row quotes, no latency. Do not compare it to the live paper ledger. Open positions valued at cost, not mark-to-market.'}
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('csv');p.add_argument('--output',default='backtest.json');a=p.parse_args();r=backtest(pd.read_csv(a.csv));open(a.output,'w').write(json.dumps(r,indent=2,default=str));print(a.output)
