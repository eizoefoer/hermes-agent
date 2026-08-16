"""Phase 1.3 durable logical-turn admission contracts."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from gateway.platforms.base import Platform
from gateway.run import GatewayRunner
from gateway.session import SessionSource
from hermes_state import SessionDB


def _db(tmp_path):
    state = SessionDB(tmp_path / "state.db")
    state.create_session("session-1", "cli", user_id="user-1")
    return state


def test_historical_resumed_session_without_lease_claims_a_normal_logical_turn(tmp_path):
    """Loading history must not manufacture busy ownership in a fresh process."""
    state = _db(tmp_path)
    state.append_message("session-1", "user", "old request")
    state.append_message("session-1", "assistant", "old response")
    assert state.get_session_turn_lease("session-1") is None

    admitted = state.admit_logical_turn(
        session_id="session-1",
        session_key="cli:session-1",
        source_identity="cli:resume:message-1",
        payload={"text": "new request", "source": "cli"},
    )
    claim = state.claim_logical_turn(
        admitted["logical_turn_id"], owner="cli:123", pid=123, ttl_seconds=30
    )
    assert state.mark_logical_turn_started(admitted["logical_turn_id"], claim["attempt_id"])

    assert admitted["state"] == "queued"
    assert claim["outcome"] == "claimed"
    assert state.get_logical_turn(admitted["logical_turn_id"])["state"] == "executing"
    assert claim["turn"]["attempt_count"] == 1
    assert state.get_session_turn_lease("session-1")["turn_id"] == claim["attempt_id"]


def test_real_durable_contention_queues_second_turn_without_second_execution(tmp_path):
    state = _db(tmp_path)
    first = state.admit_logical_turn(
        session_id="session-1", session_key="telegram:7", source_identity="update:1", payload={}
    )
    first_claim = state.claim_logical_turn(first["logical_turn_id"], owner="gateway:a", pid=1)
    second = state.admit_logical_turn(
        session_id="session-1", session_key="telegram:7", source_identity="update:2", payload={}
    )

    second_claim = state.claim_logical_turn(second["logical_turn_id"], owner="gateway:b", pid=2)

    assert first_claim["outcome"] == "claimed"
    assert second_claim["outcome"] == "busy"
    assert second_claim["lease"]["holder"].startswith("gateway:a:")
    assert state.get_logical_turn(second["logical_turn_id"])["state"] == "queued"


def test_duplicate_source_delivery_is_one_logical_turn_and_one_attempt(tmp_path):
    state = _db(tmp_path)

    first = state.admit_logical_turn(
        session_id="session-1", session_key="telegram:7", source_identity="telegram:update:99", payload={"x": 1}
    )
    replay = state.admit_logical_turn(
        session_id="session-1", session_key="telegram:7", source_identity="telegram:update:99", payload={"x": 1}
    )

    assert replay["logical_turn_id"] == first["logical_turn_id"]
    assert replay["duplicate"] is True
    assert state.count_logical_turns("session-1") == 1


def test_release_promotes_next_queued_turn_and_crashed_attempt_is_reclaimable(tmp_path):
    state = _db(tmp_path)
    first = state.admit_logical_turn(
        session_id="session-1", session_key="telegram:7", source_identity="update:1", payload={}
    )
    second = state.admit_logical_turn(
        session_id="session-1", session_key="telegram:7", source_identity="update:2", payload={}
    )
    claimed = state.claim_logical_turn(first["logical_turn_id"], owner="gateway:a", pid=1, ttl_seconds=0.01)
    assert claimed["outcome"] == "claimed"

    state.fail_logical_turn(first["logical_turn_id"], claimed["attempt_id"], "crashed", retryable=True)
    promoted = state.claim_next_logical_turn("session-1", owner="gateway:b", pid=2)

    assert promoted["outcome"] == "claimed"
    assert promoted["turn"]["logical_turn_id"] == first["logical_turn_id"]
    assert promoted["turn"]["attempt_count"] == 2


def test_concurrent_claims_have_one_winner(tmp_path):
    path = tmp_path / "state.db"
    seed = SessionDB(path)
    seed.create_session("session-1", "cli", user_id="user-1")
    turn = seed.admit_logical_turn(
        session_id="session-1", session_key="cli:session-1", source_identity="cli:1", payload={}
    )

    def claim(worker):
        db = SessionDB(path)
        return db.claim_logical_turn(turn["logical_turn_id"], owner=worker, pid=1)["outcome"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(claim, ["a", "b", "c", "d"]))

    assert outcomes.count("claimed") == 1
    assert outcomes.count("busy") == 3


def test_queued_turn_survives_restart_and_keeps_its_logical_identity(tmp_path):
    state = _db(tmp_path)
    queued = state.admit_logical_turn(
        session_id="session-1", session_key="cli:session-1",
        source_identity="cli:resume:queued", payload={"text": "continue"},
    )

    assert state.reconcile_logical_turns() == 0
    recovered = state.claim_next_logical_turn("session-1", owner="cli:replacement", pid=2)

    assert recovered["outcome"] == "claimed"
    assert recovered["turn"]["logical_turn_id"] == queued["logical_turn_id"]
    assert recovered["turn"]["attempt_count"] == 1


def test_claimed_turn_with_live_owner_is_not_replayed_on_restart(tmp_path):
    state = _db(tmp_path)
    turn = state.admit_logical_turn(
        session_id="session-1", session_key="cli:session-1", source_identity="cli:claimed", payload={},
    )
    claim = state.claim_logical_turn(turn["logical_turn_id"], owner="cli:live", pid=1)

    assert state.reconcile_logical_turns() == 0
    assert state.get_logical_turn(turn["logical_turn_id"])["state"] == "claimed"
    assert state.claim_next_logical_turn("session-1", owner="cli:replacement", pid=2)["outcome"] == "empty"
    assert claim["attempt_id"] == state.get_logical_turn(turn["logical_turn_id"])["current_attempt_id"]


def test_executing_turn_without_lease_is_requeued_with_new_attempt(tmp_path):
    state = _db(tmp_path)
    turn = state.admit_logical_turn(
        session_id="session-1", session_key="cli:session-1", source_identity="cli:crash", payload={},
    )
    first = state.claim_logical_turn(turn["logical_turn_id"], owner="cli:crashed", pid=1)
    assert state.mark_logical_turn_started(turn["logical_turn_id"], first["attempt_id"])
    state.release_session_turn_lease("session-1", first["lease"]["holder"], first["attempt_id"])

    assert state.reconcile_logical_turns() == 1
    second = state.claim_next_logical_turn("session-1", owner="cli:replacement", pid=2)

    assert second["outcome"] == "claimed"
    assert second["turn"]["logical_turn_id"] == turn["logical_turn_id"]
    assert second["attempt_id"] != first["attempt_id"]
    assert second["turn"]["attempt_count"] == 2


def test_completed_execution_retains_a_pending_delivery_obligation(tmp_path):
    state = _db(tmp_path)
    turn = state.admit_logical_turn(
        session_id="session-1", session_key="telegram:7", source_identity="telegram:update:delivery", payload={},
    )
    claim = state.claim_logical_turn(turn["logical_turn_id"], owner="gateway:a", pid=1)
    assert state.mark_logical_turn_started(turn["logical_turn_id"], claim["attempt_id"])

    completed = state.complete_logical_turn(
        turn["logical_turn_id"], claim["attempt_id"], {"response": "done"}, delivery_required=True,
    )

    assert completed["state"] == "completed"
    assert completed["delivery_state"] == "pending"
    assert state.acknowledge_logical_turn_delivery(turn["logical_turn_id"], claim["attempt_id"])["delivery_state"] == "delivered"
    assert state.get_logical_turn(turn["logical_turn_id"])["state"] == "completed"


def test_delivery_recovery_replays_only_completed_obligation(tmp_path):
    state = _db(tmp_path)
    turn = state.admit_logical_turn(
        session_id="session-1", session_key="telegram:7", source_identity="telegram:update:replay",
        payload={"source": {"platform": "telegram"}, "text": "request"},
    )
    claim = state.claim_logical_turn(turn["logical_turn_id"], owner="gateway:a", pid=1)
    assert state.mark_logical_turn_started(turn["logical_turn_id"], claim["attempt_id"])
    state.complete_logical_turn(
        turn["logical_turn_id"], claim["attempt_id"], {"response": "already executed"},
        delivery_required=True,
    )

    state.begin_logical_turn_delivery(turn["logical_turn_id"], claim["attempt_id"])
    state.fail_logical_turn_delivery(turn["logical_turn_id"], claim["attempt_id"], "transport timeout")

    pending = state.list_pending_logical_turn_deliveries()
    assert [row["logical_turn_id"] for row in pending] == [turn["logical_turn_id"]]
    assert pending[0]["state"] == "completed"
    assert pending[0]["result"]["response"] == "already executed"
    assert state.claim_logical_turn(turn["logical_turn_id"], owner="gateway:b", pid=2)["outcome"] == "terminal"

    state.acknowledge_logical_turn_delivery(turn["logical_turn_id"], claim["attempt_id"])
    assert state.get_logical_turn(turn["logical_turn_id"])["delivery_attempts"] == 2


def test_startup_drain_snapshot_excludes_claimed_completed_and_terminal_turns(tmp_path):
    state = _db(tmp_path)
    queued = state.admit_logical_turn(
        session_id="session-1", session_key="telegram:7", source_identity="queued", payload={}
    )
    claimed = state.admit_logical_turn(
        session_id="session-1", session_key="telegram:7", source_identity="claimed", payload={}
    )
    assert state.claim_logical_turn(claimed["logical_turn_id"], owner="gateway:a", pid=1)["outcome"] == "claimed"

    assert [row["logical_turn_id"] for row in state.list_ready_logical_turns()] == [queued["logical_turn_id"]]


@pytest.mark.asyncio
async def test_startup_drain_dispatches_persisted_queue_and_replays_delivery_only(tmp_path):
    state = _db(tmp_path)
    source = SessionSource(platform=Platform.TELEGRAM, chat_id="7", user_id="user-1")
    queued = state.admit_logical_turn(
        session_id="session-1", session_key="telegram:7", source_identity="startup:queued",
        payload={"source": source.to_dict(), "text": "durable queued", "message_id": "11"},
    )
    completed = state.admit_logical_turn(
        session_id="session-1", session_key="telegram:7", source_identity="startup:delivery",
        payload={"source": source.to_dict(), "text": "already ran", "message_id": "12"},
    )
    claim = state.claim_logical_turn(completed["logical_turn_id"], owner="gateway:old", pid=1)
    assert state.mark_logical_turn_started(completed["logical_turn_id"], claim["attempt_id"])
    state.complete_logical_turn(
        completed["logical_turn_id"], claim["attempt_id"], {"response": "deliver me"}, delivery_required=True,
    )

    class Adapter:
        def __init__(self):
            self._active_sessions = {}
            self.handled = []
            self.sent = []

        async def handle_message(self, event):
            self.handled.append(event)

        async def _send_with_retry(self, **kwargs):
            self.sent.append(kwargs)
            return SimpleNamespace(success=True, error=None)

    adapter = Adapter()
    runner = GatewayRunner.__new__(GatewayRunner)
    runner._session_db = state
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._background_tasks = set()

    assert await runner._drain_startup_logical_turns() == 1
    await asyncio.sleep(0)
    assert [event.text for event in adapter.handled] == ["durable queued"]
    assert await runner._replay_pending_logical_turn_deliveries() == 1
    assert [item["content"] for item in adapter.sent] == ["deliver me"]
    assert state.get_logical_turn(completed["logical_turn_id"])["state"] == "completed"
    assert state.get_logical_turn(completed["logical_turn_id"])["delivery_state"] == "delivered"
    assert state.get_logical_turn(queued["logical_turn_id"])["state"] == "queued"


def test_terminal_unrecoverable_turn_never_replays(tmp_path):
    state = _db(tmp_path)
    turn = state.admit_logical_turn(
        session_id="session-1", session_key="cli:session-1", source_identity="cli:unsafe", payload={},
    )
    claim = state.claim_logical_turn(turn["logical_turn_id"], owner="cli:1", pid=1)
    state.fail_logical_turn(turn["logical_turn_id"], claim["attempt_id"], "external side effect ambiguous", retryable=False)

    assert state.reconcile_logical_turns() == 0
    assert state.claim_logical_turn(turn["logical_turn_id"], owner="cli:2", pid=2)["outcome"] == "terminal"


def test_task_correlation_is_retained_across_attempts(tmp_path):
    state = _db(tmp_path)
    turn = state.admit_logical_turn(
        session_id="session-1", session_key="cli:session-1", source_identity="cli:task",
        payload={}, task_id="task-1", goal_id="goal-1", branch="feature/durable", worktree="/tmp/worktree",
    )
    first = state.claim_logical_turn(turn["logical_turn_id"], owner="cli:1", pid=1)
    state.fail_logical_turn(turn["logical_turn_id"], first["attempt_id"], "retry", retryable=True)
    second = state.claim_logical_turn(turn["logical_turn_id"], owner="cli:2", pid=2)

    retained = state.get_logical_turn(turn["logical_turn_id"])
    assert retained["session_id"] == "session-1"
    assert retained["task_id"] == "task-1"
    assert retained["goal_id"] == "goal-1"
    assert retained["branch"] == "feature/durable"
    assert retained["worktree"] == "/tmp/worktree"
    assert retained["current_attempt_id"] == second["attempt_id"]


def test_canonical_session_event_facade_keeps_background_completion_distinct_from_anchor(tmp_path):
    """A same-session completion is new work, not a duplicate of its anchor."""
    from gateway.session import SessionEntry
    from datetime import datetime

    state = _db(tmp_path)
    source = SessionSource(platform=Platform.TELEGRAM, user_id="user-1", chat_id="chat-1")
    entry = SessionEntry(
        session_key="telegram:chat-1", session_id="session-1", platform=Platform.TELEGRAM,
        chat_type="dm", created_at=datetime.now(), updated_at=datetime.now(),
    )
    runner = GatewayRunner.__new__(GatewayRunner)
    runner._session_db = state
    runner._running_agents = {}
    inbound = SimpleNamespace(
        source=source, platform_update_id=None, message_id="anchor-1", text="start",
        internal=False, session_event_id=None, session_event_type=None,
        task_id="task-1", goal_id=None, branch="feature/durable", worktree="/tmp/worktree",
    )
    completion = SimpleNamespace(
        source=source, platform_update_id=None, message_id="anchor-1", text="done",
        internal=True, session_event_id="process:proc-1:complete",
        session_event_type="background-complete", task_id="task-1", goal_id=None,
        branch="feature/durable", worktree="/tmp/worktree",
    )

    first = runner.admit_session_event(inbound, entry)
    second = runner.admit_session_event(completion, entry, claim=False)

    assert first["outcome"] == "claimed"
    assert second["outcome"] == "queued"
    turns = state.list_session_logical_turns("session-1")
    assert len(turns) == 2
    assert turns[1]["payload"]["event_type"] == "background-complete"
    assert turns[1]["task_id"] == "task-1"


def test_session_event_diagnostics_reports_durable_running_without_local_owner(tmp_path):
    from gateway.session import SessionEntry
    from datetime import datetime

    state = _db(tmp_path)
    turn = state.admit_session_event(
        session_id="session-1", session_key="telegram:chat-1", source_identity="event:1",
        event_type="background-complete", payload={}, task_id="task-1",
    )
    state.claim_logical_turn(turn["logical_turn_id"], owner="gateway:other", pid=777)
    runner = GatewayRunner.__new__(GatewayRunner)
    runner._session_db = state
    runner._running_agents = {}
    entry = SessionEntry(
        session_key="telegram:chat-1", session_id="session-1", platform=Platform.TELEGRAM,
        chat_type="dm", created_at=datetime.now(), updated_at=datetime.now(),
    )

    diagnostics = runner.session_event_diagnostics(entry)

    assert len(diagnostics) == 1
    assert diagnostics[0]["issue"] == "durable_running_without_local_owner"
    assert diagnostics[0]["logical_turn_id"] == turn["logical_turn_id"]
    assert diagnostics[0]["task_id"] == "task-1"
    assert diagnostics[0]["pid"] == 777
    assert diagnostics[0]["local_active"] is False


def test_rehydrated_durable_event_preserves_identity_and_correlation(tmp_path):
    state = _db(tmp_path)
    source = SessionSource(platform=Platform.TELEGRAM, user_id="user-1", chat_id="chat-1")
    turn = state.admit_session_event(
        session_id="session-1",
        session_key="telegram:chat-1",
        source_identity="telegram:event:goal:goal-1",
        event_type="goal-continuation",
        payload={
            "text": "continue",
            "internal": True,
            "session_event_id": "goal-1",
            "session_event_type": "goal-continuation",
            "source": source.to_dict(),
        },
        task_id="task-1",
        goal_id="goal-1",
        branch="feature/durable",
        worktree="/tmp/worktree",
    )

    event = GatewayRunner._message_event_from_logical_turn(
        state.get_logical_turn(turn["logical_turn_id"])
    )

    assert event is not None
    assert event.session_event_id == "goal-1"
    assert event.session_event_type == "goal-continuation"
    assert (event.task_id, event.goal_id) == ("task-1", "goal-1")
    assert (event.branch, event.worktree) == ("feature/durable", "/tmp/worktree")


@pytest.mark.asyncio
async def test_completed_turn_drain_enqueues_next_turn_without_self_wait(tmp_path):
    state = _db(tmp_path)
    source = SessionSource(platform=Platform.TELEGRAM, user_id="user-1", chat_id="chat-1")
    first = state.admit_session_event(
        session_id="session-1", session_key="telegram:chat-1", source_identity="first",
        event_type="inbound", payload={"source": source.to_dict(), "text": "first"},
    )
    claim = state.claim_logical_turn(first["logical_turn_id"], owner="gateway:test", pid=1)
    state.mark_logical_turn_started(first["logical_turn_id"], claim["attempt_id"])
    state.complete_logical_turn(first["logical_turn_id"], claim["attempt_id"])
    state.admit_session_event(
        session_id="session-1", session_key="telegram:chat-1", source_identity="second",
        event_type="background-complete",
        payload={
            "source": source.to_dict(), "text": "second", "internal": True,
            "session_event_id": "process-1", "session_event_type": "background-complete",
        },
    )

    class Adapter:
        def __init__(self):
            self._pending_messages = {}

        async def handle_message(self, event):
            raise AssertionError("current-session drain must enqueue, not self-wait")

    runner = GatewayRunner.__new__(GatewayRunner)
    runner._session_db = state
    runner.adapters = {Platform.TELEGRAM: Adapter()}
    runner._queued_events = {}

    await runner._drain_durable_logical_turn(first["logical_turn_id"])

    pending = runner.adapters[Platform.TELEGRAM]._pending_messages["telegram:chat-1"]
    assert pending.text == "second"
    assert pending.session_event_id == "process-1"


def test_session_event_diagnostics_reports_local_and_durable_owner_disagreement(tmp_path):
    from datetime import datetime
    from gateway.session import SessionEntry

    state = _db(tmp_path)
    turn = state.admit_session_event(
        session_id="session-1", session_key="telegram:chat-1", source_identity="event:owner",
        event_type="inbound", payload={}, task_id="task-1",
    )
    state.claim_logical_turn(turn["logical_turn_id"], owner="gateway:other-process", pid=777)
    runner = GatewayRunner.__new__(GatewayRunner)
    runner._session_db = state
    runner._running_agents = {"telegram:chat-1": SimpleNamespace(_logical_turn_id="local")}
    entry = SessionEntry(
        session_key="telegram:chat-1", session_id="session-1", platform=Platform.TELEGRAM,
        chat_type="dm", created_at=datetime.now(), updated_at=datetime.now(),
    )

    diagnostics = runner.session_event_diagnostics(entry)

    assert diagnostics[0]["issue"] == "local_active_durable_owner_mismatch"
    assert diagnostics[0]["lease_holder"].startswith("gateway:other-process:")
