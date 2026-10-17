"""Outbox claims recover after process loss; subscribe SQL preserves routing and cursors."""
import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_notify as kbn


@pytest.fixture
def subscribed(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "board.db"))
    monkeypatch.setattr("time.time", lambda: 10000)
    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title="outbox", assignee="worker")
        kbn.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="origin", notifier_profile="default")
        kb.complete_task(conn, tid, summary="verified", force=True)
    return dict(task_id=tid, platform="telegram", chat_id="origin")


def test_claim_lost_with_process_recovers_after_lease(subscribed, monkeypatch):
    with kbc.connect_closing() as conn:
        old, claimed, events = kbn.claim_unseen_events_for_sub(conn, **subscribed, kinds=["completed"])
        assert len(events) == 1
    # Closing without settle models a process death, not a handled send error.
    with kbc.connect_closing() as conn:
        assert kbn.claim_unseen_events_for_sub(conn, **subscribed, kinds=["completed"])[2] == []
    monkeypatch.setattr("time.time", lambda: 11000)
    with kbc.connect_closing() as conn:
        retry_old, retry_cursor, retry = kbn.claim_unseen_events_for_sub(conn, **subscribed, kinds=["completed"])
        assert [ev.id for ev in retry] == [ev.id for ev in events]
        assert (retry_old, retry_cursor) == (old, claimed)
        kbn.advance_notify_cursor(conn, **subscribed, new_cursor=retry_cursor)
        assert kbn.claim_unseen_events_for_sub(conn, **subscribed, kinds=["completed"])[2] == []


def test_claim_recovery_keeps_successful_ping_checkpoint(subscribed, monkeypatch):
    with kbc.connect_closing() as conn:
        _, claimed, events = kbn.claim_unseen_events_for_sub(conn, **subscribed, kinds=["completed"])
        kbn.record_notify_ping(conn, **subscribed, event_id=events[0].id)
    monkeypatch.setattr("time.time", lambda: 11000)
    with kbc.connect_closing() as conn:
        _, _, retry = kbn.claim_unseen_events_for_sub(conn, **subscribed, kinds=["completed"])
        assert len(retry) == 1  # wake admission can retry, ping remains checkpointed
        assert kbn.list_notify_subs(conn, subscribed["task_id"])[0]["last_ping_event_id"] == claimed


def test_stale_settle_cannot_clear_a_newer_claim(subscribed, monkeypatch):
    with kbc.connect_closing() as conn:
        _, first_cursor, _ = kbn.claim_unseen_events_for_sub(conn, **subscribed, kinds=["completed"])
        first_lease = kbn.list_notify_subs(conn, subscribed["task_id"])[0]["claim_expires_at"]
        with kb.write_txn(conn):
            kb._append_event(conn, subscribed["task_id"], "completed", {"summary": "newer"})
    monkeypatch.setattr("time.time", lambda: 11000)
    with kbc.connect_closing() as conn:
        _, second_cursor, _ = kbn.claim_unseen_events_for_sub(conn, **subscribed, kinds=["completed"])
        second_lease = kbn.list_notify_subs(conn, subscribed["task_id"])[0]["claim_expires_at"]
        kbn.advance_notify_cursor(conn, **subscribed, new_cursor=first_cursor,
                                 expected_claim_expires_at=first_lease)
        row = kbn.list_notify_subs(conn, subscribed["task_id"])[0]
        assert row["last_event_id"] == second_cursor
        assert row["claim_expires_at"] == second_lease
        assert row["claim_old_cursor"] is not None


def test_tui_immediate_claim_does_not_replay_on_gateway_lease_timeout(subscribed, monkeypatch):
    target = {**subscribed, "platform": "tui", "chat_id": "ui-session"}
    with kbc.connect_closing() as conn:
        kbn.add_notify_sub(conn, **target)
        with kb.write_txn(conn):
            kb._append_event(conn, subscribed["task_id"], "completed", {"summary": "ui result"})
        assert len(kbn.claim_unseen_events_for_sub(conn, **target, kinds=["completed"])[2]) == 1
    monkeypatch.setattr("time.time", lambda: 11000)
    with kbc.connect_closing() as conn:
        assert kbn.claim_unseen_events_for_sub(conn, **target, kinds=["completed"])[2] == []


def test_legacy_subscription_migration_preserves_origin_and_unseen_outcome(subscribed):
    with kbc.connect_closing() as conn:
        before = kbn.list_notify_subs(conn, subscribed["task_id"])[0]
        conn.execute("ALTER TABLE kanban_notify_subs DROP COLUMN claim_old_cursor")
        conn.execute("ALTER TABLE kanban_notify_subs DROP COLUMN claim_expires_at")
    kbc.init_db()
    with kbc.connect_closing() as conn:
        after = kbn.list_notify_subs(conn, subscribed["task_id"])[0]
        assert after["notifier_profile"] == before["notifier_profile"]
        assert after["chat_id"] == before["chat_id"]
        assert after["last_event_id"] == before["last_event_id"]
        assert after["claim_old_cursor"] is None and after["claim_expires_at"] == 0
        assert len(kbn.claim_unseen_events_for_sub(conn, **subscribed, kinds=["completed"])[2]) == 1


def test_subscription_insert_and_resubscribe_preserves_pending_cursor(subscribed, monkeypatch):
    monkeypatch.setattr("time.time", lambda: 11000)
    with kbc.connect_closing() as conn:
        before = kbn.list_notify_subs(conn, subscribed["task_id"])[0]
        kbn.add_notify_sub(conn, **subscribed, delivery_metadata={"scope_id": "workspace"})
        after = kbn.list_notify_subs(conn, subscribed["task_id"])[0]
        assert after["created_at"] == before["created_at"] == 10000
        assert after["last_event_id"] == before["last_event_id"]
        assert after["notifier_profile"] == "default"
        assert after["delivery_metadata"] == {"scope_id": "workspace"}
