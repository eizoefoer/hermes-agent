"""Recovery additions preserve dispatcher authority and read-only diagnostics."""
import pytest
from hermes_cli import kanban_db as kb, kanban_db_connect as kbc, kanban_db_dispatch as kbd


@pytest.fixture
def board(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(kbd, '_profile_exists_fn', lambda: None)
    monkeypatch.setattr(kbd, '_memory_pressure_level', lambda: 'normal')
    with kbc.connect(tmp_path / 'board.db') as conn:
        yield conn


def test_recovery_dry_run_does_not_create_tables_or_events(board):
    kb.create_task(board, title='dry fixture', assignee='coder')
    before = '\n'.join(board.iterdump())
    kbd.dispatch_once(board, dry_run=True, recovery_policy={'revision': 'a' * 40, 'now': 1000})
    assert '\n'.join(board.iterdump()) == before


def test_source_lookup_failure_releases_claim_and_records_failure(board, monkeypatch):
    from hermes_cli import kanban_db_recovery as recovery
    tid = kb.create_task(board, title='missing source', assignee='coder', max_retries=3)
    def unavailable(*args):
        raise ValueError('source unavailable fixture')
    monkeypatch.setattr(recovery, '_task_source_revision', unavailable)
    result = kbd.dispatch_once(board, spawn_fn=lambda *_: pytest.fail('must not spawn'),
                               recovery_policy={'revision': 'a' * 40, 'now': 1000})
    task = kb.get_task(board, tid)
    assert task.status != 'running'
    assert task.claim_lock is None
    assert result.spawned == []
    run = board.execute('SELECT outcome,error FROM task_runs WHERE task_id=?', (tid,)).fetchone()
    assert run['outcome'] == 'spawn_failed'
    assert run['error'] == 'source unavailable fixture'


def test_stale_live_worker_is_not_signalled_or_reclaimed(board, monkeypatch):
    import time
    tid = kb.create_task(board, title='slow live fixture', assignee='coder')
    claim = kb.claim_task(board, tid)
    assert claim is not None
    with kb.write_txn(board):
        board.execute('UPDATE tasks SET worker_pid=?, worker_started_at=?, started_at=?, last_heartbeat_at=NULL WHERE id=?',
                      (12345, 'fixture-identity', int(time.time()) - 10000, tid))
        board.execute('UPDATE task_runs SET started_at=? WHERE task_id=?', (int(time.time()) - 10000, tid))
    monkeypatch.setattr(kbd, '_worker_alive', lambda pid, started: pid == 12345 and started == 'fixture-identity')
    def no_termination(*args, **kwargs):
        pytest.fail('heartbeat age must not authorize termination')
    monkeypatch.setattr(kb, '_terminate_reclaimed_worker', no_termination)
    assert kbd.detect_stale_running(board, stale_timeout_seconds=1) == []
    assert kb.get_task(board, tid).status == 'running'
