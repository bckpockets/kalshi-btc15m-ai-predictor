"""Offline UI empty-state smoke test; no fictional quotes shown."""
from pathlib import Path
import pandas as pd
from streamlit.testing.v1 import AppTest
from btc15.paper import Risk
from btc15.store import Store
from types import SimpleNamespace

def test_dashboard_empty_state(monkeypatch,tmp_path):
    class EmptyEngine:
        def __init__(self):
            self.store=Store(str(tmp_path/'ui.sqlite'));self.model=SimpleNamespace(artifact=None);self.age=10;self.risk=Risk();self.enabled=False
        def configure(self,enabled,accept,risk):self.enabled=enabled;self.risk=risk
        def snapshot(self):return {'error':'Connecting to public feeds…','market':None,'prediction':None,'tick':None,'candles':pd.DataFrame(),'book':{},'trades':[]}
    monkeypatch.setattr('btc15.engine.Engine',EmptyEngine)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=20).run()
    assert not app.exception
    assert len(app.tabs)==5
    assert any('No active contract' in x.value for x in app.info)
    assert any('No officially resolved' in x.value for x in app.info)
    assert app.toggle[0].value is False
