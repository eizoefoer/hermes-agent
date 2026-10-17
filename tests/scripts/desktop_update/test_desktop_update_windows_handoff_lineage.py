"""Native Windows handoff lineage contracts; isolated file budget."""
from __future__ import annotations

from tests.scripts.desktop_update.windows_handoff_lineage_contract_support import (
    MARKER,
    Path,
    _old_desktop,
    _wait_for_log,
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
@pytest.mark.parametrize('mode', ['start-b', 'waiting-wrapper'])
def test_r4_old_desktop_wrapper_claim_is_adopted_by_lineage(tmp_path: Path, mode: str) -> None:
    """`start /b` wrapper (exits at once: lineage via the startedAt it was given) and a wrapper
    still alive (lineage via its parent == the Desktop) both hand the claim to the script."""
    home = tmp_path / 'home'; home.mkdir()
    desktop, wrapper, started, desktop_pid = _old_desktop(home, mode)
    try:
        log = _wait_for_log(home, 'hand-off start:')
    finally:
        desktop.kill(); desktop.wait()
    assert 'marker=adopted' in log, log
    script_pid = log.split('hand-off start:')[1].split(' pid=')[1].split()[0]
    lines = (home / MARKER).read_bytes().decode().split('\n')
    assert lines[:2] == [script_pid, str(started)], lines
    assert lines[2].startswith('ct:'), lines
    assert f"desktop pid {desktop_pid}'s launcher pid {wrapper}" in log, log



@pytest.mark.platforms('windows')
def test_r4_old_desktop_lineage_never_adopts_an_unrelated_live_claim(
    tmp_path: Path, sleeper: subprocess.Popen,
) -> None:
    home = tmp_path / 'home'; home.mkdir()
    desktop, _, started, _ = _old_desktop(home, 'start-b', foreign=sleeper.pid)
    try:
        log = _wait_for_log(home, 'exiting without claiming')
    finally:
        desktop.kill(); desktop.wait()
    assert 'hand-off start:' not in log, log
    time.sleep(1)
    assert (home / MARKER).read_bytes().decode() == f'{sleeper.pid}\n{started}\n'



@pytest.mark.platforms('windows')
@pytest.mark.parametrize('env_matches', [True, False], ids=['env-matches', 'env-differs'])
def test_r4_live_wrapper_not_the_desktops_child_is_adopted_only_with_the_handoff_started_at(
    tmp_path: Path, sleeper: subprocess.Popen, env_matches: bool,
) -> None:
    """The lineage table's divergent row (round 5 D11) on real processes: the cmd.exe wrapper is
    our live parent but not the Desktop's child; line 2 == HERMES_UPDATE_STARTED_AT adopts, as
    in bash. Any other startedAt refuses."""
    home = tmp_path / 'home'; home.mkdir()
    desktop, wrapper, started, _ = _old_desktop(home, 'waiting-wrapper', other_desktop=sleeper.pid,
                                                env_skew=0 if env_matches else -7)
    try:
        log = _wait_for_log(home, 'hand-off start:' if env_matches else 'exiting without claiming')
    finally:
        desktop.kill(); desktop.wait()
    if env_matches:
        assert 'marker=adopted' in log, log
        assert f"desktop pid {sleeper.pid}'s launcher pid {wrapper}" in log, log
        lines = (home / MARKER).read_bytes().decode().split('\n')
        assert lines[1] == str(started) and lines[2].startswith('ct:'), lines
    else:
        assert 'hand-off start:' not in log, log
        assert (home / MARKER).read_bytes().decode() == f'{wrapper}\n{started}\n'

