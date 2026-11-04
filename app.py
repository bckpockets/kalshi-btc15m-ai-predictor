import html
import os
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from btc15.domain import now
from btc15.engine import Engine
from btc15.paper import Risk,economics
from research.evaluate import metrics

st.set_page_config(page_title='BTC / 15 · Research terminal',page_icon='◷',layout='wide')
st.markdown('''<style>
.block-container{padding-top:2rem;max-width:1500px}h1{font-weight:650!important;letter-spacing:-.04em}h3{font-weight:500!important}.eyebrow{font-size:.75rem;letter-spacing:.14em;color:#9caeb8}.forecast{border-top:2px solid #55d9b0;padding:20px 0}.forecast.down{border-color:#ff8f92}.forecast .number{font:600 clamp(36px,5vw,76px)/1.15 monospace;letter-spacing:-.04em;color:#55d9b0}.forecast.down .number{color:#ff8f92}.caption{color:#a9bac5;font-size:.85rem}.stMetric{font-variant-numeric:tabular-nums}div[data-testid="stTabs"] button{font-size:1rem}footer{visibility:hidden}
</style>''',unsafe_allow_html=True)
st.title('Bitcoin. Every fifteen minutes.')
st.caption('Read-only market data · Experimental probabilities · Virtual portfolio only')

@st.cache_resource
def engine():return Engine()
e=engine()
with st.sidebar:
    st.subheader('Research controls')
    st.caption('Shared by all sessions in this server process. Run a single-user server for private experiments.')
    enabled=st.toggle('Enable paper simulation',value=False)
    accept=st.checkbox('Allow unvalidated proxy forecasts',value=False,help='Experimental baseline has no measured calibration. Enabling this permits virtual simulation only.')
    stake=st.number_input('Maximum spend per entry ($)',1.,1000.,10.)
    fraction=st.slider('Maximum cash per entry (%)',1,20,2)/100
    edge=st.slider('Minimum EV per contract ($)',0.,.25,.03,.005)
    spread=st.slider('Maximum spread ($)',.01,.50,.10,.01)
    depth=st.number_input('Minimum available contracts',1,10000,10)
    cutoff=st.number_input('Entry cutoff (seconds remaining)',60,900,90)
    trades=st.number_input('Maximum entries per interval',1,10,1)
    e.configure(enabled,accept,Risk(stake,fraction,edge,spread,depth,trades,cutoff))
    st.divider();st.caption('Coinbase is a proxy for the CF Benchmarks BRTI. Model probability, measured model quality, and hypothetical price edge are separate quantities.')

def plot_style(fig,height=350):
    fig.update_layout(template='plotly_dark',height=height,paper_bgcolor='#101519',plot_bgcolor='#101519',margin=dict(l=0,r=12,t=15,b=0),legend=dict(orientation='h',y=1.12),xaxis_rangeslider_visible=False)
    return fig

@st.fragment(run_every='2s')
def terminal():
    s=e.snapshot();m=s.get('market');t=s.get('tick');p=s.get('prediction');at=now()
    age=(at-t['timestamp']).total_seconds() if t else None
    stale=age is None or age>e.age or age<0 or not s.get('updated') or (at-s['updated']).total_seconds()>e.age
    if m and m.remaining(at)<60:p=None
    if stale:p=None
    cols=st.columns([1.2,1.2,1,1])
    stats24=s.get('stats24') or {}
    change=(t['price']/float(stats24['open'])-1)*100 if t and stats24.get('open') else None
    cols[0].metric('BTC / USD · Coinbase',f"${t['price']:,.2f}" if t else '—',f'{change:+.2f}% / 24h' if change is not None else None)
    remaining=max(0,int(m.remaining(at))) if m else None
    cols[1].metric('Until contract close',f'{remaining//60:02}:{remaining%60:02}' if remaining is not None else '—')
    cols[2].metric('Official opening threshold',f'${m.threshold:,.2f}' if m and m.threshold else 'Pending')
    cols[3].metric('Observation age',f'{age:.1f}s' if age is not None else 'Unavailable')
    if t:st.caption(f"Source: {t['source']} · Observation: {t['timestamp'].isoformat()}")
    if m:st.caption(f'{m.ticker}  ·  {m.status.upper()}  ·  {m.open_time:%H:%M:%S} → {m.close_time:%H:%M:%S} UTC')
    if s.get('error'):st.warning(s['error'])
    if stale:st.info('DATA UNAVAILABLE · Waiting for fresh market and exchange observations. Cached prices may remain visible.')
    left,right=st.columns(2)
    up=round(p['probability']*100,1) if p else None
    for c,label,value,cls in [(left,'↗ UP / YES',up,''),(right,'↘ DOWN / NO',round(100-up,1) if up is not None else None,' down')]:
        c.markdown(f'<div class="forecast{cls}"><div class="eyebrow">{label}</div><div class="number">{str(value)+"%" if value is not None else "—"}</div><div class="caption">Estimated official contract outcome · proxy model</div></div>',unsafe_allow_html=True)
    if p:
        st.progress(p['probability'])
        st.caption(f"{p['version']} · Updated {p['timestamp']} · Model quality: unvalidated proxy" if p['ml'] is None else f"{p['version']} · Updated {p['timestamp']} · See held-out evaluation report")
    live,history,performance,portfolio,rules=st.tabs(['Market view','Probability path','Observed performance','Paper portfolio','Rules & methodology'])
    with live:
        c=s['candles']
        if not c.empty:
            c=c[c.timestamp<=at].tail(90).copy()
            fig=go.Figure(go.Candlestick(x=c.timestamp,open=c.open,high=c.high,low=c.low,close=c.close,name='Coinbase 1m',increasing_line_color='#55d9b0',decreasing_line_color='#ff8f92'))
            fig.add_trace(go.Scatter(x=c.timestamp,y=c.close.rolling(20).mean(),name='MA 20',line=dict(color='#b8c8d8',width=1)))
            volume=c.volume.cumsum();vwap=(c.close*c.volume).cumsum()/volume.replace(0,float('nan'))
            fig.add_trace(go.Scatter(x=c.timestamp,y=vwap,name='Visible-window VWAP',line=dict(color='#d9bb77',width=1)))
            if m and m.threshold:fig.add_hline(y=m.threshold,line_dash='dash',line_color='#e0ba69',annotation_text='Official threshold')
            if m:fig.add_vline(x=m.open_time.timestamp()*1000,line_dash='dot')
            fig.add_vline(x=at.timestamp()*1000,line_color='#8193a0',line_dash='dot')
            st.plotly_chart(plot_style(fig),width='stretch',key='price')
        else:st.info('Price history appears after Coinbase returns completed candles.')
        if p:
            f=p['features'];a,b,c,d=st.columns(4)
            a.metric('Realized volatility / √second',f"{f['sigma']:.6f}");b.metric('RSI · 14',f"{f['rsi']:.1f}");c.metric('MACD',f"{f['macd']:.2f}");d.metric('Book imbalance',f"{f['imbalance']:+.2f}")
            st.write(f"Coinbase is ${abs(f['distance']):,.2f} {'above' if f['distance']>=0 else 'below'} the official threshold. The model has {int(p['remaining'])} seconds left and an assumed settlement-average standard deviation of ${p['stats']['sd_usd']:,.2f}, including proxy basis uncertainty.")
            st.caption(f"Statistical: {p['baseline']:.1%} · ML: {format(p['ml'],'.1%') if p['ml'] is not None else 'unavailable — train on official outcomes'} · Final: {p['probability']:.1%}")
            st.caption(f"Assumption-based 95% range for final average: ${p['stats']['lower_price']:,.2f}–${p['stats']['upper_price']:,.2f}. This is not a confidence interval for model quality.")
        if m:
            st.caption(('CACHED · ' if stale else 'Market snapshot · ')+str(s.get('market_received','not yet received'))+' · Quotes may change before a hypothetical fill.')
            quotes=pd.DataFrame([{'Side':'UP / YES','Bid':m.yes_bid_dollars,'Ask':m.yes_ask_dollars},{'Side':'DOWN / NO','Bid':m.no_bid_dollars,'Ask':m.no_ask_dollars}])
            st.dataframe(quotes,hide_index=True,width='stretch')
            st.caption(f'Volume: {m.volume_fp:,.0f} contracts · Open interest: {m.open_interest_fp:,.0f} contracts · Open interest is not executable liquidity.')
            estimates=[];series=s.get('series') or {};fee_ok=series.get('fee_type')=='quadratic' and series.get('fee_multiplier') is not None
            quote_fresh=s.get('book_received') and (at-s['book_received']).total_seconds()<=e.age
            if p and fee_ok and quote_fresh:
                for side,prob in [('yes',p['probability']),('no',1-p['probability'])]:
                    levels=s['book'].get('no' if side=='yes' else 'yes',[])
                    if levels:
                        bid,available=max(levels);ask=1-bid
                        if 0<ask<1:estimates.append({'side':side,'probability':prob,'executable_ask':ask,'available':available,**economics(prob,ask,1,float(series['fee_multiplier']))})
            if estimates:
                best=max(estimates,key=lambda v:v['edge']);st.subheader((best['side'].upper()+' EDGE · hypothetical') if best['edge']>=e.risk.min_ev else 'NO EDGE')
                st.dataframe(pd.DataFrame(estimates),hide_index=True,width='stretch')
            elif not p:st.caption('FORECAST UNAVAILABLE · Edge cannot be evaluated without a fresh, valid forecast.')
            else:st.caption('EXECUTABLE QUOTES UNAVAILABLE · Order-book depth or supported fee schedule is missing.')
            if s.get('book_error'):st.caption('Order book unavailable: '+s['book_error'])
            with st.expander('Order-book depth & recent Kalshi trades'):
                st.json(s['book']);st.dataframe(pd.DataFrame(s['trades']),width='stretch')
    with history:
        if not m:st.info('No active contract. Probability history will appear when a market opens.')
        if m:
            h=e.store.frame('SELECT * FROM predictions WHERE ticker=? ORDER BY timestamp',(m.ticker,))
            if len(h):
                fig=go.Figure()
                for name,y,color in [('UP',h.probability,'#55d9b0'),('DOWN',1-h.probability,'#ff8f92'),('Kalshi midpoint',h.market_probability,'#b8c8d8')]:fig.add_trace(go.Scatter(x=h.timestamp,y=y,name=name,line=dict(color=color)))
                fig.update_yaxes(range=[0,1],tickformat='.0%');st.plotly_chart(plot_style(fig),width='stretch',key='probabilities')
            else:st.info('No valid forecasts recorded for this contract yet.')
    with performance:
        if e.model.artifact:
            with st.expander('Loaded model · unseen test report & feature importance'):
                st.json(e.model.artifact['test_report'])
                from btc15.features import FEATURES
                st.dataframe(pd.DataFrame({'feature':FEATURES,'importance':e.model.artifact['model'].feature_importances_}).sort_values('importance',ascending=False),hide_index=True)
        else:st.caption('ML unavailable. No trained artifact loaded; the statistical baseline has no measured calibration yet.')
        d=e.store.frame('SELECT p.*,m.result AS label FROM predictions p JOIN markets m USING(ticker) WHERE m.result IS NOT NULL ORDER BY timestamp')
        if d.empty:st.info('No officially resolved forecasts yet. Accuracy and calibration appear after Kalshi publishes outcomes.')
        else:
            latest=d.groupby('ticker').tail(1);a,b=st.columns(2);a.metric('Resolved prediction snapshots',len(d));b.metric('Distinct resolved contracts',len(latest))
            for label,frame in [('Contract level · last forecast',latest),('Prediction level · correlated snapshots',d)]:
                r=metrics(frame.label,frame.probability);st.subheader(label);st.write({k:r[k] for k in ['accuracy','brier','log_loss','ece']})
            st.line_chart(latest.assign(correct=(latest.probability>=.5)==latest.label).set_index('timestamp').correct.rolling(20,min_periods=1).mean())
            st.dataframe(pd.DataFrame(metrics(d.label,d.probability)['reliability']),hide_index=True)
            st.dataframe(d.groupby(pd.cut(d.remaining,[0,120,300,600,901]),observed=True).agg(n=('probability','size'),average_probability=('probability','mean'),observed_up=('label','mean')))
    with portfolio:
        account=e.store.frame('SELECT * FROM account').iloc[0];pos=e.store.frame('SELECT * FROM positions ORDER BY id');open_cost=float(pos.loc[pos.payout.isna(),'cost'].sum()) if len(pos) else 0;equity=account.cash+open_cost
        a,b,c=st.columns(3);a.metric('Virtual cash',f'${account.cash:,.2f}');b.metric('Equity · open positions at cost',f'${equity:,.2f}');c.metric('Realized return',f'{(equity/account.initial-1):+.2%}')
        st.caption('Simulation '+('enabled' if e.enabled else 'disabled')+' · No real orders can be placed. Top-of-book fills are hypothetical and capped by observed size; no queue or latency model.')
        if len(pos):
            done=pos.dropna(subset=['payout']).sort_values('resolved_at');curve=account.initial+(done.payout-done.cost).cumsum();peak=curve.cummax().clip(lower=account.initial)
            st.metric('Maximum realized-equity drawdown',f'{((peak-curve)/peak).max():.2%}' if len(curve) else '—');st.metric('Win rate',f'{(done.payout>0).mean():.1%}' if len(done) else '—')
            st.line_chart(pd.DataFrame({'equity':curve.to_numpy()},index=done.resolved_at));st.dataframe(pos,width='stretch',hide_index=True)
            st.download_button('Download transaction history',pos.to_csv(index=False),'paper-transactions.csv','text/csv')
        else:st.info('No hypothetical entries. Enable simulation and explicitly allow the proxy model to begin.')
    with rules:
        st.subheader('Forecast the contract, not the next fifteen minutes')
        st.write('The recognized rule compares the final 60-second BRTI average with the pre-opening 60-second BRTI average. Coinbase observations are exchange-price proxies. The continuous distribution uses a half-cent boundary adjustment for the published two-decimal rounding rule. The official published threshold is never replaced by the first observed spot price.')
        if m:st.text(m.rules_primary);st.text(m.rules_secondary)
        st.write('The baseline is a zero-drift local Brownian approximation for the ending average. Volatility comes from completed one-minute candles. A configurable dollar basis uncertainty is added. Final-minute predictions are suspended without official index observations.')
        st.write('No ML model is bundled. Train and validate an artifact using official outcomes before loading it. Model artifacts use pickle serialization: load only files you trust.')
        if s.get('series'):st.json(s['series'])
        st.caption('Read README.md and docs/VALIDATION.md for verification boundaries, data schemas, and limitations.')
terminal()
