"""Portable pipe protocol for the Windows R6 fixture's parent controller."""
import subprocess
import sys
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.scripts.desktop_update import test_desktop_update_windows_commit_outcome as fixture


@pytest.mark.parametrize('error', [PermissionError, FileNotFoundError])
def test_ack_poll_retries_transient_log_open_failure(tmp_path, monkeypatch, error):
    log = tmp_path / 'handoff.log'
    log.write_text('keeping the update marker', encoding='utf-8')
    read = Path.read_text
    attempts = []
    holder = SimpleNamespace(stdin=StringIO())

    def transient(path, *args, **kwargs):
        if path == log:
            attempts.append(path)
            if len(attempts) == 1:
                raise error('writer holds the handoff log')
            assert holder.stdin.getvalue() == ''
        return read(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', transient)
    fixture._release_checkout_after_ack(log, holder)
    assert len(attempts) == 2
    assert holder.stdin.getvalue() == 'release\n'


def test_ack_poll_remains_bounded_when_log_stays_locked(tmp_path, monkeypatch):
    from tests.waiting import wait_until

    log = tmp_path / 'handoff.log'
    log.touch()
    holder = SimpleNamespace(stdin=StringIO())
    read = Path.read_text

    def locked(path, *args, **kwargs):
        if path == log:
            raise PermissionError('writer still holds the log')
        return read(path, *args, **kwargs)

    def bounded(predicate, **kwargs):
        assert kwargs['timeout'] == 120
        return wait_until(predicate, timeout=0.02, interval=0.001, what=kwargs['what'])

    monkeypatch.setattr(Path, 'read_text', locked)
    monkeypatch.setattr(fixture, 'wait_until', bounded)
    with pytest.raises(AssertionError, match='R6 release wait'):
        fixture._release_checkout_after_ack(log, holder)
    assert holder.stdin.getvalue() == ''


def test_ack_poll_preserves_other_io_errors(tmp_path, monkeypatch):
    log = tmp_path / 'handoff.log'
    log.touch()
    holder = SimpleNamespace(stdin=StringIO())
    read = Path.read_text

    def broken(path, *args, **kwargs):
        if path == log:
            raise OSError('unexpected device failure')
        return read(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', broken)
    with pytest.raises(OSError, match='unexpected device failure'):
        fixture._release_checkout_after_ack(log, holder)
    assert holder.stdin.getvalue() == ''

def test_acknowledged_holder_receives_complete_release_line(tmp_path):
    log = tmp_path / 'handoff.log'
    log.write_text('keeping the update marker', encoding='utf-8')
    holder = subprocess.Popen(
        [sys.executable, '-c',
         "import sys; raise SystemExit(0 if sys.stdin.readline() == 'release\\n' else 3)"],
        stdin=subprocess.PIPE, text=True,
    )
    try:
        fixture._release_checkout_after_ack(log, holder)
        assert holder.wait(timeout=5) == 0
    finally:
        if holder.poll() is None:
            holder.kill()
        holder.wait(timeout=5)
        assert holder.stdin is not None
        holder.stdin.close()


def test_handoff_preserves_callback_error_when_cleanup_also_times_out(tmp_path, monkeypatch):
    from unittest.mock import Mock

    def publish(install, _cli):
        (install / 'hermes_cli').mkdir(parents=True)

    process = Mock(pid=12345, stdout=StringIO())
    process.poll.return_value = None
    process.communicate.side_effect = subprocess.TimeoutExpired('fixture-child', 10)
    monkeypatch.setattr(fixture, 'publish_fixture_launcher', publish)
    monkeypatch.setattr(fixture.subprocess, 'Popen', lambda *args, **kwargs: process)
    cleanup = Mock(side_effect=subprocess.TimeoutExpired('fixture-cleanup', 10))
    monkeypatch.setattr(fixture.subprocess, 'run', cleanup)
    original = RuntimeError('R6 acknowledgement missing')

    def fail_callback(_process):
        raise original

    with pytest.raises(RuntimeError, match='R6 acknowledgement missing') as caught:
        fixture._handoff(tmp_path, after_spawn=fail_callback)
    assert caught.value is original
    assert 'tree cleanup failed' in str(original.__notes__)
    assert 'output cleanup failed' in str(original.__notes__)
    cleanup.assert_called_once()
    process.kill.assert_called_once()
    process.communicate.assert_called_once_with(timeout=10)
    assert process.stdout.closed
