"""Live Phase 1.3 gateway approval continuation integration tests."""

import asyncio
import threading
from types import SimpleNamespace

import pytest

from gateway.approval_continuation import (
    ContinuationUnavailable,
    UnrecoverableContinuation,
    gateway_continuation_registry,
)
from gateway.approval_store import ApprovalStore
from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, SendResult
from gateway.run import GatewayRunner
from gateway.session import SessionSource
from gateway.telegram_approval import TelegramApprovalService
from hermes_state import SessionDB


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


def _seed(store, *, key="restart-key", session_id="session-live"):
    store.create_request(
        request_id=f"approval-{key}",
        session_key="agent:main:telegram:dm:7",
        continuation_kind="hermes_session",
        payload={
            "session_id": session_id,
            "command": "echo approved",
            "description": "test command",
            "pattern_key": "test dangerous pattern",
            "pattern_keys": ["test dangerous pattern"],
        },
        idempotency_key=key,
    )
    return store.decide(f"approval-{key}", "once", decided_by="user-7")


def _runner(tmp_path):
    state = SessionDB(tmp_path / "state.db")
    state.create_session("session-live", "telegram", user_id="7")
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
    runner._session_db = state
    runner.session_store = _PersistedSessionMap(entry)
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._running_agents = {}
    runner._background_tasks = set()
    runner._approval_continuation_store = None
    runner._approval_continuation_consumer = None
    runner._approval_continuation_task = None

    async def normal_gateway_handler(event):
        # Exercise BasePlatformAdapter's real active-session/task/delivery
        # lifecycle while persisting the terminal semantic acknowledgement
        # that the production GatewayRunner/AIAgent path writes.
        from tools.approval import is_approved

        runner._observed_approval = is_approved(
            entry.session_key, "test dangerous pattern"
        )
        state.append_message("session-live", "user", event.text)
        state.append_message(
            "session-live", "assistant", "approved work completed"
        )
        return "approved work completed"

    adapter.set_message_handler(normal_gateway_handler)
    return runner, adapter, state


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
async def test_gateway_startup_poller_recovers_restart_continuation(tmp_path, monkeypatch):
    runner, adapter, state = _runner(tmp_path)
    monkeypatch.setattr("hermes_constants.get_hermes_home", lambda: tmp_path)
    seed = ApprovalStore(tmp_path / "approvals.db")
    continuation = _seed(seed)
    seed.close()  # Simulate the approving process exiting before consumption.

    runner._start_approval_continuation_runtime()
    try:
        for _ in range(100):
            current = runner._approval_continuation_store.get_continuation(
                continuation.id
            )
            if current.state == "completed":
                break
            await asyncio.sleep(0.02)
        assert current.state == "completed"
        assert current.result["session_id"] == "session-live"
        assert runner._observed_approval is True
        assert adapter._active_sessions == {}
        assert state.get_messages("session-live")[-1]["role"] == "assistant"
        from tools.approval import is_approved

        assert is_approved(
            "agent:main:telegram:dm:7", "test dangerous pattern"
        ) is False
    finally:
        runner._running = False
        gateway_continuation_registry.unregister(
            "hermes_session", runner._approval_continuation_consumer
        )
        runner._approval_continuation_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner._approval_continuation_task
        runner._approval_continuation_store.close()


@pytest.mark.asyncio
async def test_duplicate_binding_returns_ack_without_second_gateway_turn(tmp_path):
    runner, _adapter, state = _runner(tmp_path)
    store = ApprovalStore(tmp_path / "approvals.db")
    continuation = _seed(store, key="duplicate")
    runner._approval_continuation_store = store

    first = await runner._consume_hermes_session_continuation_async(continuation)
    before = len(state.get_messages("session-live"))
    replay = await runner._consume_hermes_session_continuation_async(continuation)

    assert replay == first
    assert len(state.get_messages("session-live")) == before


@pytest.mark.asyncio
async def test_busy_session_turn_lease_retries_without_binding(tmp_path):
    runner, _adapter, state = _runner(tmp_path)
    store = ApprovalStore(tmp_path / "approvals.db")
    continuation = _seed(store, key="busy")
    runner._approval_continuation_store = store
    assert state.try_acquire_session_turn_lease(
        "session-live", "normal-inbound", "normal-turn", 300
    )

    with pytest.raises(ContinuationUnavailable, match="busy"):
        await runner._consume_hermes_session_continuation_async(continuation)

    assert store.get_turn_binding(continuation.id) is None


@pytest.mark.asyncio
async def test_deleted_session_is_terminal_before_execution(tmp_path):
    runner, _adapter, _state = _runner(tmp_path)
    store = ApprovalStore(tmp_path / "approvals.db")
    continuation = _seed(store, key="terminal", session_id="deleted-session")
    runner._approval_continuation_store = store

    with pytest.raises(UnrecoverableContinuation, match="active session"):
        await runner._consume_hermes_session_continuation_async(continuation)


@pytest.mark.asyncio
async def test_running_binding_without_transcript_ack_is_ambiguous(tmp_path):
    runner, _adapter, state = _runner(tmp_path)
    store = ApprovalStore(tmp_path / "approvals.db")
    continuation = _seed(store, key="ambiguous")
    runner._approval_continuation_store = store
    binding = store.get_or_create_turn_binding(
        continuation.id,
        session_id="session-live",
        turn_id="lost-turn",
        history_message_id=state.get_last_message_id("session-live"),
    )
    assert binding.state == "prepared"
    store.mark_turn_binding_running(continuation.id)

    with pytest.raises(UnrecoverableContinuation, match="ambiguous"):
        await runner._consume_hermes_session_continuation_async(continuation)

    assert store.get_turn_binding(continuation.id).state == "ambiguous"
