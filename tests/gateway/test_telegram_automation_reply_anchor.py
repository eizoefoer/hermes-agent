"""Immutable native automation IDs are not Telegram transport message IDs.

Exercise actual reply routing and Telegram sends with an isolated fake transport.
No gateway restart, inference, provider call or real Telegram delivery.
"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import _reply_anchor_for_event, _thread_metadata_for_event
from gateway.platforms.event import MessageEvent
from gateway.session import SessionSource
from plugins.platforms.telegram.adapter import TelegramAdapter


@pytest.mark.asyncio
@pytest.mark.parametrize("chat_type,thread_id", [("dm", None), ("dm", "20197"), ("group", "20197")])
async def test_synthetic_wake_keeps_destination_without_using_admission_id_as_reply(chat_type, thread_id):
    source = SessionSource(platform=Platform.TELEGRAM, chat_id="12345", chat_type=chat_type,
                           user_id="42", thread_id=thread_id)
    identity = json.dumps(["kanban-wake", "owning-route", "event:6233"], separators=(",", ":"))
    event = MessageEvent(text="completed event", source=source, internal=True, message_id=identity)
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="fixture-token"))
    adapter._bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=782)))
    adapter._connected = True
    adapter._rich_available = False
    anchor = _reply_anchor_for_event(event)
    metadata = _thread_metadata_for_event(event)
    result = await adapter.send(source.chat_id, "continuation reply", reply_to=anchor, metadata=metadata)
    assert result.success, result.error
    assert anchor is None
    assert event.message_id == identity, "immutable admission/dedupe identity must remain untouched"
    adapter._bot.send_message.assert_awaited_once()
    sent = adapter._bot.send_message.call_args.kwargs
    assert str(sent["chat_id"]) == source.chat_id
    assert sent.get("reply_to_message_id") is None
    if thread_id:
        assert sent["message_thread_id"] == int(thread_id)
    else:
        assert sent.get("message_thread_id") is None


@pytest.mark.parametrize("internal", [False, True])
def test_real_telegram_message_reply_is_preserved(internal):
    source = SessionSource(platform=Platform.TELEGRAM, chat_id="12345", chat_type="dm", thread_id="20197")
    event = MessageEvent(text="human", source=source, message_id="463", internal=internal)
    assert _reply_anchor_for_event(event) == "463"
    assert _thread_metadata_for_event(event)["telegram_reply_to_message_id"] == "463"


def test_non_telegram_internal_reply_behavior_is_unchanged():
    source = SessionSource(platform=Platform.DISCORD, chat_id="12345", chat_type="dm")
    event = MessageEvent(text="wake", source=source, internal=True, message_id="opaque-id")
    assert _reply_anchor_for_event(event) == "opaque-id"


def test_explicit_redirect_anchor_still_wins():
    source = SessionSource(platform=Platform.TELEGRAM, chat_id="12345", chat_type="dm")
    event = MessageEvent(text="wake", source=source, internal=True, message_id="synthetic")
    event.reply_anchor_override = "777"
    assert _reply_anchor_for_event(event) == "777"


@pytest.mark.asyncio
async def test_native_wake_replay_sends_through_original_telegram_route(tmp_path, monkeypatch):
    """Real durable admission/replay and Telegram send; only inference/network are fake."""
    from gateway.config import GatewayConfig
    from gateway.run import GatewayRunner
    from gateway.session_authority import initialize_session_authority
    from gateway.session_results import execution_result
    from gateway.wake import deliver_wake
    from hermes_state_runtime import list_session_admissions
    from tests.waiting import async_wait_until

    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "42")
    runner = GatewayRunner(GatewayConfig())
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="fixture-token", typing_indicator=False))
    sent = AsyncMock(return_value=SimpleNamespace(message_id=782))
    adapter._bot = SimpleNamespace(send_message=sent)
    adapter.gateway_runner = runner
    runner.adapters = {Platform.TELEGRAM: adapter}
    authority = await initialize_session_authority(runner, profile_id="default", instance_id="test-owner")
    runner._wire_adapter_handlers(adapter)
    source = adapter.build_source(chat_id="12345", chat_type="dm", user_id="42", thread_id="20197")
    key = runner.session_store._generate_session_key(source)
    executed = []

    async def turn(event, source, quick_key, run_generation):
        executed.append(event)
        captured = execution_result.get()
        reply = "reply to " + event.text
        if captured is not None:
            captured["result"] = {"final_response": reply, "messages": []}
        return reply

    monkeypatch.setattr(runner, "_handle_message_with_agent", turn)
    await adapter.handle_message(MessageEvent(text="HUMAN", source=source, message_id="463"))
    await async_wait_until(lambda: bool(sent.await_count), timeout=15, what="human reply")
    sid = authority.logical_owner(runner.session_store.peek_session_id(key))
    await deliver_wake(adapter, source=source, text="TASK FINISHED", event_identity="immutable-event-6233")

    def rows():
        return [row for row in list_session_admissions(authority.db, session_id=sid, pending_only=False)
                if row["request_id"].startswith('["kanban-wake",')]

    await async_wait_until(lambda: len(rows()) == 1 and rows()[0]["status"] == "terminal",
                           timeout=15, what="native wake and transport settlement")
    # Terminal execution is committed before the no-waiter transport is sent.
    await async_wait_until(lambda: sent.await_count == 2 and not authority.pending_deliveries,
                           timeout=15, what="settled native wake reply delivery")
    assert rows()[0]["outcome"] == "completed"
    assert len(executed) == 2 and executed[1].internal
    assert executed[1].message_id == rows()[0]["request_id"]
    assert sent.await_count == 2
    wake_send = sent.call_args_list[1].kwargs
    assert str(wake_send["chat_id"]) == "12345"
    assert wake_send["message_thread_id"] == 20197
    assert wake_send.get("reply_to_message_id") is None
    assert sent.call_args_list[0].kwargs["reply_to_message_id"] == 463
    await deliver_wake(adapter, source=source, text="TASK FINISHED", event_identity="immutable-event-6233")
    assert len(rows()) == 1 and sent.await_count == 2, "replayed event must not re-admit/re-send"
