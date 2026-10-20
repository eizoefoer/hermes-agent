"""Cooperative marker ownership; not gateway settlement or activation proof."""
import contextlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

from gateway import drain_control as dc

pytestmark = pytest.mark.platforms("posix")


def child_mutation(home, operation):
    script = '''import sys
from pathlib import Path
from gateway import drain_control as dc
try:
    if sys.argv[2] == 'clear': dc.clear_drain_request(home=Path(sys.argv[1]))
    else: dc.write_drain_request(home=Path(sys.argv[1]), principal='competitor')
except BlockingIOError:
    print('refused')
else:
    print('mutated')
'''
    result = subprocess.run([sys.executable, '-c', script, str(home), operation],
                            capture_output=True, text=True, timeout=10,
                            cwd=Path(dc.__file__).resolve().parents[1],
                            env=dict(os.environ, HOME=str(home), HERMES_HOME=str(home)))
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.mark.parametrize('operation', ['clear', 'write'])
def test_held_scope_excludes_native_mutators_in_same_and_other_process(tmp_path, operation):
    home = tmp_path / 'home'
    home.mkdir()
    # The baseline fallback permits executing the actual unguarded mutation in
    # RED, instead of failing merely because the additive API does not exist.
    scope = getattr(dc, 'drain_marker_ownership', lambda **kw: contextlib.nullcontext(None))
    with scope(home=home) as ownership:
        options = {} if ownership is None else {'ownership': ownership}
        marker = dc.write_drain_request(home=home, principal='transaction', **options)
        assert child_mutation(home, operation) == 'refused'
        with pytest.raises(BlockingIOError):
            if operation == 'clear':
                dc.clear_drain_request(home=home)
            else:
                dc.write_drain_request(home=home)
        assert dc.read_drain_request(home=home) == marker
    # Leaving scope does not silently cancel an unknown transaction.
    assert dc.read_drain_request(home=home) == marker
    assert dc.clear_drain_request(home=home)


def test_explicit_capability_cannot_escape_home_or_outlive_scope(tmp_path):
    home = tmp_path / 'home'
    other = tmp_path / 'other'
    with dc.drain_marker_ownership(home=home) as ownership:
        marker = dc.write_drain_request(home=home, ownership=ownership)
        with pytest.raises(ValueError):
            dc.write_drain_request(home=other, ownership=ownership)
        assert not other.exists()
    with pytest.raises(ValueError):
        dc.clear_drain_request(home=home, ownership=ownership)
    assert dc.read_drain_request(home=home) == marker


def test_exception_preserves_marker_and_releases_scope(tmp_path):
    with pytest.raises(RuntimeError, match='unknown effect'):
        with dc.drain_marker_ownership(home=tmp_path) as ownership:
            dc.write_drain_request(home=tmp_path, ownership=ownership)
            raise RuntimeError('unknown effect')
    assert dc.drain_requested(home=tmp_path)
    assert child_mutation(tmp_path, 'clear') == 'mutated'


def test_process_exit_releases_lock_without_clearing_marker(tmp_path):
    script = '''import os,sys
from pathlib import Path
from gateway import drain_control as dc
with dc.drain_marker_ownership(home=Path(sys.argv[1])) as ownership:
    dc.write_drain_request(home=Path(sys.argv[1]), ownership=ownership)
    os._exit(0)
'''
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path)],
                            capture_output=True, text=True, timeout=10,
                            cwd=Path(dc.__file__).resolve().parents[1],
                            env=dict(os.environ, HOME=str(tmp_path), HERMES_HOME=str(tmp_path)))
    assert result.returncode == 0, result.stderr
    assert dc.drain_requested(home=tmp_path)
    assert child_mutation(tmp_path, 'clear') == 'mutated'
