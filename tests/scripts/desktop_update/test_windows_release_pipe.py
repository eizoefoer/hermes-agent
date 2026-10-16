"""Portable pipe protocol for the Windows R6 fixture's parent controller."""
import subprocess
import sys

from tests.scripts.desktop_update.test_desktop_update_windows_commit_outcome import (
    _release_checkout_after_ack,
)


def test_acknowledged_holder_receives_complete_release_line(tmp_path):
    log = tmp_path / 'handoff.log'
    log.write_text('keeping the update marker', encoding='utf-8')
    holder = subprocess.Popen(
        [sys.executable, '-c',
         "import sys; raise SystemExit(0 if sys.stdin.readline() == 'release\\n' else 3)"],
        stdin=subprocess.PIPE, text=True,
    )
    try:
        _release_checkout_after_ack(log, holder)
        assert holder.wait(timeout=5) == 0
    finally:
        if holder.poll() is None:
            holder.kill()
        holder.wait(timeout=5)
        assert holder.stdin is not None
        holder.stdin.close()


def test_handoff_preserves_callback_error_when_cleanup_also_times_out(tmp_path, monkeypatch):
    from io import StringIO
    from unittest.mock import Mock

    import pytest
    from tests.scripts.desktop_update import test_desktop_update_windows_commit_outcome as fixture

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
