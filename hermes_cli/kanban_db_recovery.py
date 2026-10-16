"""Additive retry receipts for native runs, keyed by signature and source SHA.

Tasks/runs/claims remain authoritative; this module neither creates cards nor
lifts approval, provider, dependency, review or active-PR guards.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import sqlite3
import subprocess
import time


TABLE = '''CREATE TABLE IF NOT EXISTS task_recovery_attempts(
 task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE, revision TEXT NOT NULL,
 signature TEXT NOT NULL, attempts INTEGER NOT NULL, last_run_id INTEGER NOT NULL,
 due_at REAL NOT NULL, state TEXT NOT NULL)'''
FAILURES = ('spawn_failed', 'crashed', 'stale', 'timed_out', 'rate_limited', 'gave_up')
SOURCES = '''CREATE TABLE IF NOT EXISTS task_recovery_sources(
 run_id INTEGER PRIMARY KEY REFERENCES task_runs(id) ON DELETE CASCADE,
 revision TEXT NOT NULL)'''


def _task_source_revision(conn, task_id, runtime_revision):
    row = conn.execute('SELECT workspace_path,workspace_kind FROM tasks WHERE id=?',(task_id,)).fetchone()
    path = Path(row['workspace_path']) if row and row['workspace_path'] else None
    if path is not None and (path/'.git').exists():
        revision = subprocess.check_output(['git','-C',str(path),'rev-parse','HEAD'],
                                           text=True,encoding='utf-8',errors='replace',timeout=5).strip()
        if not re.fullmatch('[0-9a-f]{40}',revision):
            raise ValueError('task source HEAD is unavailable')
        return revision
    if path is not None and row['workspace_kind'] == 'worktree':
        raise ValueError('task worktree HEAD unavailable; route existing source repair before admission')
    return runtime_revision


def capture_run_source(conn, task_id, run_id, policy):
    if policy is None:
        return
    from hermes_cli import kanban_db as kb
    revision = _task_source_revision(conn,task_id,policy['revision'])
    with kb.write_txn(conn):
        conn.execute(SOURCES)
        cur = conn.execute('INSERT OR IGNORE INTO task_recovery_sources SELECT ?,? FROM tasks '
                           "WHERE id=? AND current_run_id=? AND status='running'",
                           (run_id,revision,task_id,run_id))
        if cur.rowcount != 1:
            raise ValueError('native run ownership changed before source admission')


def observe_failures(conn: sqlite3.Connection, policy: dict) -> list[dict]:
    from hermes_cli import kanban_db as kb
    revision = str(policy.get('revision') or '')
    if not re.fullmatch(r'[0-9a-f]{40}(?::[0-9a-f]{40})?', revision):
        raise ValueError('exact recovery source revision is required')
    now = float(policy.get('now', time.time()))
    base = max(1, int(policy.get('base_delay', 30)))
    limit = max(1, int(policy.get('retry_limit', 3)))
    with kb.write_txn(conn):
        conn.execute(TABLE)
        conn.execute(SOURCES)
        # Completed/archived cards retain their run/event evidence, not an
        # outstanding retry obligation that the external bridge would re-route.
        conn.execute("DELETE FROM task_recovery_attempts WHERE task_id IN "
                     "(SELECT id FROM tasks WHERE status IN ('done','archived'))")
        rows = conn.execute(
            "SELECT t.id AS task_id,t.status,t.max_retries,t.consecutive_failures,r.id,r.outcome,r.error,r.metadata "
            ",s.revision AS admitted_revision FROM tasks t JOIN task_runs r ON r.id=(SELECT MAX(id) FROM task_runs "
            "WHERE task_id=t.id AND ended_at IS NOT NULL) "
            "LEFT JOIN task_recovery_sources s ON s.run_id=r.id "
            "WHERE t.status IN ('ready','review','blocked','running')"
        ).fetchall()
        for row in rows:
            if row['outcome'] not in FAILURES:
                conn.execute('DELETE FROM task_recovery_attempts WHERE task_id=?', (row['task_id'],))
                continue
            if row['status'] == 'running':
                continue
            revision = row['admitted_revision'] or str(policy['revision'])
            text = re.sub(r'\b\d{4,}\b', '<number>', str(row['error'] or row['outcome']))
            outcome = kb._json_dict(row['metadata']).get('trigger_outcome',row['outcome'])
            signature = hashlib.sha256((outcome + ':' + text).encode()).hexdigest()
            prior = conn.execute('SELECT * FROM task_recovery_attempts WHERE task_id=?',
                                 (row['task_id'],)).fetchone()
            same = prior is not None and (prior['revision'],prior['signature']) == (revision,signature)
            if same and prior['last_run_id'] == row['id']:
                continue
            count = int(prior['attempts']) + 1 if same else 1
            task_limit = row['max_retries'] if row['max_retries'] is not None else limit
            # Respect the stricter existing native breaker, never lift its blocked card.
            exhausted = count >= min(limit,int(task_limit)) or row['status'] == 'blocked'
            state = 'exhausted' if exhausted else 'backoff'
            conn.execute('INSERT OR REPLACE INTO task_recovery_attempts VALUES(?,?,?,?,?,?,?)',
                         (row['task_id'],revision,signature,count,row['id'],
                          now + min(base * 2 ** (count-1),3600),state))
            kb._append_event(conn,row['task_id'],'recovery_retry_receipt',
                             {'revision':revision,'signature':signature,'attempts':count,'state':state},
                             run_id=row['id'])
        return [dict(row) for row in conn.execute('SELECT * FROM task_recovery_attempts ORDER BY task_id')]


def admission_guard(conn: sqlite3.Connection, task_id: str, policy: dict | None) -> str | None:
    if policy is None:
        return None
    row = conn.execute('SELECT * FROM task_recovery_attempts WHERE task_id=?', (task_id,)).fetchone()
    if row is None:
        return None
    current = _task_source_revision(conn,task_id,policy['revision'])
    if current != row['revision']:
        # A real source repair gets a fresh signature/head budget. Native
        # block/approval/dependency/active-PR gates still apply unchanged.
        return None
    if row['state'] == 'exhausted':
        return 'recovery_exhausted'
    if row['due_at'] > float(policy.get('now',time.time())):
        return 'recovery_backoff'
    return None


def detect_stale_running(conn: sqlite3.Connection, *, stale_timeout_seconds: int = 0,
                         signal_fn=None) -> list[str]:
    """A stale heartbeat requests a checkpoint; only proven dead owners are reclaimed.

    This is the native sweep's internal sibling, not an external task ledger.
    Signal authority is never inferred from heartbeat age.
    """
    from hermes_cli import kanban_db as kb, kanban_db_dispatch as dispatch
    if stale_timeout_seconds <= 0:
        return []
    now = int(time.time())
    reclaimed = []
    rows = conn.execute(
        "SELECT t.id,t.worker_pid,t.worker_started_at,t.current_run_id,t.last_heartbeat_at,t.claim_lock,"
        " COALESCE(r.started_at,t.started_at) AS active_started_at FROM tasks t "
        "LEFT JOIN task_runs r ON r.id=t.current_run_id WHERE t.status='running'"
    ).fetchall()
    for row in rows:
        start = kb._to_epoch(row['active_started_at'])
        if start is None or now-start < stale_timeout_seconds:
            continue
        last_hb = kb._to_epoch(row['last_heartbeat_at'])
        hb_age = now-last_hb if last_hb is not None else None
        if hb_age is not None and hb_age < 3600:
            continue
        pid,tid,lock = row['worker_pid'],row['id'],row['claim_lock'] or ''
        if not lock.startswith(kb._host_prefix()):
            continue
        fingerprint = row['worker_started_at']
        if dispatch._worker_alive(pid,fingerprint):
            dispatch._defer_reclaim_for_live_worker_with_cooperative_steering(
                conn,tid,lock,now,reason='heartbeat_stale_worker_alive_cooperative_steering',
                elapsed_seconds=now-start,heartbeat_age_seconds=kb._opt_int(hb_age),
                timeout_seconds=stale_timeout_seconds)
            continue
        if dispatch._worker_alive(pid,fingerprint):
            continue
        with kb.write_txn(conn):
            retry_status = kb._retry_status_for_run(conn,tid)
            cur = conn.execute(
                "UPDATE tasks SET status=?,claim_lock=NULL,claim_expires=NULL,worker_pid=NULL,"
                "worker_started_at=NULL,last_heartbeat_at=NULL WHERE id=? AND status='running' "
                "AND claim_lock IS ? AND worker_pid IS ? AND worker_started_at IS ? AND current_run_id IS ?",
                (retry_status,tid,row['claim_lock'],pid,fingerprint,row['current_run_id']))
            if cur.rowcount != 1:
                continue
            payload = {'elapsed_seconds':now-start,'last_heartbeat_at':kb._opt_int(last_hb),
                       'heartbeat_age_seconds':kb._opt_int(hb_age),'timeout_seconds':stale_timeout_seconds,
                       'pid':pid,'retry_status':retry_status,'host_local':True,
                       'termination_attempted':False,'terminated':True,'pid_recycled':bool(kb._pid_alive(pid))}
            error = (f'no heartbeat for {int(hb_age)}s' if hb_age is not None else 'no heartbeat ever')
            run_id = kb._end_run(conn,tid,outcome='stale',status='stale',error=error,
                                 metadata=payload)
            kb._append_event(conn,tid,'stale',payload,run_id=run_id)
            reclaimed.append(tid)
    return reclaimed
