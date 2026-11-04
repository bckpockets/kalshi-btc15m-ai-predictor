import httpx
import pytest
from btc15.feeds import Kalshi,Coinbase

def test_fixed_point_book_and_pagination():
    seen=[]
    def handler(req):
        seen.append(str(req.url))
        if 'orderbook' in req.url.path:return httpx.Response(200,json={'orderbook_fp':{'yes_dollars':[['0.1','5.25'],['0.5','3.00']],'no_dollars':[['0.45','9']]}})
        return httpx.Response(200,json={'markets':[],'cursor':'next' if not req.url.params.get('cursor') else ''})
    k=Kalshi('https://example.com/trade-api/v2');k.client.close();k.client=httpx.Client(base_url='https://example.com/trade-api/v2',transport=httpx.MockTransport(handler))
    assert k.book('TEST')['yes']==[(.5,3.),(.1,5.25)]
    assert k.markets()==[];assert len(seen)==3
    assert '/trade-api/v2/markets' in seen[0]

def test_coinbase_candle_end_timestamp():
    c=Coinbase();c.client.close();c.client=httpx.Client(base_url='https://example.com',transport=httpx.MockTransport(lambda r:httpx.Response(200,json=[[1000,1,3,2,2.5,10]])))
    assert c.candles().timestamp.iloc[0].timestamp()==1060
