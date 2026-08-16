"""SQLite-backed approval requests and durable continuation inbox.

An approval decision and the work it unlocks are committed in the same
transaction.  Consumers lease inbox rows; they never rely on an in-memory
event surviving a gateway or worker restart.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from hermes_constants import get_hermes_home
from hermes_state import apply_wal_with_fallback


DEFAULT_APPROVAL_DB_PATH = get_hermes_home() / "approvals.db"


@dataclass(frozen=True)
class ApprovalRequest:
    request_id: str
    session_key: str
    continuation_kind: str
    payload: dict[str, Any]
    idempotency_key: str
    decision: Optional[str]
    decided_by: Optional[str]


@dataclass(frozen=True)
class Continuation:
    id: str
    request_id: str
    session_key: str
    kind: str
    payload: dict[str, Any]
    idempotency_key: str
    decision: str
    state: str
    attempts: int
    next_attempt_at: float
    lease_owner: Optional[str]
    lease_until: Optional[float]
    last_error: Optional[str]
    result: Optional[dict[str, Any]]


@dataclass(frozen=True)
class ExternalIntent:
    continuation_id: str
    idempotency_key: str
    state: str
    payload: dict[str, Any]
    external_id: Optional[str]
    last_error: Optional[str]
    result: Optional[dict[str, Any]]


@dataclass(frozen=True)
class ContinuationTurnBinding:
    continuation_id: str
    session_id: str
    turn_id: str
    state: str
    history_message_id: int
    last_error: Optional[str]
    result: Optional[dict[str, Any]]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS approval_requests (
    request_id TEXT PRIMARY KEY,
    session_key TEXT NOT NULL,
    continuation_kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    decision TEXT,
    decided_by TEXT,
    created_at REAL NOT NULL,
    decided_at REAL
);

CREATE TABLE IF NOT EXISTS approval_continuations (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    session_key TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    decision TEXT NOT NULL,
    state TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at REAL NOT NULL,
    lease_owner TEXT,
    lease_until REAL,
    last_error TEXT,
    result_json TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY(request_id) REFERENCES approval_requests(request_id)
);

CREATE INDEX IF NOT EXISTS idx_approval_continuations_claim
ON approval_continuations(state, next_attempt_at, lease_until, created_at);

CREATE TABLE IF NOT EXISTS approval_external_intents (
    continuation_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    external_id TEXT,
    last_error TEXT,
    result_json TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY(continuation_id) REFERENCES approval_continuations(id)
);

CREATE TABLE IF NOT EXISTS approval_continuation_turns (
    continuation_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    turn_id TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL,
    history_message_id INTEGER NOT NULL,
    last_error TEXT,
    result_json TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY(continuation_id) REFERENCES approval_continuations(id)
);
"""


class ApprovalStore:
    """Own approval decisions, continuation leases, and external intents."""

    def __init__(
        self,
        path: str | Path = DEFAULT_APPROVAL_DB_PATH,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.path), timeout=30, isolation_level=None, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        apply_wal_with_fallback(self._conn, db_label=str(self.path))
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "ApprovalStore":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _transaction(self):
        return _ImmediateTransaction(self._conn, self._lock)

    @staticmethod
    def _approval(row: sqlite3.Row) -> ApprovalRequest:
        return ApprovalRequest(
            request_id=row["request_id"],
            session_key=row["session_key"],
            continuation_kind=row["continuation_kind"],
            payload=json.loads(row["payload_json"]),
            idempotency_key=row["idempotency_key"],
            decision=row["decision"],
            decided_by=row["decided_by"],
        )

    @staticmethod
    def _continuation(row: sqlite3.Row) -> Continuation:
        return Continuation(
            id=row["id"],
            request_id=row["request_id"],
            session_key=row["session_key"],
            kind=row["kind"],
            payload=json.loads(row["payload_json"]),
            idempotency_key=row["idempotency_key"],
            decision=row["decision"],
            state=row["state"],
            attempts=row["attempts"],
            next_attempt_at=row["next_attempt_at"],
            lease_owner=row["lease_owner"],
            lease_until=row["lease_until"],
            last_error=row["last_error"],
            result=json.loads(row["result_json"]) if row["result_json"] else None,
        )

    def create_request(
        self,
        *,
        request_id: str,
        session_key: str,
        continuation_kind: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> ApprovalRequest:
        now = self._clock()
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with self._transaction():
            self._conn.execute(
                """INSERT OR IGNORE INTO approval_requests
                   (request_id, session_key, continuation_kind, payload_json,
                    idempotency_key, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    request_id,
                    session_key,
                    continuation_kind,
                    payload_json,
                    idempotency_key,
                    now,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM approval_requests WHERE request_id=?", (request_id,)
            ).fetchone()
        return self._approval(row)

    def get_request(self, request_id: str) -> Optional[ApprovalRequest]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM approval_requests WHERE request_id=?", (request_id,)
            ).fetchone()
        return self._approval(row) if row else None

    def decide(
        self,
        request_id: str,
        decision: str,
        *,
        decided_by: str,
        lease_owner: Optional[str] = None,
        lease_seconds: float = 30,
    ) -> Continuation:
        """Persist the first decision and create its continuation atomically."""
        now = self._clock()
        with self._transaction():
            self._conn.execute(
                """UPDATE approval_requests
                   SET decision=?, decided_by=?, decided_at=?
                   WHERE request_id=? AND decision IS NULL""",
                (decision, decided_by, now, request_id),
            )
            request = self._conn.execute(
                "SELECT * FROM approval_requests WHERE request_id=?", (request_id,)
            ).fetchone()
            if request is None:
                raise KeyError(f"unknown approval request: {request_id}")
            continuation_id = uuid.uuid4().hex
            self._conn.execute(
                """INSERT OR IGNORE INTO approval_continuations
                   (id, request_id, session_key, kind, payload_json,
                    idempotency_key, decision, state, next_attempt_at,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
                (
                    continuation_id,
                    request["request_id"],
                    request["session_key"],
                    request["continuation_kind"],
                    request["payload_json"],
                    request["idempotency_key"],
                    request["decision"],
                    now,
                    now,
                    now,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM approval_continuations WHERE idempotency_key=?",
                (request["idempotency_key"],),
            ).fetchone()
            if lease_owner and row["state"] in ("pending", "retry"):
                self._conn.execute(
                    """UPDATE approval_continuations
                       SET state='leased', lease_owner=?, lease_until=?, updated_at=?
                       WHERE id=? AND state IN ('pending', 'retry')""",
                    (lease_owner, now + lease_seconds, now, row["id"]),
                )
                row = self._conn.execute(
                    "SELECT * FROM approval_continuations WHERE id=?", (row["id"],)
                ).fetchone()
        return self._continuation(row)

    def release_lease(self, continuation_id: str, worker_id: str) -> Continuation:
        """Return an owned lease to the pending inbox without an attempt."""
        now = self._clock()
        with self._transaction():
            self._conn.execute(
                """UPDATE approval_continuations
                   SET state='pending', next_attempt_at=?, lease_owner=NULL,
                       lease_until=NULL, updated_at=?
                   WHERE id=? AND state='leased' AND lease_owner=?""",
                (now, now, continuation_id, worker_id),
            )
            row = self._conn.execute(
                "SELECT * FROM approval_continuations WHERE id=?", (continuation_id,)
            ).fetchone()
        if row is None:
            raise KeyError(continuation_id)
        return self._continuation(row)

    def renew_lease(
        self, continuation_id: str, worker_id: str, *, lease_seconds: float
    ) -> bool:
        """Extend an inbox lease iff ``worker_id`` still owns it."""
        now = self._clock()
        with self._transaction():
            cursor = self._conn.execute(
                """UPDATE approval_continuations SET lease_until=?, updated_at=?
                   WHERE id=? AND state='leased' AND lease_owner=?""",
                (now + lease_seconds, now, continuation_id, worker_id),
            )
        return cursor.rowcount == 1

    def get_continuation(self, continuation_id: str) -> Optional[Continuation]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM approval_continuations WHERE id=?", (continuation_id,)
            ).fetchone()
        return self._continuation(row) if row else None

    def count_continuations(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM approval_continuations"
            ).fetchone()
        return int(row[0])

    def claim_next(
        self, worker_id: str, *, lease_seconds: float = 30
    ) -> Optional[Continuation]:
        """Atomically lease one due row, including an expired prior lease."""
        now = self._clock()
        with self._transaction():
            row = self._conn.execute(
                """SELECT id FROM approval_continuations
                   WHERE ((state IN ('pending', 'retry') AND next_attempt_at <= ?)
                          OR (state='leased' AND lease_until <= ?))
                   ORDER BY created_at, id
                   LIMIT 1""",
                (now, now),
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                """UPDATE approval_continuations
                   SET state='leased', lease_owner=?, lease_until=?, updated_at=?
                   WHERE id=?""",
                (worker_id, now + lease_seconds, now, row["id"]),
            )
            claimed = self._conn.execute(
                "SELECT * FROM approval_continuations WHERE id=?", (row["id"],)
            ).fetchone()
        return self._continuation(claimed)

    def complete(
        self,
        continuation_id: str,
        worker_id: str,
        result: Optional[dict[str, Any]] = None,
    ) -> Continuation:
        result_json = json.dumps(result or {}, sort_keys=True, separators=(",", ":"))
        now = self._clock()
        with self._transaction():
            current = self._conn.execute(
                "SELECT * FROM approval_continuations WHERE id=?", (continuation_id,)
            ).fetchone()
            if current is None:
                raise KeyError(continuation_id)
            if current["state"] == "completed":
                return self._continuation(current)
            if current["state"] != "leased" or current["lease_owner"] != worker_id:
                raise RuntimeError("continuation lease is not owned by this worker")
            self._conn.execute(
                """UPDATE approval_continuations
                   SET state='completed', result_json=?, lease_owner=NULL,
                       lease_until=NULL, last_error=NULL, updated_at=?
                   WHERE id=?""",
                (result_json, now, continuation_id),
            )
            row = self._conn.execute(
                "SELECT * FROM approval_continuations WHERE id=?", (continuation_id,)
            ).fetchone()
        return self._continuation(row)

    def consume_for_process_local_compatibility(
        self, continuation_id: str
    ) -> tuple[bool, Continuation]:
        """Atomically reserve a legacy continuation before local resolution.

        This is intentionally unavailable to durable workers.  Marking the
        row completed before touching the process-local queue ensures that a
        replayed Telegram callback cannot create a second logical resume.
        """
        now = self._clock()
        marker = json.dumps({"compatibility_fast_path": True}, separators=(",", ":"))
        with self._transaction():
            current = self._conn.execute(
                "SELECT * FROM approval_continuations WHERE id=?", (continuation_id,)
            ).fetchone()
            if current is None:
                raise KeyError(continuation_id)
            won = current["state"] in ("pending", "retry")
            if won:
                self._conn.execute(
                    """UPDATE approval_continuations
                       SET state='completed', result_json=?, lease_owner=NULL,
                           lease_until=NULL, last_error=NULL, updated_at=?
                       WHERE id=?""",
                    (marker, now, continuation_id),
                )
                current = self._conn.execute(
                    "SELECT * FROM approval_continuations WHERE id=?",
                    (continuation_id,),
                ).fetchone()
        return won, self._continuation(current)

    def fail(
        self,
        continuation_id: str,
        worker_id: str,
        error: str,
        *,
        retryable: bool,
        max_attempts: int = 5,
        base_backoff: float = 5,
        max_backoff: float = 300,
    ) -> Continuation:
        now = self._clock()
        with self._transaction():
            current = self._conn.execute(
                "SELECT * FROM approval_continuations WHERE id=?", (continuation_id,)
            ).fetchone()
            if current is None:
                raise KeyError(continuation_id)
            if current["state"] in ("completed", "terminal"):
                return self._continuation(current)
            if current["state"] != "leased" or current["lease_owner"] != worker_id:
                raise RuntimeError("continuation lease is not owned by this worker")
            attempts = int(current["attempts"]) + 1
            terminal = not retryable or attempts >= max_attempts
            delay = min(max_backoff, base_backoff * (2 ** (attempts - 1)))
            self._conn.execute(
                """UPDATE approval_continuations
                   SET state=?, attempts=?, next_attempt_at=?, lease_owner=NULL,
                       lease_until=NULL, last_error=?, updated_at=?
                   WHERE id=?""",
                (
                    "terminal" if terminal else "retry",
                    attempts,
                    now if terminal else now + delay,
                    error,
                    now,
                    continuation_id,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM approval_continuations WHERE id=?", (continuation_id,)
            ).fetchone()
        return self._continuation(row)

    def mark_reconciling(
        self,
        continuation_id: str,
        worker_id: str,
        *,
        error: str,
        external_id: Optional[str],
    ) -> Continuation:
        now = self._clock()
        with self._transaction():
            current = self._conn.execute(
                "SELECT * FROM approval_continuations WHERE id=?", (continuation_id,)
            ).fetchone()
            if current is None:
                raise KeyError(continuation_id)
            if current["state"] != "leased" or current["lease_owner"] != worker_id:
                raise RuntimeError("continuation lease is not owned by this worker")
            self._conn.execute(
                """UPDATE approval_continuations
                   SET state='reconciling', lease_owner=NULL, lease_until=NULL,
                       last_error=?, updated_at=? WHERE id=?""",
                (error, now, continuation_id),
            )
            self._conn.execute(
                """UPDATE approval_external_intents
                   SET state='ambiguous', external_id=COALESCE(?, external_id),
                       last_error=?, updated_at=? WHERE continuation_id=?""",
                (external_id, error, now, continuation_id),
            )
            row = self._conn.execute(
                "SELECT * FROM approval_continuations WHERE id=?", (continuation_id,)
            ).fetchone()
        return self._continuation(row)

    def get_or_create_external_intent(
        self, continuation: Continuation
    ) -> ExternalIntent:
        now = self._clock()
        with self._transaction():
            self._conn.execute(
                """INSERT OR IGNORE INTO approval_external_intents
                   (continuation_id, idempotency_key, state, payload_json, created_at, updated_at)
                   VALUES (?, ?, 'prepared', ?, ?, ?)""",
                (
                    continuation.id,
                    continuation.idempotency_key,
                    json.dumps(
                        continuation.payload, sort_keys=True, separators=(",", ":")
                    ),
                    now,
                    now,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM approval_external_intents WHERE continuation_id=?",
                (continuation.id,),
            ).fetchone()
        return self._intent(row)

    def get_external_intent(self, continuation_id: str) -> Optional[ExternalIntent]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM approval_external_intents WHERE continuation_id=?",
                (continuation_id,),
            ).fetchone()
        return self._intent(row) if row else None

    def begin_external_dispatch(self, continuation_id: str) -> bool:
        """Persist dispatch-in-progress before making an external request."""
        now = self._clock()
        with self._transaction():
            cursor = self._conn.execute(
                """UPDATE approval_external_intents
                   SET state='dispatching', updated_at=?
                   WHERE continuation_id=? AND state='prepared'""",
                (now, continuation_id),
            )
        return cursor.rowcount == 1

    def record_external_success(
        self, continuation_id: str, result: dict[str, Any]
    ) -> ExternalIntent:
        """Persist a definitive external result for replay without dispatch."""
        now = self._clock()
        result_json = json.dumps(result, sort_keys=True, separators=(",", ":"))
        with self._transaction():
            self._conn.execute(
                """UPDATE approval_external_intents
                   SET state='succeeded', result_json=?, last_error=NULL, updated_at=?
                   WHERE continuation_id=? AND state='dispatching'""",
                (result_json, now, continuation_id),
            )
            row = self._conn.execute(
                "SELECT * FROM approval_external_intents WHERE continuation_id=?",
                (continuation_id,),
            ).fetchone()
        return self._intent(row)

    @staticmethod
    def _intent(row: sqlite3.Row) -> ExternalIntent:
        return ExternalIntent(
            continuation_id=row["continuation_id"],
            idempotency_key=row["idempotency_key"],
            state=row["state"],
            payload=json.loads(row["payload_json"]),
            external_id=row["external_id"],
            last_error=row["last_error"],
            result=json.loads(row["result_json"]) if row["result_json"] else None,
        )

    def next_ambiguous_intent(self) -> Optional[ExternalIntent]:
        with self._lock:
            row = self._conn.execute(
                """SELECT i.* FROM approval_external_intents i
                   JOIN approval_continuations c ON c.id=i.continuation_id
                   WHERE i.state='ambiguous' AND c.state='reconciling'
                   ORDER BY i.created_at LIMIT 1"""
            ).fetchone()
        return self._intent(row) if row else None

    def complete_reconciliation(
        self, continuation_id: str, result: dict[str, Any]
    ) -> Continuation:
        now = self._clock()
        result_json = json.dumps(result, sort_keys=True, separators=(",", ":"))
        with self._transaction():
            current = self._conn.execute(
                "SELECT * FROM approval_continuations WHERE id=?", (continuation_id,)
            ).fetchone()
            if current is None:
                raise KeyError(continuation_id)
            if current["state"] == "completed":
                return self._continuation(current)
            if current["state"] != "reconciling":
                raise RuntimeError("continuation is not awaiting reconciliation")
            self._conn.execute(
                """UPDATE approval_continuations SET state='completed', result_json=?,
                   last_error=NULL, updated_at=? WHERE id=?""",
                (result_json, now, continuation_id),
            )
            self._conn.execute(
                """UPDATE approval_external_intents SET state='completed',
                   last_error=NULL, updated_at=? WHERE continuation_id=?""",
                (now, continuation_id),
            )
            row = self._conn.execute(
                "SELECT * FROM approval_continuations WHERE id=?", (continuation_id,)
            ).fetchone()
        return self._continuation(row)

    @staticmethod
    def _turn_binding(row: sqlite3.Row) -> ContinuationTurnBinding:
        return ContinuationTurnBinding(
            continuation_id=row["continuation_id"],
            session_id=row["session_id"],
            turn_id=row["turn_id"],
            state=row["state"],
            history_message_id=int(row["history_message_id"]),
            last_error=row["last_error"],
            result=json.loads(row["result_json"]) if row["result_json"] else None,
        )

    def get_or_create_turn_binding(
        self,
        continuation_id: str,
        *,
        session_id: str,
        turn_id: str,
        history_message_id: int,
    ) -> ContinuationTurnBinding:
        """Persist continuation→turn identity before gateway execution."""
        now = self._clock()
        with self._transaction():
            self._conn.execute(
                """INSERT OR IGNORE INTO approval_continuation_turns
                   (continuation_id, session_id, turn_id, state,
                    history_message_id, created_at, updated_at)
                   VALUES (?, ?, ?, 'prepared', ?, ?, ?)""",
                (
                    continuation_id,
                    session_id,
                    turn_id,
                    int(history_message_id),
                    now,
                    now,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM approval_continuation_turns WHERE continuation_id=?",
                (continuation_id,),
            ).fetchone()
        return self._turn_binding(row)

    def get_turn_binding(
        self, continuation_id: str
    ) -> Optional[ContinuationTurnBinding]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM approval_continuation_turns WHERE continuation_id=?",
                (continuation_id,),
            ).fetchone()
        return self._turn_binding(row) if row else None

    def mark_turn_binding_running(
        self, continuation_id: str
    ) -> ContinuationTurnBinding:
        now = self._clock()
        with self._transaction():
            self._conn.execute(
                """UPDATE approval_continuation_turns
                   SET state='running', updated_at=?
                   WHERE continuation_id=? AND state='prepared'""",
                (now, continuation_id),
            )
            row = self._conn.execute(
                "SELECT * FROM approval_continuation_turns WHERE continuation_id=?",
                (continuation_id,),
            ).fetchone()
        if row is None:
            raise KeyError(continuation_id)
        return self._turn_binding(row)

    def acknowledge_turn_binding(
        self, continuation_id: str, result: dict[str, Any]
    ) -> ContinuationTurnBinding:
        now = self._clock()
        result_json = json.dumps(result, sort_keys=True, separators=(",", ":"))
        with self._transaction():
            self._conn.execute(
                """UPDATE approval_continuation_turns
                   SET state='completed', result_json=?, last_error=NULL, updated_at=?
                   WHERE continuation_id=? AND state IN ('running', 'completed')""",
                (result_json, now, continuation_id),
            )
            row = self._conn.execute(
                "SELECT * FROM approval_continuation_turns WHERE continuation_id=?",
                (continuation_id,),
            ).fetchone()
        if row is None:
            raise KeyError(continuation_id)
        return self._turn_binding(row)

    def mark_turn_binding_ambiguous(
        self, continuation_id: str, error: str
    ) -> ContinuationTurnBinding:
        now = self._clock()
        with self._transaction():
            self._conn.execute(
                """UPDATE approval_continuation_turns
                   SET state='ambiguous', last_error=?, updated_at=?
                   WHERE continuation_id=? AND state != 'completed'""",
                (error, now, continuation_id),
            )
            row = self._conn.execute(
                "SELECT * FROM approval_continuation_turns WHERE continuation_id=?",
                (continuation_id,),
            ).fetchone()
        if row is None:
            raise KeyError(continuation_id)
        return self._turn_binding(row)


class _ImmediateTransaction:
    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock) -> None:
        self._conn = conn
        self._lock = lock

    def __enter__(self):
        self._lock.acquire()
        self._conn.execute("BEGIN IMMEDIATE")
        return self

    def __exit__(self, exc_type, _exc, _tb):
        try:
            self._conn.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self._lock.release()
