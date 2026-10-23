"""Post-commit outcomes (contract C3) on the real Windows hand-off script.

A real ``windows.ps1`` drives a real published launcher whose ``hermes``
application is a scripted stand-in: once ``hermes update`` exits 0 the install
is on the new version, so nothing afterwards may report "still on the previous
version", and the hand-off's own supervision (watchdog, probes, marker
release) must not turn healthy work into a failure.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from tests.installation_launcher_fixture import publish_fixture_launcher
from tests.waiting import wait_until

ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT = ROOT / 'scripts/desktop-update/windows.ps1'
CLI = """
import ctypes, json, os, subprocess, sys, time
from pathlib import Path
def main():
    if '--version' in sys.argv:
        print('Install directory: ' + str(Path(__file__).resolve().parents[1])); return 0
    if '--help' in sys.argv:
        if os.environ.get('HANDOFF_HELP_HANG'):
            time.sleep(600)
        print('--keep-stash'); return 0
    with Path(os.environ['HANDOFF_CALLS']).open('a') as stream:
        stream.write(json.dumps(sys.argv[1:]) + '\\n')
    if sys.argv[1:2] != ['update']:
        return 0
    busy = float(os.environ.get('HANDOFF_BUSY_SECONDS', '0'))
    end = time.monotonic() + busy
    while time.monotonic() < end:   # pipe-silent, CPU-busy (download/extract shape)
        pass
    if os.environ.get('HANDOFF_DELEGATE'):
        # Leave a live process running under the claim, named on line 4.
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        Path(os.environ['HANDOFF_DELEGATE']).write_text(str(child.pid))
        # update_lock's delegate line: pid + creation time (GetProcessTimes, limited rights).
        k32 = ctypes.windll.kernel32
        k32.OpenProcess.restype = ctypes.c_void_p
        handle = ctypes.c_void_p(k32.OpenProcess(0x1000, False, child.pid))
        times = [ctypes.c_ulonglong() for _ in range(4)]
        k32.GetProcessTimes(handle, *[ctypes.byref(t) for t in times])
        k32.CloseHandle(handle)
        ct = times[0].value / 1e7 - 11644473600
        marker = Path(os.environ['HERMES_HOME']) / '.hermes-update-in-progress'
        head = marker.read_bytes().decode().split('\\n')[:3]
        marker.write_bytes(('\\n'.join(head) + '\\ndelegate:%d ct:%.3f\\n' % (child.pid, ct)).encode())
    if os.environ.get('HANDOFF_HANG'):
        print(os.environ['HANDOFF_HANG'], flush=True)
        time.sleep(300)
    receipt = os.environ.get('HANDOFF_RECEIPT')   # this run's finalized receipt: outcome[,correlation]
    if receipt:
        outcome, correlation = (receipt.split(',') + [''])[:2]
        path = Path(os.environ['HERMES_HOME']) / 'logs' / 'update_receipts' / 'latest.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'correlation_id': correlation or os.environ.get('HERMES_UPDATE_CORRELATION_ID'),
                                    'outcome': outcome, 'finished_at': '2026-10-05T12:00:00+00:00'}, indent=2),
                        encoding='utf-8')
        return int(os.environ.get('HANDOFF_EXIT', '0'))
    print('Update complete!', flush=True)
    return 0
if __name__ == '__main__':
    sys.exit(main())
"""


def _handoff(tmp_path: Path, *args: str, verify: str = 'pass\n', timeout: int = 150, prepare=None, after_spawn=None, **env: str):
    install = tmp_path / 'checkout'
    publish_fixture_launcher(install, CLI)
    (install / 'hermes_cli/desktop_update_verify.py').write_text(verify, encoding='utf-8')
    home = tmp_path / 'home'; home.mkdir()
    if prepare is not None:
        prepare(home, install)
    calls = tmp_path / 'calls.jsonl'
    proc = subprocess.Popen(
        ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(SCRIPT),
         '-InstallRoot', str(install), '-NoUi', *args],
        cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env={**os.environ, 'HERMES_HOME': str(home), 'HERMES_RUNTIME_DIR': str(tmp_path / 'empty-store'),
             'HANDOFF_CALLS': str(calls), 'PYTHONIOENCODING': 'utf-8', **env},
    )
    deadline = time.monotonic() + timeout
    try:
        if after_spawn is not None:
            after_spawn(proc)
        out, _ = proc.communicate(timeout=max(0, deadline - time.monotonic()))
    except BaseException as exc:
        cleanup_error = ''
        if proc.poll() is None:
            try:
                subprocess.run(['taskkill', '/T', '/F', '/PID', str(proc.pid)],
                               capture_output=True, check=False, timeout=10)
            except (OSError, subprocess.SubprocessError) as cleanup_exc:
                cleanup_error = f'; tree cleanup failed: {cleanup_exc}'
            finally:
                if proc.poll() is None:
                    proc.kill()
        try:
            out, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired as cleanup_exc:
            cleanup_error += f'; output cleanup failed: {cleanup_exc}'
            proc.stdout.close()
            out = ''
        if isinstance(exc, subprocess.TimeoutExpired):
            pytest.fail(f'hand-off did not finish within {timeout}s: {out}{cleanup_error}')
        if cleanup_error:
            exc.add_note(cleanup_error)
        raise
    argv = [json.loads(line) for line in calls.read_text(encoding='utf-8-sig').splitlines()] if calls.exists() else []
    result_path = home / '.hermes-update-result.json'
    result = json.loads(result_path.read_text(encoding='utf-8-sig')) if result_path.is_file() else None
    return proc.returncode, out, argv, result, home


@pytest.mark.platforms('windows')
def test_verify_failure_after_a_committed_update_is_ok_with_warnings(tmp_path: Path) -> None:
    code, out, argv, result, _ = _handoff(tmp_path, verify='raise SystemExit(3)\n')
    assert code == 0, out
    assert (result['ok'], result['exit_code'], result['manual']) == (True, 0, True), result
    assert [w.split(':')[0] for w in result['warnings']] == ['verify'], result
    assert result['message'].startswith('Hermes was updated, but'), result
    assert 'previous version' not in result['message']
    # The fleet the Desktop stopped is still brought back.
    assert argv[-1] == ['gateway', 'start', '--all'], argv


@pytest.mark.platforms('windows')
def test_result_is_atomic_json_carrying_started_at_and_warnings(tmp_path: Path) -> None:
    started = int(time.time()) - 5
    code, out, _, result, home = _handoff(tmp_path, HERMES_UPDATE_STARTED_AT=str(started))
    assert code == 0, out
    assert (result['ok'], result['started_at'], result['warnings']) == (True, started, []), result
    assert not list(home.glob('.hermes-update-result.json.*.tmp'))


@pytest.mark.platforms('windows')
def test_a_result_that_cannot_be_published_leaves_no_tmp_file(tmp_path: Path) -> None:
    # Write-Result's Move/Replace fail (the result path is a directory): its <result>.<pid>.tmp
    # must not be left beside it, one per hand-off, forever.
    code, out, _, result, home = _handoff(
        tmp_path, '-NoGateway', prepare=lambda home, _install: (home / '.hermes-update-result.json').mkdir())
    assert code == 0, out
    assert result is None
    assert not list(home.glob('.hermes-update-result.json.*.tmp')), list(home.iterdir())


@pytest.mark.platforms('windows')
@pytest.mark.parametrize(('receipt', 'ok'), [('interrupted', True), ('partial', True), ('interrupted,another-run', False),
                                             ('failed', False)])
def test_nonzero_exit_after_the_commit_point_is_installed_with_a_followup(tmp_path: Path, receipt: str, ok: bool) -> None:
    # Contract C3: `hermes update` exits 130 / 1 AFTER its commit point (Ctrl-C after the code
    # moved, a parked autostash). Its own receipt (matched by the correlation id) says so: the
    # result must report the update installed with an owed follow-up, never "update failed".
    code, out, argv, result, _ = _handoff(tmp_path, HANDOFF_RECEIPT=receipt, HANDOFF_EXIT='130')
    if ok:
        assert code == 0, out
        assert (result['ok'], result['exit_code'], result['manual']) == (True, 0, True), result
        assert result['message'].startswith('Hermes was updated, but'), result
        assert [w.split(':')[0] for w in result['warnings']] == ['update'], result
        assert argv[-1] == ['gateway', 'start', '--all'], argv   # the post-commit steps still run
    else:
        assert code == 130, out
        assert (result['ok'], result['exit_code']) == (False, 130), result


_CHECKOUT_HOLDER = """
import msvcrt, os, sys, time
from pathlib import Path
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT | os.O_BINARY, 0o644)
os.lseek(fd, 1 << 20, os.SEEK_SET)
msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)   # hermes_cli/update_lock.py::_try_lock's byte
ready, released = map(Path, sys.argv[2:])
ready.touch()
if sys.stdin.readline() != 'release\\n':
    raise SystemExit('missing explicit release command')
os.close(fd)
released.write_text(str(int(time.time())), encoding='utf-8')
"""


def _release_checkout_after_ack(log_path: Path, holder: subprocess.Popen) -> None:
    def acknowledged() -> bool:
        try:
            return 'keeping the update marker' in log_path.read_text(encoding='utf-8-sig')
        except (FileNotFoundError, PermissionError):
            # PowerShell Add-Content may briefly deny concurrent readers.
            return False

    wait_until(acknowledged, timeout=120,
               what='hand-off acknowledged the R6 release wait')
    assert holder.stdin is not None
    holder.stdin.write('release\n')
    holder.stdin.flush()


@pytest.mark.platforms('windows')
def test_result_finished_at_is_stamped_after_the_r6_release_wait(tmp_path: Path) -> None:
    # Keep the real checkout lock until the production R6 acknowledgement, then
    # release it explicitly; log observation stays in the parent, not the lock-holder.
    holders = []
    ready, released = tmp_path / 'holder.ready', tmp_path / 'holder.released'

    def hold_checkout(home: Path, install: Path) -> None:
        holders.append(subprocess.Popen([
            sys.executable, '-c', _CHECKOUT_HOLDER, str(install / '.hermes-update.lock'),
            str(ready), str(released),
        ], stdin=subprocess.PIPE, text=True))
        wait_until(ready.exists, timeout=30, what='checkout holder acquired the real lock')

    def release_after_ack(_handoff_proc: subprocess.Popen) -> None:
        _release_checkout_after_ack(tmp_path / 'home/logs/desktop-update-handoff.log', holders[0])

    try:
        code, out, _, result, home = _handoff(
            tmp_path, '-NoGateway', prepare=hold_checkout, after_spawn=release_after_ack)
    finally:
        for proc in holders:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)
    assert code == 0, out
    log = (home / 'logs/desktop-update-handoff.log').read_text(encoding='utf-8-sig')
    assert 'keeping the update marker' in log, log
    assert released.exists(), log
    assert holders[0].returncode == 0
    assert result is not None, out
    assert result['ok'] is True and result['finished_at'] >= int(released.read_text(encoding='utf-8')), result


@pytest.mark.platforms('windows')
def test_marker_with_a_live_delegate_is_handed_to_it_at_finish(tmp_path: Path) -> None:
    # A7 release rule: the owner deletes its claim unless a delegate still runs; then that
    # delegate becomes the owner (canonical body, started_at kept, no delegate line).
    pid_file = tmp_path / 'delegate.pid'
    try:
        code, out, _, _result, home = _handoff(tmp_path, HANDOFF_DELEGATE=str(pid_file))
        assert code == 0, out
        marker = (home / '.hermes-update-in-progress').read_bytes().decode().split('\n')
        assert marker[0] == pid_file.read_text(encoding='utf-8-sig'), marker
        assert marker[2].startswith('ct:') and marker[3:] == [''], marker
    finally:
        if pid_file.exists():
            subprocess.run(['taskkill', '/F', '/PID', pid_file.read_text(encoding='utf-8-sig')], capture_output=True, check=False)
