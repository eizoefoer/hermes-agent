"""Native Windows marker contracts; isolated file budget."""
from __future__ import annotations

from tests.scripts.desktop_update.windows_marker_contract_support import (
    MARKER,
    Path,
    _creation_time,
    _dead_pid,
    _result,
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
def test_claim_is_the_first_action_and_writes_a_v2_body(tmp_path: Path) -> None:
    pid, code, out = _run(tmp_path, '-SelfTestMarker', '-NoMarkerCleanup')
    assert code == 0, out
    lines = (tmp_path / MARKER).read_bytes().decode().split('\n')
    assert lines[0] == str(pid)
    assert lines[2].startswith('ct:') and len(lines[2].split('.')[-1]) == 3, lines
    log = (tmp_path / 'logs/desktop-update-handoff.log').read_text(encoding='utf-8-sig').splitlines()
    # Nothing (UI, Add-Type'd console helpers, probes) logs before the claim.
    assert 'claimed update marker' in log[0], log



@pytest.mark.platforms('windows')
@pytest.mark.parametrize('ct_matches', [True, False])
def test_live_foreign_owner_refuses_and_reused_pid_is_reclaimed(
    tmp_path: Path, sleeper: subprocess.Popen, ct_matches: bool,
) -> None:
    ct = _creation_time(sleeper.pid) if ct_matches else '1000.000'   # wrong ct = pid reuse
    body = f'{sleeper.pid}\n{int(time.time())}\nct:{ct}\n'.encode()
    (tmp_path / MARKER).write_bytes(body)
    pid, code, out = _run(tmp_path, '-SelfTestMarker', '-NoMarkerCleanup')
    if ct_matches:
        assert code == 2, out
        assert (tmp_path / MARKER).read_bytes() == body
        # A refused run changed nothing and owns no result (A4): the other update reports.
        assert not (tmp_path / '.hermes-update-result.json').exists()
    else:
        assert code == 0, out
        lines = (tmp_path / MARKER).read_bytes().decode().split('\n')
        assert lines[0] == str(pid) and lines[2].startswith('ct:'), lines



@pytest.mark.platforms('windows')
@pytest.mark.parametrize('bridge', ['absent', 'someone-else'])
def test_desktop_started_handoff_only_adopts_its_bridge(
    tmp_path: Path, sleeper: subprocess.Popen, bridge: str,
) -> None:
    """A4: the Desktop gave up on a late script: it must not claim fresh, run, or leave a result."""
    other = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'])
    try:
        body = f'{other.pid}\n{int(time.time())}\nct:{_creation_time(other.pid)}\n'.encode()
        if bridge == 'someone-else':
            (tmp_path / MARKER).write_bytes(body)
        _, code, out = _run(tmp_path, '-DesktopPid', str(sleeper.pid))
    finally:
        other.kill(); other.wait()
    assert code == 2, out
    assert not (tmp_path / '.hermes-update-result.json').exists()
    if bridge == 'absent':
        assert not (tmp_path / MARKER).exists()
    else:
        assert (tmp_path / MARKER).read_bytes() == body



@pytest.mark.platforms('windows')
def test_live_desktop_bridge_marker_is_adopted_keeping_its_started_at(
    tmp_path: Path, sleeper: subprocess.Popen,
) -> None:
    started = int(time.time()) - 30
    (tmp_path / MARKER).write_bytes(f'{sleeper.pid}\n{started}\nct:{_creation_time(sleeper.pid)}\n'.encode())
    pid, code, out = _run(tmp_path, '-SelfTestMarker', '-NoMarkerCleanup', '-DesktopPid', str(sleeper.pid))
    assert code == 0, out
    lines = (tmp_path / MARKER).read_bytes().decode().split('\n')
    assert lines[:2] == [str(pid), str(started)], lines
    assert lines[2].startswith('ct:'), lines
    assert _result(tmp_path)['started_at'] == started



@pytest.mark.platforms('windows')
def test_dead_owner_marker_is_reclaimed_and_released(tmp_path: Path) -> None:
    (tmp_path / MARKER).write_bytes(f'{_dead_pid()}\n{int(time.time())}\nct:5.000\n'.encode())
    pid, code, out = _run(tmp_path, '-SelfTestMarker')
    assert code == 0, out
    assert not (tmp_path / MARKER).exists()
    log = (tmp_path / 'logs/desktop-update-handoff.log').read_text(encoding='utf-8-sig')
    assert f'claimed update marker (pid {pid})' in log
    assert 'removed update marker (owned)' in log

