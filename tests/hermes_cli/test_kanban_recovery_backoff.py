"""Failures are signature/head-bound once per native run, not once per tick."""
from pathlib import Path
import pytest
from hermes_cli import kanban_db as kb, kanban_db_connect as kbc, kanban_db_dispatch as kbd


@pytest.fixture
def board(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(kbd, '_profile_exists_fn', lambda: None)
    monkeypatch.setattr(kbd, '_memory_pressure_level', lambda: 'normal')
    with kbc.connect(tmp_path/'board.db') as conn:
        yield conn


def test_spawn_failure_is_backed_off_after_native_component_reopen(board, tmp_path):
    tid = kb.create_task(board, title='fixture', assignee='coder', max_retries=3)
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise RuntimeError('reconcile_logical_turns startup signature')
    policy = {'revision':'a'*40, 'now':1000, 'base_delay':10,'retry_limit':3}
    kbd.dispatch_once(board, spawn_fn=fail, recovery_policy=policy)
    assert len(calls) == 1
    path = board.execute('PRAGMA database_list').fetchone()['file']
    with kbc.connect(Path(path)) as reopened:
        kbd.dispatch_once(reopened, spawn_fn=fail, recovery_policy=policy)
        assert len(calls) == 1
        policy['now'] += 10
        kbd.dispatch_once(reopened, spawn_fn=fail, recovery_policy=policy)
        assert len(calls) == 2
        policy['now'] += 20
        kbd.dispatch_once(reopened, spawn_fn=fail, recovery_policy=policy)
        assert len(calls) == 3
        from hermes_cli.kanban_db_recovery import observe_failures
        receipts = observe_failures(reopened, policy)
        assert receipts[0]['state'] == 'exhausted'
        assert receipts[0]['task_id'] == tid
        assert kb.get_task(reopened, tid).status == 'blocked'
        assert observe_failures(reopened, policy) == receipts


def test_revision_change_does_not_override_native_block_or_approval(board):
    from hermes_cli.kanban_db_recovery import observe_failures
    tid = kb.create_task(board, title='authority', assignee='coder')
    kb.block_task(board, tid, reason='approval required', kind='needs_input')
    assert observe_failures(board, {'revision':'b'*40,'now':1000}) == []
    result = kbd.dispatch_once(board, spawn_fn=lambda *_: pytest.fail('authority bypass'),
                               recovery_policy={'revision':'b'*40,'now':1000})
    assert result.spawned == []
    assert kb.get_task(board, tid).status == 'blocked'


def test_task_head_is_recorded_per_run_and_new_head_does_not_reuse_old_backoff(board,tmp_path):
    import subprocess
    repo=tmp_path/'source'
    repo.mkdir()
    for args in (['init','-b','main'],['config','user.email','fixture@example.invalid'],['config','user.name','Fixture']):
        subprocess.run(['git',*args],cwd=repo,check=True,capture_output=True)
    (repo/'marker').write_text('before')
    subprocess.run(['git','add','.'],cwd=repo,check=True,capture_output=True)
    subprocess.run(['git','commit','-m','before'],cwd=repo,check=True,capture_output=True)
    first_sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
    tid=kb.create_task(board,title='source-bound',assignee='coder',workspace_kind='dir',workspace_path=str(repo),max_retries=5)
    calls=[]
    def fail(*args,**kwargs):
        calls.append(1)
        raise RuntimeError('same signature')
    policy={'revision':'a'*40,'now':1000,'base_delay':60,'retry_limit':3}
    first=kbd.dispatch_once(board,spawn_fn=fail,recovery_policy=policy)
    assert first.recovery_observations[0]['task_id'] == tid
    assert first.recovery_observations[0]['revision'] == first_sha
    kbd.dispatch_once(board,spawn_fn=fail,recovery_policy=policy)
    assert len(calls)==1
    (repo/'marker').write_text('reviewed repair')
    subprocess.run(['git','add','.'],cwd=repo,check=True,capture_output=True)
    subprocess.run(['git','commit','-m','repair'],cwd=repo,check=True,capture_output=True)
    second_sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
    second=kbd.dispatch_once(board,spawn_fn=fail,recovery_policy=policy)
    assert len(calls)==2
    assert second.recovery_observations[0]['revision'] == second_sha
    assert second.recovery_observations[0]['attempts']==1


def test_spawn_callback_failure_does_not_repeat_admission_within_one_tick(board):
    kb.create_task(board,title='single admission',assignee='coder',max_retries=3)
    calls=[]
    def admitted_then_failed(*args,**kwargs):
        calls.append(1)
        raise ValueError('admitted process; receipt failure')
    kbd.dispatch_once(board,spawn_fn=admitted_then_failed,recovery_policy={'revision':'a'*40,'now':1000})
    assert calls == [1]
