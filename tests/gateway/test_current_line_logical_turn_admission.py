"""Current-runtime gateway integration for durable Phase 1 turn admission."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent, MessageType
from gateway.run import GatewayRunner
from gateway.session import SessionEntry, SessionSource
from hermes_state import AsyncSessionDB, SessionDB
from hermes_state import consume_preacquired_logical_turn_lease


def _source() -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        user_id="user-1",
        chat_id="chat-1",
        chat_type="dm",
    )


def _entry(source: SessionSource | None = None) -> SessionEntry:
    source = source or _source()
    return SessionEntry(
        session_key="agent:main:telegram:dm:chat-1",
        session_id="session-1",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        origin=source,
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )


def _runner(tmp_path) -> tuple[GatewayRunner, SessionDB, SessionEntry]:
    db = SessionDB(tmp_path / "state.db")
    db.create_session("session-1", "telegram", user_id="user-1")
    runner = GatewayRunner.__new__(GatewayRunner)
    runner._session_db = AsyncSessionDB(db)
    runner._background_tasks = set()
    runner.adapters = {}
    return runner, db, _entry()


@pytest.mark.asyncio
async def test_gateway_occurrence_is_persisted_and_rehydrated_without_content_dedup(
    tmp_path,
):
    runner, db, entry = _runner(tmp_path)
    first = MessageEvent(text="identical", source=_source(), internal=True)
    second = MessageEvent(
        text="identical",
        source=_source(),
        internal=True,
        message_id="reply-anchor",
    )

    first_admission = await runner.admit_session_event(first, entry, claim=False)
    second_admission = await runner.admit_session_event(second, entry, claim=False)
    restored = runner._message_event_from_logical_turn(
        db.get_logical_turn(first_admission["turn"]["logical_turn_id"])
    )
    replay = await runner.admit_session_event(restored, entry, claim=False)

    assert first.occurrence_id != second.occurrence_id
    assert (
        first_admission["turn"]["logical_turn_id"]
        != second_admission["turn"]["logical_turn_id"]
    )
    assert replay["turn"]["logical_turn_id"] == first_admission["turn"]["logical_turn_id"]
    assert restored.occurrence_id == first.occurrence_id
    assert restored.message_id is None
    assert db.count_logical_turns("session-1") == 2


@pytest.mark.asyncio
async def test_authoritative_transport_replay_id_maps_to_one_gateway_turn(tmp_path):
    runner, db, entry = _runner(tmp_path)
    first = MessageEvent(
        text="hello", source=_source(), platform_update_id=4242
    )
    replay = MessageEvent(
        text="changed rendering", source=_source(), platform_update_id=4242
    )

    first_admission = await runner.admit_session_event(first, entry, claim=False)
    replay_admission = await runner.admit_session_event(replay, entry, claim=False)

    assert (
        replay_admission["turn"]["logical_turn_id"]
        == first_admission["turn"]["logical_turn_id"]
    )
    assert db.count_logical_turns("session-1") == 1


@pytest.mark.asyncio
async def test_real_sessiondb_lease_queues_second_gateway_occurrence(tmp_path):
    runner, db, entry = _runner(tmp_path)
    first = MessageEvent(text="first", source=_source(), message_id="m1")
    second = MessageEvent(text="second", source=_source(), message_id="m2")

    first_claim = await runner.admit_session_event(first, entry)
    second_claim = await runner.admit_session_event(second, entry)

    assert first_claim["outcome"] == "claimed"
    assert second_claim["outcome"] == "busy"
    assert db.get_logical_turn(second._logical_turn_id)["state"] == "queued"
    assert db.get_session_turn_lease("session-1")["holder"] == first_claim["lease"]["holder"]
    await runner._stop_gateway_logical_turn_heartbeat(first)


@pytest.mark.asyncio
async def test_gateway_rebinds_async_claim_lease_in_caller_context(tmp_path):
    """The AIAgent worker must inherit the lease claimed by AsyncSessionDB."""
    runner, db, entry = _runner(tmp_path)
    event = MessageEvent(text="first", source=_source(), message_id="m1")

    claim = await runner.admit_session_event(event, entry)
    handed_off = consume_preacquired_logical_turn_lease(entry.session_id)

    assert claim["outcome"] == "claimed"
    assert handed_off is not None
    assert handed_off["holder"] == claim["lease"]["holder"]
    assert db.get_session_turn_lease(entry.session_id)["holder"] == handed_off["holder"]
    await runner._stop_gateway_logical_turn_heartbeat(event)


@pytest.mark.asyncio
async def test_execution_completion_and_delivery_ack_are_separate(tmp_path):
    runner, db, entry = _runner(tmp_path)
    event = MessageEvent(text="work", source=_source(), message_id="m1")
    claim = await runner.admit_session_event(event, entry)
    assert await event._logical_turn_db.mark_logical_turn_started(
        event._logical_turn_id, claim["attempt_id"]
    )

    await runner._complete_gateway_logical_turn(
        event, {"final_response": "finished"}
    )
    completed = db.get_logical_turn(event._logical_turn_id)
    assert completed["state"] == "completed"
    assert completed["delivery_state"] == "pending"
    assert db.get_session_turn_lease("session-1") is None

    callback = event._logical_turn_delivery_callback
    await callback(event, True, None)
    delivered = db.get_logical_turn(event._logical_turn_id)
    assert delivered["state"] == "completed"
    assert delivered["delivery_state"] == "delivered"


@pytest.mark.asyncio
async def test_busy_gateway_event_is_durable_before_local_fifo(tmp_path):
    runner, db, entry = _runner(tmp_path)

    class Store:
        def lookup_by_session_key(self, session_key):
            return entry if session_key == entry.session_key else None

        def get_or_create_session(self, source, **_kwargs):
            return entry

    adapter = SimpleNamespace(_pending_messages={})
    runner.session_store = Store()
    runner._async_session_store = None
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._adapter_for_source = lambda _source: adapter
    runner._session_key_for_source = lambda _source: entry.session_key
    runner._sessions = {}
    runner._persist_active_agents = lambda: None
    event = MessageEvent(
        text="next", source=_source(), message_id="busy-2"
    )

    assert await runner._persist_busy_gateway_event(event, entry.session_key)
    runner._queue_or_replace_pending_event(entry.session_key, event)

    turn = db.get_logical_turn(event._logical_turn_id)
    assert turn["state"] == "queued"
    assert adapter._pending_messages[entry.session_key] is event


@pytest.mark.asyncio
async def test_startup_drain_rehydrates_same_occurrence_and_skips_terminal(tmp_path):
    runner, db, entry = _runner(tmp_path)
    queued_event = MessageEvent(
        text="recover me",
        source=_source(),
        internal=True,
        session_event_type="gateway-recovered-event",
    )
    queued = await runner.admit_session_event(queued_event, entry, claim=False)
    terminal_event = MessageEvent(
        text="already done", source=_source(), message_id="terminal"
    )
    terminal_claim = await runner.admit_session_event(terminal_event, entry)
    assert await terminal_event._logical_turn_db.mark_logical_turn_started(
        terminal_event._logical_turn_id, terminal_claim["attempt_id"]
    )
    await runner._complete_gateway_logical_turn(
        terminal_event, {"final_response": "done"}
    )

    class Adapter:
        def __init__(self):
            self._active_sessions = {}
            self.events = []

        async def handle_message(self, event):
            self.events.append(event)

    adapter = Adapter()
    runner._adapter_for_source = lambda _source: adapter
    runner._session_key_for_source = lambda _source: entry.session_key
    runner._gateway_logical_turn_dispatching = {}

    assert await runner._drain_gateway_logical_turn_scope() == 1
    assert [event.text for event in adapter.events] == ["recover me"]
    assert adapter.events[0].occurrence_id == queued_event.occurrence_id
    assert adapter.events[0]._logical_turn_id == queued["turn"]["logical_turn_id"]
    assert db.get_logical_turn(terminal_event._logical_turn_id)["state"] == "completed"


@pytest.mark.asyncio
async def test_startup_drain_does_not_treat_local_active_cache_as_ownership(tmp_path):
    runner, db, entry = _runner(tmp_path)
    event = MessageEvent(
        text="recover despite stale local cache",
        source=_source(),
        internal=True,
        session_event_type="gateway-recovered-event",
    )
    admitted = await runner.admit_session_event(event, entry, claim=False)

    class Adapter:
        def __init__(self):
            self._active_sessions = {entry.session_key: object()}
            self.events = []

        def _heal_stale_session_lock(self, _session_key):
            return False

        async def handle_message(self, recovered):
            self.events.append(recovered)

    adapter = Adapter()
    runner._adapter_for_source = lambda _source: adapter
    runner._session_key_for_source = lambda _source: entry.session_key
    runner._gateway_logical_turn_dispatching = {}

    assert db.get_session_turn_lease(entry.session_id) is None
    assert await runner._drain_gateway_logical_turn_scope() == 1
    assert len(adapter.events) == 1
    assert (
        adapter.events[0]._logical_turn_id
        == admitted["turn"]["logical_turn_id"]
    )


@pytest.mark.asyncio
async def test_persistent_gateway_admission_fails_closed_without_sessiondb(tmp_path):
    runner = GatewayRunner.__new__(GatewayRunner)
    runner._session_db = None
    runner._background_tasks = set()
    with pytest.raises(Exception, match="refusing unmanaged"):
        await runner.admit_session_event(
            MessageEvent(text="do not execute", source=_source()),
            _entry(),
        )
