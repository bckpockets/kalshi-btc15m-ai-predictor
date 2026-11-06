# Reliability and wake-up

Observed on the 2026-11-06 archive. This does not install a service, raise privileges, or promise that a guest process survives replacement of the virtual machine.

## What the package shows

Collected `2026-11-06T21:42:19Z`. Checksums in the package `SHA256SUMS` matched, including the sqlite backup.

- `who -b`: system boot `2026-11-06 21:06`. dmesg at `21:06:57Z` starts a fresh systemd userspace and includes `random: crng reseeded due to virtual machine fork` (quoted in `FORENSICS.md`).
- `sand-supervisor.log`: boot-fetch saw image-baked host `ea33472`, pointer `0299e17`, applied that swap, then `box-runtime restart signaled`.
- Copy-in log: store rehydrated **4493** files, **4493** verified, **0** failures.
- Workspace files were present after that boot. Many payload birth times cluster at `21:07Z`, which is also consistent with copy-in. The package does not prove the same block device never moved.
- Paper pidfile still said **110945**. Depth pidfile still said **121082**. Neither process was running at collection. Uptime then was about 33 minutes (`2026-11-06T21:40:53Z`).
- Last paper `status.json` write: `2026-11-06T21:06:07Z`. Last depth `response_received_at`: `2026-11-06T21:06:50Z`. Depth has a `started` record and **no** `stopped` record. `--until 2026-11-06T23:20:00Z` was not reached.
- `/workspace/kalshi-paper/venv` was absent. `/usr/bin/python3` was 3.13.5. `box-doctor` reported 11 checks, 0 failed.
- No user systemd bus, no linger, `crontab` not installed, no supervisord/runit/pm2 on `PATH`. A writable `~/.config/systemd/user` would not start units here: loginctl reports this guest was not booted with systemd as PID 1 (PID 1 is tini/pod-daemon).

Unknown, and not filled in: why the host bundle was swapped, whether a write was mid-statement at the cut, and whether the venv was deleted or simply not part of the hydrated tree. No application traceback in the packaged logs was identified as the stop. Depth's own log file in the package is empty (sha256 of empty content).

In-guest `nohup` cannot promise survival across that kind of replacement.

## What the code does

`research/runner.py` and the depth recorder:

- One exclusive `flock` on the output directory. A second writer exits **2** and is not replaced.
- `runner-manifest-v1` records Python, `httpx` version, platform, git commit if `git` works, and pid. It does not pin the host image.
- `runner-heartbeat-v1` is rewritten after snapshots (pid, seq, ticker, last receive time).
- If the previous receive time is more than `max(5 × poll, 30)` seconds before a new process starts, the new process appends a `gap` record with `fail_closed: true`. It does not invent the missing polls.
- Restart helper `python -m research.supervise` retries a **depth** child with backoff capped at 30 seconds until `--until`, and stops on exit 0 or on lock-busy exit 2. It does not run after the machine is gone.
- `scripts/resume-after-wake.sh` is dry by default. `--restore-venv` recreates only a missing venv from `requirements.lock` with `pip install --require-hashes` and does not fall back to unpinned `requirements.txt`. `--check-lock` is a dry-run of that install. `--start-depth` needs an explicit `--until`. The script never deletes a sqlite file, never resets a bankroll, never arms fills, and never passes `--attach`.

Paper execution stays `taker-confirm-v1`. Settlement code is unchanged. A new process that opens the existing database uses the existing manifest; `start_run` does not rewrite baseline cash when the manifest row is already there.

## After an external wake

An outside scheduler may run this once. Do not install it as a guest cron or a system unit on this host.

```bash
# read-only ledger check. Does not start paper.
scripts/resume-after-wake.sh --paper-dir /workspace/kalshi-paper-taker

# show whether the hashed lock can be installed. Does not change an existing venv.
scripts/resume-after-wake.sh --check-lock

# recreate a missing venv only, from requirements.lock with --require-hashes.
# No unpinned fallback. The lock was compiled for Python 3.11; the guest
# python after the boot was 3.13.5. Hashes pin packages, not that interpreter.
scripts/resume-after-wake.sh --restore-venv

# only if a new recording interval is wanted, and only when paper preflight
# was not requested or it passed
scripts/resume-after-wake.sh --start-depth --until 2026-11-07T23:20:00Z \
  --depth-dir "$HOME/kalshi-depth-prospective"
```

`/workspace/kalshi-paper-taker/run_taker.py` in the archive exits if `runtime.sqlite` already exists, then writes a new baseline and arms. It is the kickoff tool. It is not the resume tool. Do not delete the sqlite file to make that script run again.

A separate process may attach the same file after the check passes. It does not arm, does not rewrite `baseline.json` or `armed.json`, and unsets `MODEL_PATH`:

```bash
python -m research.resume --sqlite /workspace/kalshi-paper-taker/runtime.sqlite
python -m research.resume --sqlite /workspace/kalshi-paper-taker/runtime.sqlite --attach
```

The first command only prints the preflight. The second is the paper process. This script's wake entry does not pass `--attach`. Confirm the printed manifest still shows baseline cash **100**, `taker-confirm-v1`, `statistical-proxy-v1`, and the original `armed_at` before attaching. Current cash is expected to stay **81.42** with the open position still open. Do not point `DATABASE_PATH` at a new file.

The depth directory from the archive may be resumed. The new process appends a gap after `2026-11-06T21:06:50Z` and then records only new polls. It must not be described as continuous with the pre-boot file. The previous `--until` was `2026-11-06T23:20:00Z` and was not reached.
