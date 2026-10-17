"""Lost notifier receipt with growing event batch, using native board and authority.

Agent inference and transport are stand-ins; admission, collection, subscriptions,
and cursor operations use real disposable SQLite stores. No production messages.
"""
import asyncio
import json

import pytest

from tests.waiting import async_wait_until


@pytest.mark.asyncio
async def test_event_retry_with_new_event_has_one_obligation_each(tmp_path, monkeypatch):
    from gateway.config import GatewayConfig, Platform, PlatformConfig
    from gateway.kanban_watchers_notifier import _Collector, _KanbanNotification
    from gateway.platforms.base import SendResult
    from gateway.platforms.event import MessageEvent
    from gateway.run import GatewayRunner
    from gateway.session_authority import initialize_session_authority
    from gateway.session_results import execution_result
    from gateway.wake import WakeNotAccepted
    from hermes_cli import kanban_db as kb, kanban_db_connect as kbc, kanban_db_notify as kbn
    from hermes_state_runtime import list_session_admissions
    from plugins.platforms.discord.adapter import DiscordAdapter
    from gateway import wake

    monkeypatch.setenv('HERMES_KANBAN_DB', str(tmp_path / 'board.db'))
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
    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title='original mutable title', assignee='worker', session_id=sid)
        kbn.add_notify_sub(conn, task_id=tid, platform='discord', chat_id='42', user_id='42',
                           chat_type='dm', delivery_mode='wake')
        with kb.write_txn(conn):
            kb._append_event(conn, tid, 'completed', {'summary': 'E1 immutable handoff' + 'x' * 8000,
                                                      'private_debug': 'DO-NOT-FORWARD-RAW-EVENT'})

    collector = _Collector(runner, kb, notifier_profile=None, gc_due=False, gc_retention_days=7)

    def collect():
        with kbc.connect_closing() as conn:
            sub = kbn.list_notify_subs(conn, tid)[0]
            return collector._claim_for_sub(conn, None, sub)

    def wakes():
        return [r for r in list_session_admissions(authority.db, session_id=sid, pending_only=False)
                if r['request_id'].startswith('[') and json.loads(r['request_id'])[0] == 'kanban-wake']

    original_deliver = wake.deliver_wake
    lost = False

    async def lose_first_receipt(*args, **kwargs):
        nonlocal lost
        await original_deliver(*args, **kwargs)
        if not lost:
            lost = True
            raise WakeNotAccepted('fixture discards receipt after native commit')

    monkeypatch.setattr(wake, 'deliver_wake', lose_first_receipt)
    try:
        first = collect()
        assert first and len(first['events']) == 1
        await _KanbanNotification(runner, first, platform_cls=Platform, sub_fail_counts={}).deliver()
        first_rows = wakes()
        assert lost and len(first_rows) == 1
        assert 'DO-NOT-FORWARD-RAW-EVENT' not in first_rows[0]['payload']['text']
        assert len(first_rows[0]['payload']['text']) < 2000, 'event details must be bounded'
        with kbc.connect_closing() as conn:
            # Mutable task context must not change E1's retry fingerprint.
            conn.execute('UPDATE tasks SET title = ?, assignee = ? WHERE id = ?', ('renamed', 'new worker', tid))
            with kb.write_txn(conn):
                kb._append_event(conn, tid, 'blocked', {'kind': 'needs_input', 'reason': 'E2 owner decision'})
        retry = collect()
        assert retry and len(retry['events']) == 2
        await _KanbanNotification(runner, retry, platform_cls=Platform, sub_fail_counts={}).deliver()
        rows = wakes()
        assert sum('E1 immutable handoff' in r['payload']['text'] for r in rows) == 1, 'E1 must not be admitted again inside a growing batch'
        assert len(rows) == 2, 'E1 replay plus E2 must not create a third obligation or replace E1'
        assert rows[0]['admission_id'] == first_rows[0]['admission_id']
        assert rows[0]['payload'] == first_rows[0]['payload']
        assert 'E2 owner decision' in rows[1]['payload']['text'], 'new event must not be dropped'
        with kbc.connect_closing() as conn:
            sub = kbn.list_notify_subs(conn, tid)[0]
            assert sub['last_event_id'] == retry['cursor']
            assert sub['claim_expires_at'] == 0, 'settled subscription must release its claim lease'
        assert executed == ['HUMAN'], 'a healthy human turn must remain untouched'
    finally:
        finish.set()
    await async_wait_until(lambda: len(executed) == 3, timeout=15, what='one wake for each event')
    await async_wait_until(lambda: all(r['status'] == 'terminal' and r['outcome'] == 'completed' for r in wakes()), timeout=15,
                           what='both native event obligations to settle')
    row = wakes()[0]
    identity = json.loads(row['request_id'])[2]
    await original_deliver(adapter, text=row['payload']['text'], source=source, event_identity=identity)
    assert len(wakes()) == 2, 'terminal replay must reuse the original receipt'
    with pytest.raises(WakeNotAccepted, match='admission_conflict'):
        await original_deliver(adapter, text='changed event under same identity', source=source,
                               event_identity=identity)
    assert len(wakes()) == 2


@pytest.mark.asyncio
async def test_keyed_wake_refuses_legacy_fallback():
    from types import SimpleNamespace
    from gateway.wake import deliver_wake, WakeNotAccepted

    async def forbidden(event):
        pytest.fail('keyed wake must not fall through to volatile adapter admission')

    adapter = SimpleNamespace(handle_message=forbidden)
    with pytest.raises(WakeNotAccepted, match='durable native admission'):
        await deliver_wake(adapter, text='event', source=object(), event_identity='E1')
