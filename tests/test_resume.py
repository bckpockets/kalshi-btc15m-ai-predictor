"""Resume checks the existing ledger and does not arm or reset it."""
from btc15.execution import EXECUTION_MODEL
from btc15.store import Store
from research.resume import Attachment, preflight

def _armed(path, bankroll=100):
    store = Store(str(path), bankroll)
    store.start_run({'model_version': 'statistical-proxy-v1'})
    store.arm_fills()
    return store

def test_preflight_keeps_drifted_cash_and_refuses_a_disarmed_file(tmp_path):
    path = tmp_path / 'runtime.sqlite'
    store = Store(str(path), 100)
    store.start_run({'model_version': 'statistical-proxy-v1'})
    disarmed = preflight(path)
    assert disarmed['ok'] is False
    assert disarmed['writes'] is False and disarmed['arms'] is False
    assert 'not_armed_refusing_to_arm' in disarmed['reasons']
    store.arm_fills()
    store.db.execute('UPDATE account SET cash=81.42 WHERE id=1')
    store.db.execute("INSERT INTO positions(ticker,timestamp,side,quantity,price,fee,cost,payout) VALUES('A','t','no',1,0.5,0.01,0.51,0)")
    store.db.execute("INSERT INTO positions(ticker,timestamp,side,quantity,price,fee,cost) VALUES('B','t','no',1,0.4,0.01,0.41)")
    store.db.commit()
    ready = preflight(path)
    assert ready['ok'] is True
    assert ready['ledger']['cash'] == '81.42'
    assert ready['ledger']['open_positions'] == 1
    assert ready['ledger']['resolved_positions'] == 1
    assert ready['ledger']['open_cost'] == '0.41'
    assert ready['manifest']['baseline_cash'] == '100.00'
    assert ready['manifest']['fills_armed'] == 1
    again = Store(str(path))
    assert again.start_run({'model_version': 'statistical-proxy-v1'})['baseline_cash'] == 100
    assert abs(again.db.execute('SELECT cash FROM account').fetchone()[0] - 81.42) < 1e-9

def test_preflight_rejects_a_different_baseline(tmp_path):
    path = tmp_path / 'other.sqlite'
    store = _armed(path, 1000)
    report = preflight(path)
    assert report['ok'] is False
    assert 'baseline_cash_mismatch' in report['reasons']
    assert 'initial_mismatch' in report['reasons']
    store.db.close()

def test_attach_does_not_start_or_arm_when_preflight_fails(tmp_path):
    path = tmp_path / 'runtime.sqlite'
    store = Store(str(path), 100)
    store.start_run({'model_version': 'statistical-proxy-v1'})
    store.db.close()
    def start():
        raise AssertionError('engine must not start')
    report = Attachment(path).open(start)
    assert report['ok'] is False
    assert 'not_armed_refusing_to_arm' in report['reasons']

def test_attach_enables_the_existing_model_and_does_not_arm(tmp_path):
    path = tmp_path / 'runtime.sqlite'
    store = _armed(path, 100)
    store.db.execute('UPDATE account SET cash=81.42 WHERE id=1')
    store.db.commit()
    store.db.close()
    class Fake:
        def __init__(self):
            self.execution = {'created': False}
            self.configured = None
            self.closed = False
            self.store = self
        def execution_status(self):
            return {'ok': True, 'fills_armed': True, 'execution_model': EXECUTION_MODEL, 'baseline_cash': 100}
        def configure(self, enabled, allow, risk):
            self.configured = (enabled, allow, type(risk).__name__)
        def close(self):
            self.closed = True
        def arm_fills(self):
            raise AssertionError('resume must not arm')
    created = []
    def start():
        engine = Fake()
        created.append(engine)
        return engine
    attachment = Attachment(path)
    report = attachment.open(start)
    assert report['attached'] is True and report['armed_by_resume'] is False
    assert created[0].configured[0] is True and created[0].configured[1] is True
    attachment.close()
    assert created[0].closed is True
    blocked = Attachment(path)
    class Created(Fake):
        def __init__(self):
            super().__init__()
            self.execution = {'created': True}
    refused = blocked.open(Created)
    assert refused['ok'] is False and refused.get('attached') is not True
    assert 'attach_blocked' in refused['reasons']
