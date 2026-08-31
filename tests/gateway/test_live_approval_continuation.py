"""Live current-line gateway approval-continuation integration tests."""

import asyncio
import threading
from types import SimpleNamespace

import pytest

from gateway.approval_continuation import (
    ContinuationPending,
    UnrecoverableContinuation,
)
from gateway.approval_store import ApprovalStore
from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, SendResult
from gateway.run import GatewayRunner
from gateway.session import SessionSource
from gateway.telegram_approval import TelegramApprovalService
from hermes_state import AsyncSessionDB, SessionDB


class _LifecycleAdapter(BasePlatformAdapter):
    async def connect(self):
        return True

    async def disconnect(self):
        return None

    async def send(self, chat_id, content, **kwargs):
        return SendResult(success=True, message_id="delivered")

    async def get_chat_info(self, chat_id):
        return {}


class _PersistedSessionMap:
    def __init__(self, entry):
        self._lock = threading.Lock()
        self._entries = {entry.session_key: entry}

    def _ensure_loaded_locked(self):
        return None

    def lookup_by_session_key(self, session_key):
        with self._lock:
            return self._entries.get(session_key)


def _seed(
    store,
    *,
    key="restart-key",
    session_id="session-live",
    task_id=None,
    goal_id=None,
    pattern_key="test dangerous pattern",
):
    store.create_request(
        request_id=f"approval-{key}",
        session_key="agent:main:telegram:dm:7",
        continuation_kind="hermes_session",
        payload={
            "session_id": session_id,
            "command": "echo approved",
            "description": "test command",
            "pattern_key": pattern_key,
            "pattern_keys": [pattern_key],
            "task_id": task_id,
            "goal_id": goal_id,
        },
        idempotency_key=key,
    )
    return store.decide(f"approval-{key}", "once", decided_by="user-7")


def _runner(tmp_path, *, pattern_key="test dangerous pattern"):
    state = SessionDB(tmp_path / "state.db")
    state.create_session("session-live", "telegram", user_id="7")
    async_state = AsyncSessionDB(state)
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="7",
        chat_type="dm",
        user_id="7",
    )
    entry = SimpleNamespace(
        session_key="agent:main:telegram:dm:7",
        session_id="session-live",
        origin=source,
    )
    adapter = _LifecycleAdapter(
        PlatformConfig(enabled=True, token="test"), Platform.TELEGRAM
    )
    runner = object.__new__(GatewayRunner)
    runner._running = True
    runner._gateway_loop = asyncio.get_running_loop()
    runner._session_db = async_state
    runner.session_store = _PersistedSessionMap(entry)
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner.config = SimpleNamespace(multiplex_profiles=False)
    runner._running_agents = {}
    runner._background_tasks = set()
    runner._approval_continuation_runtimes = {}
    runner._gateway_logical_turn_dispatching = {}
    runner._observed_correlations = []
    runner._execution_count = 0

    async def normal_gateway_handler(event):
        # The adapter invokes the same durable admission/completion boundary
        # used by GatewayRunner._handle_message_with_agent.  The continuation
        # worker itself owns neither this attempt nor the SessionDB lease.
        from tools.approval import is_approved

        admission = await runner.admit_session_event(event, entry)
        assert admission["outcome"] == "claimed"
        turn_id = admission["turn"]["logical_turn_id"]
        attempt_id = admission["attempt_id"]
        assert await async_state.mark_logical_turn_started(turn_id, attempt_id)
        runner._observed_approval = is_approved(
            entry.session_key, pattern_key
        )
        runner._observed_correlations.append((event.task_id, event.goal_id))
        runner._execution_count += 1
        result = "approved work completed"
        state.append_message("session-live", "user", event.text)
        state.append_message("session-live", "assistant", result)
        await runner._complete_gateway_logical_turn(event, result)
        return result

    adapter.set_message_handler(normal_gateway_handler)
    return runner, adapter, state


async def _consume(runner, store, continuation):
    return await runner._consume_approval_continuation_async(store, continuation)


def test_live_telegram_fast_path_atomically_beats_durable_poller(tmp_path):
    store = ApprovalStore(tmp_path / "approvals.db")
    store.create_request(
        request_id="approval-fast",
        session_key="agent:main:telegram:dm:7",
        continuation_kind="hermes_session",
        payload={
            "session_id": "session-live",
            "process_local_fast_path": True,
        },
        idempotency_key="fast-key",
    )
    resolutions = []
    service = TelegramApprovalService(
        store,
        process_local_resolver=lambda *args: resolutions.append(args) or 1,
    )

    outcome = service.decide("approval-fast", "once", decided_by="user-7")

    assert outcome.local_resolution_count == 1
    assert outcome.continuation.state == "completed"
    assert resolutions == [("agent:main:telegram:dm:7", "once")]
    assert store.claim_next("poller") is None


@pytest.mark.asyncio
async def test_gateway_watcher_recovers_restart_continuation(tmp_path, monkeypatch):
    pattern_key = f"test dangerous pattern:{tmp_path.name}"
    runner, adapter, state = _runner(tmp_path, pattern_key=pattern_key)
    monkeypatch.setattr("hermes_constants.get_hermes_home", lambda: tmp_path)
    with ApprovalStore(tmp_path / "approvals.db") as seed:
        continuation = _seed(seed, pattern_key=pattern_key)

    watcher = asyncio.create_task(
        runner._approval_continuation_watcher(poll_interval=0.01)
    )
    observer = ApprovalStore(tmp_path / "approvals.db")
    try:
        for _ in range(200):
            current = observer.get_continuation(continuation.id)
            if current and current.state == "completed":
                break
            await asyncio.sleep(0.01)
        assert current is not None
        assert current.state == "completed"
        assert current.result["completed"] is True
        assert runner._observed_approval is True
        assert runner._execution_count == 1
        assert adapter._active_sessions == {}
        assert state.get_messages("session-live")[-1]["role"] == "assistant"
        assert state.get_session_turn_lease("session-live") is None
        from tools.approval import is_approved

        assert not is_approved(
            "agent:main:telegram:dm:7", pattern_key
        )
    finally:
        runner._running = False
        await watcher
        observer.close()
        for store, _worker in runner._approval_continuation_runtimes.values():
            store.close()


@pytest.mark.asyncio
async def test_duplicate_binding_returns_ack_without_second_gateway_turn(tmp_path):
    runner, _adapter, state = _runner(tmp_path)
    store = ApprovalStore(tmp_path / "approvals.db")
    continuation = _seed(store, key="duplicate")

    first = await _consume(runner, store, continuation)
    before = len(state.get_messages("session-live"))
    replay = await _consume(runner, store, continuation)

    assert replay == first
    assert runner._execution_count == 1
    assert len(state.get_messages("session-live")) == before
    turns = state.list_session_logical_turns("session-live")
    assert len(turns) == 1
    assert turns[0]["state"] == "completed"


@pytest.mark.asyncio
async def test_approval_continuation_does_not_invent_task_or_goal(tmp_path):
    runner, _adapter, state = _runner(tmp_path)
    store = ApprovalStore(tmp_path / "approvals.db")
    continuation = _seed(store, key="ordinary-approval")

    await _consume(runner, store, continuation)

    assert runner._observed_correlations == [(None, None)]
    turn = state.list_session_logical_turns("session-live")[-1]
    assert (turn["task_id"], turn["goal_id"]) == (None, None)


@pytest.mark.asyncio
async def test_approval_continuation_preserves_authoritative_task_metadata(tmp_path):
    runner, _adapter, state = _runner(tmp_path)
    store = ApprovalStore(tmp_path / "approvals.db")
    continuation = _seed(store, key="task-approval", task_id="T1", goal_id="G1")

    await _consume(runner, store, continuation)

    assert runner._observed_correlations == [("T1", "G1")]
    turn = state.list_session_logical_turns("session-live")[-1]
    assert (turn["task_id"], turn["goal_id"]) == ("T1", "G1")


@pytest.mark.asyncio
async def test_busy_session_is_durably_queued_without_worker_failure(tmp_path):
    runner, adapter, state = _runner(tmp_path)
    store = ApprovalStore(tmp_path / "approvals.db")
    continuation = _seed(store, key="busy")
    assert state.try_acquire_session_turn_lease(
        "session-live", "normal-inbound", ttl_seconds=300
    )

    with pytest.raises(ContinuationPending, match="queued"):
        await _consume(runner, store, continuation)

    binding = store.get_turn_binding(continuation.id)
    assert binding is not None
    turn = state.get_logical_turn(binding.turn_id)
    assert turn["state"] == "queued"
    assert runner._execution_count == 0
    assert adapter._active_sessions == {}


@pytest.mark.asyncio
async def test_deleted_or_rebound_session_is_terminal_before_execution(tmp_path):
    runner, _adapter, _state = _runner(tmp_path)
    store = ApprovalStore(tmp_path / "approvals.db")
    continuation = _seed(store, key="terminal", session_id="deleted-session")

    with pytest.raises(UnrecoverableContinuation, match="active session"):
        await _consume(runner, store, continuation)

    assert runner._execution_count == 0


@pytest.mark.asyncio
async def test_terminal_logical_turn_is_authoritative_after_lost_ack(tmp_path):
    runner, _adapter, state = _runner(tmp_path)
    store = ApprovalStore(tmp_path / "approvals.db")
    continuation = _seed(store, key="lost-ack")

    result = await _consume(runner, store, continuation)
    # Simulate acknowledgement loss in approvals.db while the canonical
    # logical turn remains terminal in SessionDB.
    with store._transaction():
        store._conn.execute(
            "UPDATE approval_continuations SET state='pending', result_json=NULL "
            "WHERE id=?",
            (continuation.id,),
        )
        store._conn.execute(
            "UPDATE approval_continuation_turns SET state='running', "
            "result_json=NULL WHERE continuation_id=?",
            (continuation.id,),
        )

    replay = await _consume(
        runner, store, store.get_continuation(continuation.id)
    )

    assert replay == result
    assert runner._execution_count == 1
    assert state.list_session_logical_turns("session-live")[0]["state"] == "completed"
    assert store.get_continuation(continuation.id).state == "pending"
    assert store.get_turn_binding(continuation.id).state == "completed"
