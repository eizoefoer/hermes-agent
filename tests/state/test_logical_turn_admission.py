"""Current-line Phase 1 durable logical-turn primitive regressions."""

from __future__ import annotations

import time
import sqlite3

from hermes_state import SessionDB


def _db(tmp_path) -> SessionDB:
    db = SessionDB(tmp_path / "state.db")
    db.create_session("session-a", source="test")
    return db


def test_admission_is_idempotent_for_one_authoritative_source(tmp_path):
    db = _db(tmp_path)
    first = db.admit_session_event(
        session_id="session-a",
        session_key="agent:test",
        source_identity="transport:update:42",
        event_type="gateway-message",
        payload={"text": "hello"},
    )
    replay = db.admit_session_event(
        session_id="session-a",
        session_key="agent:test",
        source_identity="transport:update:42",
        event_type="gateway-message",
        payload={"text": "hello"},
    )

    assert replay["logical_turn_id"] == first["logical_turn_id"]
    assert replay["duplicate"] is True
    assert db.count_logical_turns("session-a") == 1


def test_ordinary_turn_correlation_remains_null(tmp_path):
    db = _db(tmp_path)
    turn = db.admit_session_event(
        session_id="session-a",
        session_key="agent:test",
        source_identity="occurrence:1",
        event_type="gateway-message",
        payload={"text": "ordinary chat"},
    )

    assert turn["task_id"] is None
    assert turn["goal_id"] is None


def test_claim_correlates_attempt_and_canonical_lease(tmp_path):
    db = _db(tmp_path)
    admitted = db.admit_session_event(
        session_id="session-a",
        session_key="agent:test",
        source_identity="occurrence:claim",
        event_type="gateway-message",
    )
    claim = db.claim_logical_turn(
        admitted["logical_turn_id"], owner="worker-a", pid=123, ttl_seconds=30
    )

    assert claim["outcome"] == "claimed"
    assert claim["turn"]["current_attempt_id"] == claim["attempt_id"]
    assert claim["turn"]["attempt_count"] == 1
    assert claim["turn"]["lease_holder"] == claim["lease"]["holder"]
    lease = db.get_session_turn_lease("session-a")
    assert lease is not None
    assert lease["holder"] == claim["lease"]["holder"]


def test_valid_foreign_lease_blocks_claim_even_when_holder_pid_is_dead(
    tmp_path, monkeypatch
):
    import hermes_state

    db = _db(tmp_path)
    assert db.try_acquire_session_turn_lease(
        "session-a", "pid=424242:foreign", ttl_seconds=30
    )
    monkeypatch.setattr(
        hermes_state.psutil, "pid_exists", lambda _pid: False
    )
    admitted = db.admit_session_event(
        session_id="session-a",
        session_key="agent:test",
        source_identity="occurrence:busy",
        event_type="gateway-message",
    )

    claim = db.claim_logical_turn(
        admitted["logical_turn_id"], owner="worker-b", pid=525252
    )
    assert claim["outcome"] == "busy"
    assert claim["lease"]["holder"] == "pid=424242:foreign"
    assert db.get_logical_turn(admitted["logical_turn_id"])["state"] == "queued"


def test_expired_attempt_reconciles_to_same_turn_with_new_attempt(tmp_path):
    db = _db(tmp_path)
    admitted = db.admit_session_event(
        session_id="session-a",
        session_key="agent:test",
        source_identity="occurrence:retry",
        event_type="gateway-message",
        payload={"recovery_policy": "auto_retry"},
    )
    first = db.claim_logical_turn(
        admitted["logical_turn_id"], owner="worker-a", ttl_seconds=0.05
    )
    assert db.mark_logical_turn_started(
        admitted["logical_turn_id"], first["attempt_id"]
    )
    time.sleep(0.12)

    assert db.reconcile_logical_turns() == 1
    second = db.claim_logical_turn(
        admitted["logical_turn_id"], owner="worker-b", ttl_seconds=30
    )
    assert second["outcome"] == "claimed"
    assert second["attempt_id"] != first["attempt_id"]
    assert second["turn"]["logical_turn_id"] == admitted["logical_turn_id"]
    assert second["turn"]["attempt_count"] == 2


def test_expired_executing_attempt_blocks_without_safe_retry_policy(tmp_path):
    db = _db(tmp_path)
    admitted = db.admit_session_event(
        session_id="session-a",
        session_key="agent:test",
        source_identity="occurrence:ambiguous",
        event_type="gateway-message",
    )
    claimed = db.claim_logical_turn(
        admitted["logical_turn_id"], owner="worker-a", ttl_seconds=0.05
    )
    assert db.mark_logical_turn_started(
        admitted["logical_turn_id"], claimed["attempt_id"]
    )
    time.sleep(0.12)

    assert db.reconcile_logical_turns() == 1
    blocked = db.get_logical_turn(admitted["logical_turn_id"])
    assert blocked["state"] == "blocked"
    assert "effect reconciliation required" in blocked["error"]
    assert db.claim_logical_turn(
        admitted["logical_turn_id"], owner="worker-b"
    )["outcome"] == "terminal"


def test_expired_claimed_attempt_is_safe_to_retry_before_execution(tmp_path):
    db = _db(tmp_path)
    admitted = db.admit_session_event(
        session_id="session-a",
        session_key="agent:test",
        source_identity="occurrence:claimed-only",
        event_type="gateway-message",
    )
    first = db.claim_logical_turn(
        admitted["logical_turn_id"], owner="worker-a", ttl_seconds=0.05
    )
    time.sleep(0.12)

    assert db.reconcile_logical_turns() == 1
    second = db.claim_logical_turn(
        admitted["logical_turn_id"], owner="worker-b", ttl_seconds=30
    )
    assert second["outcome"] == "claimed"
    assert second["attempt_id"] != first["attempt_id"]


def test_terminal_execution_is_immutable_and_delivery_is_separate(tmp_path):
    db = _db(tmp_path)
    admitted = db.admit_session_event(
        session_id="session-a",
        session_key="agent:test",
        source_identity="occurrence:complete",
        event_type="gateway-message",
    )
    claim = db.claim_logical_turn(admitted["logical_turn_id"], owner="worker")
    assert db.mark_logical_turn_started(
        admitted["logical_turn_id"], claim["attempt_id"]
    )
    completed = db.complete_logical_turn(
        admitted["logical_turn_id"],
        claim["attempt_id"],
        {"response": "done"},
        delivery_required=True,
    )
    assert completed["state"] == "completed"
    assert completed["delivery_state"] == "pending"
    assert db.get_session_turn_lease("session-a") is None

    assert db.reconcile_logical_turns() == 0
    replay = db.claim_logical_turn(admitted["logical_turn_id"], owner="other")
    assert replay["outcome"] == "terminal"
    delivered = db.acknowledge_logical_turn_delivery(
        admitted["logical_turn_id"], claim["attempt_id"]
    )
    assert delivered["state"] == "completed"
    assert delivered["delivery_state"] == "delivered"


def test_ready_filter_is_applied_before_limit(tmp_path):
    db = _db(tmp_path)
    for index in range(5):
        db.admit_session_event(
            session_id="session-a",
            session_key="agent:test",
            source_identity=f"foreign:{index}",
            event_type="foreign-producer",
        )
    target = db.admit_session_event(
        session_id="session-a",
        session_key="agent:test",
        source_identity="target:1",
        event_type="target-producer",
    )

    ready = db.list_ready_logical_turns(
        limit=1, event_types=("target-producer",)
    )
    assert [row["logical_turn_id"] for row in ready] == [
        target["logical_turn_id"]
    ]


def test_pending_delivery_filter_is_applied_before_limit(tmp_path):
    db = _db(tmp_path)

    def _complete(source_identity, event_type):
        turn = db.admit_session_event(
            session_id="session-a",
            session_key="agent:test",
            source_identity=source_identity,
            event_type=event_type,
        )
        claim = db.claim_logical_turn(turn["logical_turn_id"], owner=source_identity)
        assert db.mark_logical_turn_started(
            turn["logical_turn_id"], claim["attempt_id"]
        )
        return db.complete_logical_turn(
            turn["logical_turn_id"],
            claim["attempt_id"],
            {"response": source_identity},
            delivery_required=True,
        )

    for index in range(5):
        _complete(f"foreign:{index}", "foreign-producer")
    target = _complete("target:delivery", "target-producer")

    pending = db.list_pending_logical_turn_deliveries(
        limit=1, event_types=("target-producer",)
    )
    assert [row["logical_turn_id"] for row in pending] == [
        target["logical_turn_id"]
    ]


def test_compression_lineage_uses_one_conversation_lease(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    db.create_session("root", source="test")
    db.end_session("root", "compression")
    db.create_session("child", source="test", parent_session_id="root")
    root_turn = db.admit_session_event(
        session_id="root",
        session_key="root-key",
        source_identity="root-event",
        event_type="gateway-message",
    )
    child_turn = db.admit_session_event(
        session_id="child",
        session_key="child-key",
        source_identity="child-event",
        event_type="gateway-message",
    )

    first = db.claim_logical_turn(root_turn["logical_turn_id"], owner="root")
    second = db.claim_logical_turn(child_turn["logical_turn_id"], owner="child")
    assert first["outcome"] == "claimed"
    assert second["outcome"] == "busy"
    assert second["lease"]["conversation_id"] == "root"


def test_phase1_snapshot_lease_shape_is_migrated_without_reclaim(tmp_path):
    """The preserved production snapshot has the old turn_id lease shape."""
    path = tmp_path / "state.db"
    db = SessionDB(path)
    db.create_session("session-a", source="test")
    admitted = db.admit_session_event(
        session_id="session-a",
        session_key="agent:test",
        source_identity="legacy:active",
        event_type="gateway-message",
    )
    claimed = db.claim_logical_turn(
        admitted["logical_turn_id"],
        owner="legacy-worker",
        pid=987654,
        ttl_seconds=300,
    )
    assert db.mark_logical_turn_started(
        admitted["logical_turn_id"], claimed["attempt_id"]
    )
    holder = claimed["lease"]["holder"]
    acquired_at = claimed["lease"]["acquired_at"]
    expires_at = claimed["lease"]["expires_at"]
    db.close()

    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("DROP INDEX IF EXISTS idx_session_turn_leases_expires")
    conn.execute("DROP INDEX IF EXISTS idx_logical_turns_ready")
    conn.execute("DROP INDEX IF EXISTS idx_logical_turns_session_ready")
    conn.execute("DROP INDEX IF EXISTS idx_logical_turns_attempt")
    conn.execute("ALTER TABLE logical_turns RENAME TO logical_turns_current")
    conn.execute(
        """CREATE TABLE logical_turns (
            logical_turn_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            session_key TEXT NOT NULL,
            source_identity TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            state TEXT NOT NULL DEFAULT 'queued',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            current_attempt_id TEXT,
            owner TEXT,
            owner_pid INTEGER,
            lease_holder TEXT,
            started_at REAL,
            heartbeat_at REAL,
            completed_at REAL,
            failed_at REAL,
            result_json TEXT,
            error TEXT,
            delivery_state TEXT NOT NULL DEFAULT 'not_required',
            delivery_attempts INTEGER NOT NULL DEFAULT 0,
            delivery_updated_at REAL,
            delivery_error TEXT,
            task_id TEXT,
            goal_id TEXT,
            branch TEXT,
            worktree TEXT,
            UNIQUE(session_id, source_identity)
        )"""
    )
    legacy_columns = (
        "logical_turn_id, session_id, session_key, source_identity, "
        "payload_json, state, created_at, updated_at, attempt_count, "
        "current_attempt_id, owner, owner_pid, lease_holder, started_at, "
        "heartbeat_at, completed_at, failed_at, result_json, error, "
        "delivery_state, delivery_attempts, delivery_updated_at, "
        "delivery_error, task_id, goal_id, branch, worktree"
    )
    conn.execute(
        f"INSERT INTO logical_turns ({legacy_columns}) "
        f"SELECT {legacy_columns} FROM logical_turns_current"
    )
    conn.execute("DROP TABLE logical_turns_current")
    conn.execute("ALTER TABLE session_turn_leases RENAME TO lease_current")
    conn.execute(
        """CREATE TABLE session_turn_leases (
            conversation_id TEXT PRIMARY KEY,
            holder TEXT NOT NULL,
            acquired_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            session_id TEXT,
            turn_id TEXT NOT NULL
        )"""
    )
    conn.execute(
        "INSERT INTO session_turn_leases "
        "(conversation_id, holder, acquired_at, expires_at, session_id, turn_id) "
        "VALUES (NULL, ?, ?, ?, 'session-a', ?)",
        (holder, acquired_at, expires_at, claimed["attempt_id"]),
    )
    conn.execute("DROP TABLE lease_current")
    conn.commit()
    conn.close()

    reopened = SessionDB(path)
    lease_columns = {
        row[1]
        for row in reopened._conn.execute(
            "PRAGMA table_info(session_turn_leases)"
        ).fetchall()
    }
    assert lease_columns == {
        "conversation_id",
        "holder",
        "acquired_at",
        "expires_at",
    }
    lease = reopened.get_session_turn_lease("session-a")
    assert lease is not None
    assert lease["holder"] == holder
    turn = reopened.get_logical_turn(admitted["logical_turn_id"])
    assert turn["state"] == "executing"
    assert turn["lease_conversation_id"] == "session-a"
    assert reopened.heartbeat_logical_turn(
        admitted["logical_turn_id"], claimed["attempt_id"], ttl_seconds=300
    )
