"""Native Windows marker bodies contracts; isolated file budget."""
from __future__ import annotations

from tests.scripts.desktop_update.windows_marker_contract_support import (
    MARKER,
    Path,
    _BODIES,
    _creation_time,
    _protected_pid,
    _run,
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
@pytest.mark.parametrize('ct_matches', [True, False])
def test_owner_creation_time_is_read_for_system_processes(tmp_path: Path, ct_matches: bool) -> None:
    """A1: a marker naming a SYSTEM/protected pid (a reused pid after a reboot) is judged by its
    real creation time, not treated as live forever because StartTime is access-denied."""
    owner = _protected_pid()
    ct = _creation_time(owner) if ct_matches else '1000.000'
    body = f'{owner}\n{int(time.time())}\nct:{ct}\n'.encode()
    (tmp_path / MARKER).write_bytes(body)
    pid, code, out = _run(tmp_path, '-SelfTestMarker', '-NoMarkerCleanup')
    if ct_matches:
        assert code == 2, out
        assert (tmp_path / MARKER).read_bytes() == body
    else:
        assert code == 0, out
        assert (tmp_path / MARKER).read_bytes().decode().split('\n')[0] == str(pid)



@pytest.mark.platforms('windows')
@pytest.mark.parametrize('case', sorted(_BODIES))
def test_marker_bodies_are_parsed_positionally_like_every_other_reader(
    tmp_path: Path, sleeper: subprocess.Popen, case: str,
) -> None:
    template, verdict = _BODIES[case]
    now = int(time.time())
    body = template.format(pid=sleeper.pid, now=now, old=now - 1300, ct=_creation_time(sleeper.pid)).encode()
    (tmp_path / MARKER).write_bytes(body)
    pid, code, out = _run(tmp_path, '-SelfTestMarker', '-NoMarkerCleanup')
    if verdict == 'live':
        assert code == 2, out
        assert (tmp_path / MARKER).read_bytes() == body
    else:
        assert code == 0, out
        assert (tmp_path / MARKER).read_bytes().decode().split('\n')[0] == str(pid)

