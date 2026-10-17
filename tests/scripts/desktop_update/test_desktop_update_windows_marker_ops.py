"""Native Windows marker ops contracts; isolated file budget."""
from __future__ import annotations

from tests.scripts.desktop_update.windows_marker_ops_contract_support import (
    MARKER,
    Path,
    _HeldLock,
    _creation_time,
    _dead_pid,
    _op,
    pytest,
    subprocess,
    sys,
    time,
)


@pytest.fixture
def sleeper():
    proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'])
    yield proc
    proc.kill()
    proc.wait()



@pytest.mark.platforms('windows')
def test_marker_op_reclaim(tmp_path: Path, sleeper: subprocess.Popen) -> None:
    marker = tmp_path / MARKER
    assert _op(tmp_path, '-MarkerOp', 'reclaim')[:2] == (0, 'absent\n')
    marker.write_bytes(f'{_dead_pid()}\n{int(time.time())}\nct:5.000\n'.encode())
    assert _op(tmp_path, '-MarkerOp', 'reclaim')[:2] == (0, 'reclaimed\n')
    assert not marker.exists()
    live = f'{sleeper.pid}\n{int(time.time())}\nct:{_creation_time(sleeper.pid)}\n'.encode()
    marker.write_bytes(live)
    assert _op(tmp_path, '-MarkerOp', 'reclaim')[:2] == (0, f'live {sleeper.pid}\n')
    assert marker.read_bytes() == live
    assert (tmp_path / (MARKER + '.lock')).exists()   # the sidecar is never deleted
    assert _op(tmp_path, '-MarkerOp', 'nonsense')[0] == 64
    assert _op(tmp_path, '-MarkerOp', 'withdraw')[0] == 64



@pytest.mark.platforms('windows')
def test_marker_op_reports_busy_while_the_lock_is_held(tmp_path: Path) -> None:
    dead = f'{_dead_pid()}\n{int(time.time())}\nct:5.000\n'.encode()
    (tmp_path / MARKER).write_bytes(dead)
    lock = _HeldLock(tmp_path)
    try:
        assert _op(tmp_path, '-MarkerOp', 'reclaim')[:2] == (0, 'busy\n')
    finally:
        lock.release()
    assert (tmp_path / MARKER).read_bytes() == dead

