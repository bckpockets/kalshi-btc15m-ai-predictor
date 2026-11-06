"""Run scripts/resume-after-wake.sh. These are process checks, not import checks."""
from hashlib import sha256
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'resume-after-wake.sh'
REAL_PYTHON = Path(sys.executable)

def _venv(tmp_path):
    bindir = tmp_path / 'venv' / 'bin'
    bindir.mkdir(parents=True)
    log = tmp_path / 'python-calls.log'
    wrapper = bindir / 'python'
    wrapper.write_text(
        '#!/bin/sh\n'
        f'printf "%s\\n" "$*" >> "{log}"\n'
        f'exec "{REAL_PYTHON}" "$@"\n'
    )
    wrapper.chmod(0o755)
    return log

def _ledger(tmp_path):
    from btc15.store import Store
    paper = tmp_path / 'paper'
    paper.mkdir()
    path = paper / 'runtime.sqlite'
    store = Store(str(path), 100)
    store.start_run({'model_version': 'statistical-proxy-v1'})
    store.arm_fills()
    store.db.execute('UPDATE account SET cash=81.42 WHERE id=1')
    store.db.commit()
    store.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    store.db.close()
    return paper, path, sha256(path.read_bytes()).hexdigest()

def _run(tmp_path, args, venv=True):
    env = os.environ.copy()
    env['HOME'] = str(tmp_path / 'home')
    env['VENV'] = str(tmp_path / 'venv') if venv else str(tmp_path / 'missing-venv')
    Path(env['HOME']).mkdir(exist_ok=True)
    return subprocess.run(
        [str(SCRIPT), *args], cwd=ROOT, env=env, text=True, capture_output=True, check=False,
    )

def test_default_script_uses_venv_and_does_not_start_or_change_the_ledger(tmp_path):
    log = _venv(tmp_path)
    paper, path, digest = _ledger(tmp_path)
    done = _run(tmp_path, ['--paper-dir', str(paper)])
    assert done.returncode == 0, done.stderr
    text = done.stdout
    calls = log.read_text()
    assert f'interpreter={tmp_path / "venv" / "bin" / "python"}' in text
    assert 'preflight=pass' in text
    assert 'paper_start=not performed by this script' in text
    assert 'depth_start=not requested' in text
    assert '-m research.resume' in calls
    assert '--attach' not in calls and '--attach' not in text
    assert 'research.runner' not in calls and 'research.supervise' not in text
    assert sha256(path.read_bytes()).hexdigest() == digest
    uri = path.resolve().as_uri() + '?mode=ro'
    db = sqlite3.connect(uri, uri=True)
    try:
        initial, cash = db.execute('SELECT initial, cash FROM account WHERE id=1').fetchone()
        armed, baseline = db.execute('SELECT fills_armed, baseline_cash FROM run_manifest WHERE id=1').fetchone()
    finally:
        db.close()
    assert initial == 100 and abs(cash - 81.42) < 1e-9
    assert armed == 1 and baseline == 100

def test_start_depth_dry_run_names_research_runner_and_does_not_exec_it(tmp_path):
    log = _venv(tmp_path)
    paper, path, digest = _ledger(tmp_path)
    depth = tmp_path / 'depth'
    done = _run(tmp_path, [
        '--paper-dir', str(paper),
        '--start-depth', '--until', '2026-11-07T23:20:00Z',
        '--depth-dir', str(depth),
        '--dry-run',
    ])
    assert done.returncode == 0, done.stderr
    text = done.stdout
    calls = log.read_text()
    assert 'depth_start=dry-run' in text
    assert '-m research.runner ' in text
    assert 'research.supervise' not in text
    assert '-m research.depth_recorder ' in text
    assert '--attach' not in text and '--attach' not in calls
    assert 'research.runner' not in calls
    assert 'research.depth_recorder' not in calls
    assert sha256(path.read_bytes()).hexdigest() == digest
    assert not depth.exists()

def test_start_depth_without_until_does_not_launch(tmp_path):
    log = _venv(tmp_path)
    done = _run(tmp_path, ['--start-depth', '--depth-dir', str(tmp_path / 'depth')])
    assert done.returncode == 64
    calls = log.read_text() if log.exists() else ''
    assert 'research.runner' not in calls
    assert '--start-depth requires --until' in done.stderr

def test_missing_venv_falls_back_to_python3_and_still_does_not_start(tmp_path):
    done = _run(tmp_path, [], venv=False)
    assert done.returncode == 0, done.stderr
    assert 'interpreter=python3' in done.stdout
    assert 'paper_start=not performed by this script' in done.stdout
    assert 'depth_start=not requested' in done.stdout
    assert '--attach' not in done.stdout

def test_runner_module_help_is_the_real_entrypoint():
    done = subprocess.run(
        [str(REAL_PYTHON), '-m', 'research.runner', '--help'],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert done.returncode == 0, done.stderr
    assert '--until' in done.stdout
    missing = subprocess.run(
        [str(REAL_PYTHON), '-m', 'research.supervise', '--help'],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert missing.returncode != 0
