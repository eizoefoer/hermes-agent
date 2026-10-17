"""Characterize lost-receipt retries against real authority/SQLite, not a mock queue.

The two rows asserted here are a RELEASE BLOCKER, not desired once-only behavior.
Only a disposable profile and a stand-in agent turn are used; no remote transport.
"""
import asyncio
import json

import pytest

from tests.waiting import async_wait_until


@pytest.mark.asyncio
async def test_lost_receipt_retry_currently_duplicates_native_wake(monkeypatch):
    from gateway.config import GatewayConfig, Platform, PlatformConfig
    from gateway.platforms.base import SendResult
    from gateway.platforms.event import MessageEvent
    from gateway.run import GatewayRunner
    from gateway.session_authority import initialize_session_authority
    from gateway.session_automation import admit_route_wake
    from gateway.session_results import execution_result
    from hermes_state_runtime import list_session_admissions
    from plugins.platforms.discord.adapter import DiscordAdapter

    monkeypatch.setenv('DISCORD_ALLOWED_USERS', '42')
    runner = GatewayRunner(GatewayConfig())
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token='fixture-token', typing_indicator=False))

    async def send(chat_id, content, reply_to=None, metadata=None):
        return SendResult(success=True, message_id='sent')

    monkeypatch.setattr(adapter, 'send', send)
    adapter.gateway_runner = runner
    runner.adapters = {Platform.DISCORD: adapter}
    authority = await initialize_session_authority(runner, profile_id='default', instance_id='owner')
    runner._wire_adapter_handlers(adapter)
    source = adapter.build_source(chat_id='42', chat_type='dm', user_id='42')
    key = runner.session_store._generate_session_key(source)
    running, finish = asyncio.Event(), asyncio.Event()
    executed = []

    async def turn(event, source, quick_key, run_generation):
        executed.append(event.text)
        if event.text == 'HUMAN':
            running.set()
            await finish.wait()
        captured = execution_result.get()
        if captured is not None:
            captured['result'] = {'final_response': 'reply', 'messages': []}
        return 'reply'

    monkeypatch.setattr(runner, '_handle_message_with_agent', turn)
    await adapter.handle_message(MessageEvent(text='HUMAN', source=source, message_id='human'))
    await asyncio.wait_for(running.wait(), 10)
    sid = authority.logical_owner(runner.session_store.peek_session_id(key))
    try:
        # Ignore the first successful receipt, modelling its loss after durable commit.
        assert await admit_route_wake(runner, adapter, source, 'SAME BATCH', 'kanban-wake')
        first = list_session_admissions(authority.db, session_id=sid, pending_only=False)
        assert await admit_route_wake(runner, adapter, source, 'SAME BATCH', 'kanban-wake')
        rows = list_session_admissions(authority.db, session_id=sid, pending_only=False)
        wakes = [r for r in rows if r['request_id'].startswith('[')
                 and json.loads(r['request_id'])[0] == 'kanban-wake']
        assert len(rows) == len(first) + 1
        assert len(wakes) == 2, rows
        assert len({r['admission_id'] for r in wakes}) == 2
        assert len({r['request_id'] for r in wakes}) == 2
        assert all(r['payload']['text'] == 'SAME BATCH' for r in wakes)
        assert all(r['status'] == 'queued' for r in wakes)
        assert executed == ['HUMAN'], 'wake admission must not interrupt healthy work'
    finally:
        finish.set()
    await async_wait_until(lambda: executed.count('SAME BATCH') == 2, timeout=15,
                           what='both duplicate wakes to execute after human completion')
