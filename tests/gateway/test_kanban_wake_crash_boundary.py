"""A notifier retry must reuse its durable native wake admission.

Real gateway, Discord adapter, native SessionAuthority/FIFO and SQLite. Inference
and transport are inert; an injected interruption stands in for notifier death.
"""
import asyncio

import pytest


class NotifierInterrupted(BaseException):
    """Interrupt after wake admission, before outbox cursor settlement."""


@pytest.mark.asyncio
async def test_unsettled_wake_retry_reuses_one_native_admission(monkeypatch, tmp_path):
    from gateway.config import GatewayConfig, Platform, PlatformConfig
    from gateway.kanban_watchers_notifier import _KanbanNotification
    from gateway.platforms.base import SendResult
    from gateway.platforms.event import MessageEvent
    from gateway.run import GatewayRunner
    from gateway.session_authority import initialize_session_authority
    from gateway.session_results import execution_result
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc
    from hermes_cli import kanban_db_notify as kbn
    from hermes_state_runtime import list_session_admissions
    from plugins.platforms.discord.adapter import DiscordAdapter
    from tests.waiting import async_wait_until

    monkeypatch.setenv('DISCORD_ALLOWED_USERS', '42')
    monkeypatch.setenv('HERMES_KANBAN_DB', str(tmp_path / 'wake-board.db'))
    runner = GatewayRunner(GatewayConfig())
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token='fixture-token', typing_indicator=False))

    async def send(*args, **kwargs):
        return SendResult(success=True, message_id='inert')

    monkeypatch.setattr(adapter, 'send', send)
    adapter.gateway_runner = runner
    runner.adapters = {Platform.DISCORD: adapter}
    authority = await initialize_session_authority(runner, profile_id='default', instance_id='wake-probe')
    runner._wire_adapter_handlers(adapter)
    source = adapter.build_source(chat_id='42', chat_type='dm', user_id='42')
    key = runner.session_store._generate_session_key(source)
    entered, finish = asyncio.Event(), asyncio.Event()
    executed = []

    async def turn(event, source, quick_key, run_generation):
        executed.append(event.text)
        if event.text == 'HOLD':
            entered.set()
            await finish.wait()
        captured = execution_result.get()
        if captured is not None:
            captured['result'] = {'final_response': 'inert', 'messages': []}
        return 'inert'

    monkeypatch.setattr(runner, '_handle_message_with_agent', turn)
    await adapter.handle_message(MessageEvent(text='HOLD', source=source, message_id='hold'))
    await asyncio.wait_for(entered.wait(), 10)
    sid = runner.session_store.peek_session_id(key)
    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title='isolated wake', assignee='fixture', session_id=key)
        kbn.add_notify_sub(conn, task_id=tid, platform='discord', chat_id='42',
                           chat_type='dm', user_id='42', delivery_mode='wake')
        kb.complete_task(conn, tid, summary='same result', force=True)

    def claim():
        with kbc.connect_closing() as conn:
            sub = kbn.list_notify_subs(conn, tid)[0]
            metadata = {}
            old, cursor, events = kbn.claim_unseen_events_for_sub(
                conn, task_id=tid, platform='discord', chat_id='42',
                kinds=['completed'], claim_metadata=metadata)
            assert len(events) == 1
            sub['_claim_expires_at'] = metadata['claim_expires_at']
            return dict(sub=sub, old_cursor=old, cursor=cursor, events=events,
                        task=kb.get_task(conn, tid), board=None)

    async def interrupt(_self):
        raise NotifierInterrupted()

    original_advance = _KanbanNotification.advance
    monkeypatch.setattr(_KanbanNotification, 'advance', interrupt)
    try:
        first = claim()
        with pytest.raises(NotifierInterrupted):
            await _KanbanNotification(runner, first, platform_cls=Platform, sub_fail_counts={}).deliver()
        first_rows = list_session_admissions(authority.db, session_id=sid, pending_only=False)
        assert len(first_rows) == 2  # original human turn plus committed wake
        assert executed == ['HOLD']  # healthy existing turn not interrupted
        monkeypatch.setattr('time.time', lambda: first['sub']['_claim_expires_at'] + 1)
        monkeypatch.setattr(_KanbanNotification, 'advance', original_advance)
        retry = claim()
        assert [e.id for e in retry['events']] == [e.id for e in first['events']]
        await _KanbanNotification(runner, retry, platform_cls=Platform, sub_fail_counts={}).deliver()
        rows = list_session_admissions(authority.db, session_id=sid, pending_only=False)
        wakes = [r for r in rows if r['principal_id'] == 'automation:' + key]
        assert len(wakes) == 1, 'retry of the same source event must reuse its native admission'
        assert wakes[0]['request_id'] == first_rows[1]['request_id']
        assert executed == ['HOLD']
    finally:
        finish.set()
        await async_wait_until(lambda: len(executed) >= 2, timeout=15,
                               what='isolated human and wake drain to finish')
