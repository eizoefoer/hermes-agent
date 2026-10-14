"""Heartbeat staleness never grants signal authority; reclaim binds the full owner."""
import os

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd


@pytest.fixture
def board(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    with kbc.connect(tmp_path / "kanban.db") as conn:
        yield conn


def stale_worker(conn, fingerprint, now=100_000):
    tid = kb.create_task(conn, title="recovery", assignee="worker")
    kb.claim_task(conn, tid)
    kbd._set_worker_pid(conn, tid, os.getpid())
    with kb.write_txn(conn):
        conn.execute("UPDATE tasks SET worker_started_at=?, started_at=?, last_heartbeat_at=? WHERE id=?",
                     (fingerprint, now - 20_000, now - 10_000, tid))
        conn.execute("UPDATE task_runs SET started_at=? WHERE task_id=?", (now - 20_000, tid))
    return tid


def test_stale_boot_uuid_recycle_reclaims_once_without_signal(board, monkeypatch):
    monkeypatch.setattr(kbd.time, "time", lambda: 100_000)
    actual = kbd._process_fingerprint(os.getpid())
    tid = stale_worker(board, "other-boot:0|" + actual.split("|", 1)[1])
    signals = []
    assert kbd.detect_stale_running(board, stale_timeout_seconds=100,
                                    signal_fn=lambda *args: signals.append(args)) == [tid]
    assert signals == []
    assert kb.get_task(board, tid).status == "ready"
    assert kbd.detect_stale_running(board, stale_timeout_seconds=100) == []


@pytest.mark.parametrize("fingerprint", [None, "unverified", "matching"])
def test_live_long_worker_is_held_with_one_cooperative_notice(board, monkeypatch, fingerprint):
    monkeypatch.setattr(kbd.time, "time", lambda: 100_000)
    if fingerprint == "matching":
        fingerprint = kbd._process_fingerprint(os.getpid())
    tid = stale_worker(board, fingerprint)
    before = kb.get_task(board, tid)
    signals = []
    for _ in range(2):
        assert kbd.detect_stale_running(board, stale_timeout_seconds=100,
                                        signal_fn=lambda *args: signals.append(args)) == []
    after = kb.get_task(board, tid)
    assert (after.status, after.worker_pid, after.current_run_id) == (
        before.status, before.worker_pid, before.current_run_id)
    assert signals == []
    assert sum(e.kind == "cooperative_steering_applied" for e in kb.list_events(board, tid)) == 1


def test_stale_snapshot_cannot_reclaim_changed_pid_under_same_lock(board, monkeypatch):
    monkeypatch.setattr(kbd.time, "time", lambda: 100_000)
    tid = stale_worker(board, "other-boot:0|1")
    def changed_owner(pid, started_at):
        with kb.write_txn(board):
            board.execute("UPDATE tasks SET worker_pid=?, worker_started_at=? WHERE id=?",
                          (pid + 1, "new-owner:0|2", tid))
        return False
    monkeypatch.setattr(kbd, "_worker_alive", changed_owner)
    assert kbd.detect_stale_running(board, stale_timeout_seconds=100) == []
    assert kb.get_task(board, tid).status == "running"


def test_foreign_host_stale_claim_is_not_local_pid_evidence(board, monkeypatch):
    monkeypatch.setattr(kbd.time, "time", lambda: 100_000)
    tid = stale_worker(board, "other-boot:0|1")
    with kb.write_txn(board):
        board.execute("UPDATE tasks SET claim_lock='foreign-host:worker' WHERE id=?", (tid,))
    assert kbd.detect_stale_running(board, stale_timeout_seconds=100) == []
    assert kb.get_task(board, tid).status == "running"
