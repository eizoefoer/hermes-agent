"""Sparse progress uses the native event ledger, one schedule per active run."""
from __future__ import annotations

import json
import sqlite3
import time


PROGRESS_KIND = "progress"


def enqueue_due_progress(conn: sqlite3.Connection, task_id: str) -> None:
    """Enqueue at most one due update; concurrent ticks serialize on the board DB.

    Intervals expand 10 → 20 → 40 minutes, then stay at 40. Late ticks emit
    one current update, not a catch-up burst. The event is both the schedule
    checkpoint and the normal notifier outbox; no live-schema surgery needed.
    """
    from hermes_cli import kanban_db as kb

    now = int(time.time())
    with kb.write_txn(conn):
        run = conn.execute(
            "SELECT r.id, r.started_at FROM tasks t JOIN task_runs r ON r.id = t.current_run_id "
            "WHERE t.id = ? AND t.status = 'running' AND r.status = 'running' AND r.ended_at IS NULL",
            (task_id,),
        ).fetchone()
        if run is None:
            return
        last = conn.execute(
            "SELECT payload FROM task_events WHERE task_id = ? AND run_id = ? AND kind = ? ORDER BY id DESC LIMIT 1",
            (task_id, run["id"], PROGRESS_KIND),
        ).fetchone()
        previous = json.loads(last["payload"]) if last else {}
        due = previous.get("next_due_at", int(run["started_at"]) + 600)
        if now < due:
            return
        interval = min(int(previous.get("interval_seconds", 300)) * 2, 2400)
        kb._append_event(conn, task_id, PROGRESS_KIND, {
            "elapsed_minutes": max(0, (now - int(run["started_at"])) // 60),
            "interval_seconds": interval, "next_due_at": now + min(interval * 2, 2400),
        }, run_id=run["id"])
