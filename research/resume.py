"""Read-only check before attaching the existing taker-confirm ledger.

The kickoff script records a baseline and arms fills only on a new database.
This module does not reset cash, does not write a baseline, and does not arm.
`--attach` continues a ledger that is already armed on `taker-confirm-v1`.
"""
from decimal import Decimal
import json
import os
import sqlite3
from pathlib import Path
from btc15.execution import EXECUTION_MODEL, FEE_SCHEDULE
from btc15.paper import Risk
from research.runner import WriterLock

EXPECTED_MODEL_VERSION = 'statistical-proxy-v1'

def _dec(value):
    return Decimal(str(value))

def _money(value):
    return format(_dec(value).quantize(Decimal('0.01')), 'f')

def preflight(sqlite_path, expect_baseline_cash='100', status_path=None):
    """Inspect one ledger. The connection is mode=ro and nothing is written."""
    path = Path(sqlite_path)
    report = {
        'schema': 'resume-preflight-v1',
        'ok': False,
        'writes': False,
        'attaches': False,
        'arms': False,
        'resets_bankroll': False,
        'reasons': [],
        'sqlite': str(path),
    }
    if not path.is_file():
        report['reasons'].append('sqlite_missing')
        return report
    uri = path.resolve().as_uri() + '?mode=ro'
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    try:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'account' not in tables or 'run_manifest' not in tables:
            report['reasons'].append('missing_account_or_manifest')
            return report
        account = db.execute('SELECT initial, cash FROM account WHERE id=1').fetchone()
        manifest = db.execute('SELECT * FROM run_manifest WHERE id=1').fetchone()
        if account is None or manifest is None:
            report['reasons'].append('missing_account_or_manifest')
            return report
        open_n, open_cost, resolved = db.execute(
            'SELECT SUM(CASE WHEN payout IS NULL THEN 1 ELSE 0 END), '
            'COALESCE(SUM(CASE WHEN payout IS NULL THEN cost ELSE 0 END), 0), '
            'SUM(CASE WHEN payout IS NULL THEN 0 ELSE 1 END) FROM positions'
        ).fetchone()
        conflicts = 0
        if 'settlement_conflicts' in tables:
            conflicts = db.execute('SELECT COUNT(*) FROM settlement_conflicts').fetchone()[0]
    finally:
        db.close()
    expected = _dec(expect_baseline_cash)
    manifest = dict(manifest)
    reasons = []
    if manifest['execution_model'] != EXECUTION_MODEL:
        reasons.append('execution_model_mismatch')
    if manifest['fee_schedule'] != FEE_SCHEDULE:
        reasons.append('fee_schedule_mismatch')
    if manifest.get('model_version') != EXPECTED_MODEL_VERSION:
        reasons.append('model_version_mismatch')
    if int(manifest['fills_armed'] or 0) != 1 or not manifest.get('armed_at'):
        reasons.append('not_armed_refusing_to_arm')
    if _dec(manifest['baseline_cash']) != expected or not manifest.get('baseline_recorded_at'):
        reasons.append('baseline_cash_mismatch')
    if _dec(account['initial']) != expected:
        reasons.append('initial_mismatch')
    report['reasons'] = reasons
    report['ok'] = not reasons
    report['ledger'] = {
        'initial': _money(account['initial']),
        'cash': _money(account['cash']),
        'open_positions': int(open_n or 0),
        'open_cost': _money(open_cost or 0),
        'resolved_positions': int(resolved or 0),
        'settlement_conflicts': int(conflicts),
    }
    report['manifest'] = {
        'execution_model': manifest['execution_model'],
        'fee_schedule': manifest['fee_schedule'],
        'model_version': manifest.get('model_version'),
        'fills_armed': int(manifest['fills_armed'] or 0),
        'armed_at': manifest.get('armed_at'),
        'baseline_cash': _money(manifest['baseline_cash']),
        'baseline_recorded_at': manifest.get('baseline_recorded_at'),
    }
    report['note'] = (
        'Cash may differ from baseline cash. This check does not set it back. '
        'run_taker.py refuses an existing database and is not a resume path.'
    )
    if status_path and Path(status_path).is_file():
        try:
            status = json.loads(Path(status_path).read_text())
            report['status_json'] = {
                'updated_utc': status.get('updated_utc'),
                'phase': status.get('phase'),
                'note': 'status.json can be older than the sqlite backup. It is not the ledger.',
            }
        except json.JSONDecodeError:
            report['status_json'] = {'note': 'status.json was not valid JSON and was not used.'}
    return report

class Attachment:
    """Hold the paper-directory lock and enable the already-armed engine.

    `start_engine` is called only after preflight passes. `arm_fills` is never
    called. The caller closes the attachment, which closes the engine.
    """
    def __init__(self, sqlite_path, expect_baseline_cash='100', lock_dir=None):
        self.sqlite_path = sqlite_path
        self.expect_baseline_cash = expect_baseline_cash
        self.lock_dir = Path(lock_dir) if lock_dir else Path(sqlite_path).parent
        self.engine = None
        self.lock = None
        self.report = None
    def open(self, start_engine):
        self.report = preflight(self.sqlite_path, self.expect_baseline_cash)
        if not self.report['ok']:
            return self.report
        self.lock = WriterLock(self.lock_dir)
        self.lock.acquire()
        self.engine = start_engine()
        created = bool((getattr(self.engine, 'execution', None) or {}).get('created'))
        status = self.engine.store.execution_status()
        if created or not status.get('ok') or not status.get('fills_armed') or status.get('execution_model') != EXECUTION_MODEL:
            self.report = {**self.report, 'ok': False, 'attached': False, 'reasons': self.report['reasons'] + ['attach_blocked']}
            self.close()
            return self.report
        if _dec(status.get('baseline_cash')) != _dec(self.expect_baseline_cash):
            self.report = {**self.report, 'ok': False, 'attached': False, 'reasons': self.report['reasons'] + ['baseline_changed_during_attach']}
            self.close()
            return self.report
        self.engine.configure(True, True, Risk())
        self.report = {**self.report, 'attached': True, 'armed_by_resume': False}
        return self.report
    def close(self):
        if self.engine is not None:
            self.engine.close()
            self.engine = None
        if self.lock is not None:
            self.lock.release()
            self.lock = None

def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description='Check an existing taker-confirm ledger. Does not reset or arm it.')
    parser.add_argument('--sqlite', required=True)
    parser.add_argument('--expect-baseline-cash', default='100')
    parser.add_argument('--status')
    parser.add_argument('--attach', action='store_true', help='Start the engine on this already-armed ledger. Does not arm and does not rewrite baseline files.')
    args = parser.parse_args(argv)
    if not args.attach:
        report = preflight(args.sqlite, args.expect_baseline_cash, args.status)
        print(json.dumps(report, indent=2))
        raise SystemExit(0 if report['ok'] else 2)
    os.environ['DATABASE_PATH'] = str(Path(args.sqlite).resolve())
    os.environ['STARTING_BANKROLL'] = str(args.expect_baseline_cash)
    os.environ.pop('MODEL_PATH', None)
    from btc15.engine import Engine
    from research.runner import LockBusy
    attachment = Attachment(args.sqlite, args.expect_baseline_cash)
    try:
        report = attachment.open(Engine)
    except LockBusy:
        print(json.dumps({'ok': False, 'reasons': ['lock_busy'], 'writes': False, 'arms': False}))
        raise SystemExit(2)
    print(json.dumps(report, indent=2, default=str))
    if not report.get('attached'):
        raise SystemExit(2)
    import signal
    import time
    stopped = {'value': False}
    def _stop(signum, frame):
        stopped['value'] = True
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    try:
        while not stopped['value']:
            time.sleep(1)
    finally:
        attachment.close()
    raise SystemExit(0)

if __name__ == '__main__':
    main()
