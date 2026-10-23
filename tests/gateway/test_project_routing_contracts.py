"""Native routing controls, deterministic status, revocation and recovery contracts."""
import asyncio
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.project_routing import route_of, routing_context, route_control, check_delivery
from gateway.session_authorities import owner_scope
from gateway.session_ingress_context import native_callback
from hermes_state_runtime import RuntimeStoreError, list_session_admissions
from tests.waiting import async_wait_until

pytest_plugins = ["tests.gateway.test_project_routing_native"]


async def idle(r):
    await async_wait_until(lambda: not r.adapter._background_tasks and
        all(live.task is None or live.task.done() for live in r.authority.sessions.values()),
        timeout=15, what='native delivery and drains finish')


@pytest.mark.asyncio
async def test_status_has_only_eligible_pointers_and_never_runs_an_agent(routed):
    r = routed
    r.adapter.config.extra['project_routing']['eligible_projects'] = ['property']
    await r.adapter.handle_message(r.incoming('all projects status', 1))
    await idle(r)
    assert not r.calls
    assert len(r.sent) == 1
    assert 'property' in r.sent[0]['text'] and 'heisentick' not in r.sent[0]['text']
    assert not r.authority.db._read_all('SELECT admission_id FROM session_admissions')


@pytest.mark.asyncio
async def test_eligibility_revocation_fences_delivery_and_retried_input(routed):
    r = routed
    original = r.incoming('trading inspect', 1)
    await r.adapter.handle_message(original)
    await idle(r)
    r.adapter.config.extra['project_routing']['eligible_projects'] = ['property']
    with pytest.raises(RuntimeStoreError, match='permission_denied'):
        check_delivery(r.runner, original.source)
    with pytest.raises(RuntimeStoreError, match='permission_denied'):
        await r.adapter.handle_message(r.incoming('trading inspect', 1))
    assert len(r.calls) == 1 and len(r.sent) == 1


@pytest.mark.asyncio
async def test_anchored_controls_refuse_a_replacement_session(routed):
    r = routed
    ev = r.incoming('trading inspect', 1)
    await r.adapter.handle_message(ev)
    await idle(r)
    control = r.incoming('/model', 2)
    control.reply_to_message_id = '701'
    r.runner._canonicalize(control.source)
    route_control(r.runner, control)
    assert route_of(control.source) == route_of(ev.source)
    r.runner.session_store.reset_session(route_of(ev.source).execution_key)
    replacement = r.incoming('/stop', 3)
    replacement.reply_to_message_id = '701'
    r.runner._canonicalize(replacement.source)
    with pytest.raises(RuntimeStoreError, match='stale_project_action'):
        route_control(r.runner, replacement)
    physical = r.incoming('/stop', 4)
    r.runner._canonicalize(physical.source)
    route_control(r.runner, physical)
    assert route_of(physical.source) is None


@pytest.mark.asyncio
async def test_archived_mapping_gets_fresh_isolated_workspace(routed):
    r = routed
    first = r.incoming('trading inspect', 1)
    await r.adapter.handle_message(first)
    await idle(r)
    old = r.calls[0][4]
    r.authority.db._execute_write(lambda conn: conn.execute('UPDATE sessions SET archived=1 WHERE id=?', (old,)))
    await r.adapter.handle_message(r.incoming('trading continue', 2))
    await idle(r)
    assert len(r.calls) == 2 and r.calls[1][4] != old
    assert r.calls[1][2] == r.paths['heisentick']
    assert r.authority.db.get_session(r.calls[1][4])['cwd'] == r.paths['heisentick']


@pytest.mark.asyncio
async def test_queued_native_recovery_uses_receipt_without_private_source_attributes(routed, monkeypatch):
    from gateway.project_routing import route_inbound
    from gateway.session import SessionSource
    r = routed
    ev = r.incoming('trading recovered', 1)
    r.runner._canonicalize(ev.source)
    schedule = r.authority._schedule
    monkeypatch.setattr(r.authority, '_schedule', lambda *a: None)
    with owner_scope(r.authority), native_callback(r.runner, ev, r.home):
        await route_inbound(r.runner, r.authority, ev)
        accepted = await r.authority.admit_native(ev)
    assert not r.calls
    restored = SessionSource.from_dict(ev.source.to_dict())
    r.runner._canonicalize(restored)
    assert route_of(restored) is None
    r.authority.sessions.clear()
    monkeypatch.setattr(r.authority, '_schedule', schedule)
    with owner_scope(r.authority):
        result = await r.authority.recover_native_sessions([(accepted.ref.session_id, restored, r.adapter)])
    assert result == {accepted.ref.session_id: 'ready'}
    await idle(r)
    assert len(r.calls) == len(r.sent) == 1
    assert r.calls[0][2] == r.paths['heisentick']


@pytest.mark.asyncio
async def test_delivery_retry_requires_durable_origin_and_rechecks_revocation(routed, monkeypatch):
    from gateway.delivery_ledger import compute_obligation_id
    from gateway.project_routing import link_delivery_obligation, recovered_delivery_source, record_reply_anchor
    from gateway.platforms.base import SendResult
    r = routed
    original = r.incoming('trading inspect', 1)
    await r.adapter.handle_message(original)
    await idle(r)
    route = route_of(original.source)
    oid = compute_obligation_id(route.execution_key, '1', 'done')
    link_delivery_obligation(r.runner, original, oid)
    row = dict(obligation_id=oid, session_key=route.execution_key, chat_id='42', thread_id='20197',
               profile='default', content='recovered done', needs_marker=False, attempts=0, platform='telegram')
    assert route_of(recovered_delivery_source(r.runner, row)) == route
    with pytest.raises(RuntimeStoreError, match='permission_denied'):
        recovered_delivery_source(r.runner, {**row, 'thread_id': 'other'})
    monkeypatch.setattr(r.runner, '_obligation_adapter', AsyncMock(return_value=r.adapter))
    assert await r.runner._redeliver_claimed_obligations([row]) == 1
    from hermes_state_project_routing import anchor
    assert anchor(r.authority.db, bot=route.bot, profile=route.profile, chat='42', thread='20197',
                  message='702', principal='42')[0]['receipt_id'] == route.receipt_id
    r.adapter.config.extra['project_routing']['eligible_projects'] = []
    with pytest.raises(RuntimeStoreError, match='permission_denied'):
        recovered_delivery_source(r.runner, row)


@pytest.mark.asyncio
async def test_media_and_reconcile_edits_record_reply_anchors(routed, monkeypatch, tmp_path):
    from gateway.platforms.base import SendResult
    from hermes_state_project_routing import anchor
    r = routed
    ev = r.incoming('trading inspect', 1)
    await r.adapter.handle_message(ev)
    await idle(r)
    route = route_of(ev.source)
    doc = tmp_path / 'fixture.pdf'
    doc.write_bytes(b'fixture document')
    send = AsyncMock(return_value=SendResult(success=True, message_id='800'))
    monkeypatch.setattr(r.adapter, 'send_document', send)
    await r.runner._deliver_media_from_response(f'MEDIA:{doc}', ev, r.adapter)
    assert send.await_count == 1
    assert anchor(r.authority.db, bot=route.bot, profile=route.profile, chat='42', thread='20197',
                  message='800', principal='42') is not None
    edit = AsyncMock(return_value=SendResult(success=True, message_id='801'))
    monkeypatch.setattr(r.adapter, 'edit_message', edit)
    stream = SimpleNamespace(message_id='801', _turn_split_delivery=False)
    await r.runner._deliver_queued_first_response('reconciled', ev.source, r.adapter,
        stream_consumer=stream, session_key=route.execution_key, event_message_id='1')
    assert edit.await_count == 1
    assert anchor(r.authority.db, bot=route.bot, profile=route.profile, chat='42', thread='20197',
                  message='801', principal='42') is not None
    r.adapter.config.extra['project_routing']['eligible_projects'] = []
    with pytest.raises(RuntimeStoreError, match='permission_denied'):
        await r.runner._deliver_media_from_response(f'MEDIA:{doc}', ev, r.adapter)
    assert send.await_count == 1


@pytest.mark.asyncio
async def test_choice_revocation_does_not_consume_selection(routed):
    from hermes_state_project_routing import get_choice
    r = routed
    await r.adapter.handle_message(r.incoming('inspect the repository', 1))
    await idle(r)
    button = r.sent[0]['reply_markup'].inline_keyboard[0][0]
    token = button.callback_data.split(':')[1]
    r.adapter.config.extra['project_routing']['eligible_projects'] = []
    query = SimpleNamespace(data=button.callback_data, from_user=SimpleNamespace(id=42, first_name='Owner'),
        message=SimpleNamespace(chat_id=42, message_thread_id=20197, chat=SimpleNamespace(type='private')),
        answer=AsyncMock(), edit_message_reply_markup=AsyncMock())
    await r.adapter._handle_callback_query(SimpleNamespace(callback_query=query), SimpleNamespace())
    assert 'permission_denied' in query.answer.call_args.kwargs['text']
    assert get_choice(r.authority.db, token)['state'] == 'pending'
    assert not r.calls


@pytest.mark.asyncio
async def test_compression_tip_resumes_without_physical_topic_healing(routed, monkeypatch):
    r = routed
    ev = r.incoming('trading inspect', 1)
    await r.adapter.handle_message(ev)
    await idle(r)
    sid = r.calls[0][4]
    route = route_of(ev.source)
    r.authority.db.end_session(sid, 'compression')
    r.authority.db.create_session('compressed-tip', source='telegram', parent_session_id=sid,
                                  session_key=route.execution_key, cwd=route.cwd)
    # Topic recovery/healing must never touch the isolated mapping.
    monkeypatch.setattr(r.runner, '_hmwa_heal_telegram_topic_binding', AsyncMock(side_effect=AssertionError('physical topic heal')))
    await r.adapter.handle_message(r.incoming('trading continue', 2))
    await idle(r)
    assert len(r.calls) == 2 and r.calls[1][4] == 'compressed-tip'
    assert r.calls[0][1] == r.calls[1][1]


@pytest.mark.asyncio
async def test_unknown_work_never_replays_and_followers_remain_queued(routed, monkeypatch):
    from gateway.project_routing import route_inbound
    from hermes_state_runtime import begin_runtime_epoch, claim_session_input, recover_session_inputs
    r = routed
    monkeypatch.setattr(r.authority, '_schedule', lambda *a: None)
    accepted = []
    for mid in (1, 2):
        ev = r.incoming(f'trading input {mid}', mid)
        r.runner._canonicalize(ev.source)
        with owner_scope(r.authority), native_callback(r.runner, ev, r.home):
            await route_inbound(r.runner, r.authority, ev)
            accepted.append(await r.authority.admit_native(ev))
    sid = accepted[0].ref.session_id
    claimed = claim_session_input(r.authority.db, epoch=r.authority.epoch, session_id=sid)
    assert claimed['admission_id'] == accepted[0].admission_id
    epoch = begin_runtime_epoch(r.authority.db, instance_id='replacement')
    recover_session_inputs(r.authority.db, epoch=epoch)
    with pytest.raises(RuntimeStoreError, match='unknown_execution'):
        claim_session_input(r.authority.db, epoch=epoch, session_id=sid)
    rows = list_session_admissions(r.authority.db, session_id=sid)
    assert [row['status'] for row in rows] == ['unknown', 'queued']
    assert not r.calls


@pytest.mark.asyncio
async def test_failed_admission_rolls_back_binding_without_losing_pinned_target(routed, monkeypatch):
    from gateway.project_routing import route_inbound
    from hermes_state_project_routing import receipt
    r = routed
    ev = r.incoming('trading inspect', 1)
    r.runner._canonicalize(ev.source)
    with owner_scope(r.authority), native_callback(r.runner, ev, r.home):
        await route_inbound(r.runner, r.authority, ev)
        route = route_of(ev.source)
        def refuse():
            raise RuntimeStoreError('runtime_draining')
        monkeypatch.setattr(r.authority, '_require_admission_open', refuse)
        with pytest.raises(RuntimeStoreError, match='runtime_draining'):
            await r.authority.admit_native(ev)
    saved = receipt(r.authority.db, route.receipt_id)
    assert saved['state'] == 'pinned' and saved['session_id'] is None
    assert not r.authority.db._read_all('SELECT admission_id FROM session_admissions')
    assert not r.calls


@pytest.mark.asyncio
async def test_photo_burst_never_merges_different_project_actions(routed, tmp_path):
    from gateway.platforms.event import MessageType
    r = routed
    image = tmp_path / 'fixture.png'
    image.write_bytes(b'fixture image')
    for mid, text in ((1, 'trading inspect image'), (2, 'cashflow inspect image')):
        ev = r.incoming(text, mid)
        ev.message_type = MessageType.PHOTO
        ev.media_urls, ev.media_types = [str(image)], ['image/png']
        await r.adapter._route_photo_event(SimpleNamespace(media_group_id='same-album'), ev)
    await idle(r)
    assert len(r.calls) == 2
    assert {call[0].message_id for call in r.calls} == {'1', '2'}
    assert {call[2] for call in r.calls} == set(r.paths.values())
    assert all(len(call[0].media_urls) == 1 for call in r.calls)


@pytest.mark.asyncio
async def test_restart_after_transcription_before_admission_recovers_original_input_once(routed, monkeypatch):
    from gateway.project_routing import route_inbound, recover_project_inputs
    r = routed
    ev = r.incoming('trading recover captured input', 1)
    r.runner._canonicalize(ev.source)
    with owner_scope(r.authority), native_callback(r.runner, ev, r.home):
        await route_inbound(r.runner, r.authority, ev)
    assert not r.authority.db._read_all('SELECT admission_id FROM session_admissions')
    await recover_project_inputs(r.authority)
    await idle(r)
    assert len(r.calls) == 1 and r.calls[0][0].message_id == '1'
    await recover_project_inputs(r.authority)
    await idle(r)
    assert len(r.calls) == 1


@pytest.mark.asyncio
async def test_successful_send_is_finalized_even_when_anchor_write_fails(routed, monkeypatch):
    from gateway.project_routing import link_delivery_obligation
    from gateway.platforms.base import SendResult
    r = routed
    ev = r.incoming('trading inspect', 1)
    await r.adapter.handle_message(ev)
    await idle(r)
    monkeypatch.setattr(r.adapter, '_record_delivery_obligation', AsyncMock(return_value='owed'))
    finalize = AsyncMock()
    monkeypatch.setattr(r.adapter, '_finalize_delivery_obligation', finalize)
    monkeypatch.setattr(r.adapter, '_send_with_retry', AsyncMock(return_value=SendResult(success=True, message_id='900')))
    async def failed_anchor(*args):
        raise OSError('isolated anchor fault')
    monkeypatch.setattr('gateway.project_routing.record_reply_anchor', failed_anchor)
    with pytest.raises(OSError, match='isolated anchor fault'):
        await r.adapter.send_final_ledgered(ev, route_of(ev.source).execution_key, 'delivered', {}, reply_to='1')
    assert finalize.await_count == 1 and finalize.call_args.args[1].success


@pytest.mark.asyncio
async def test_stream_send_and_edit_authorize_each_request_and_anchor_ids(routed, monkeypatch):
    from gateway.stream_consumer import GatewayStreamConsumer
    from gateway.platforms.base import SendResult
    from hermes_state_project_routing import anchor
    r = routed
    ev = r.incoming('trading inspect', 1)
    await r.adapter.handle_message(ev)
    await idle(r)
    route = route_of(ev.source)
    send = AsyncMock(return_value=SendResult(success=True, message_id='810', continuation_message_ids=['811']))
    edit = AsyncMock(return_value=SendResult(success=True, message_id='810'))
    monkeypatch.setattr(r.adapter, 'send', send)
    monkeypatch.setattr(r.adapter, 'edit_message', edit)
    consumer = GatewayStreamConsumer(r.adapter, '42', source=ev.source, metadata={'thread_id': '20197'})
    await consumer._delivery_adapter.send(chat_id='42', content='stream', metadata={'thread_id': '20197'})
    await consumer._delivery_adapter.edit_message(chat_id='42', message_id='810', content='stream final')
    for mid in ('810', '811'):
        assert anchor(r.authority.db, bot=route.bot, profile=route.profile, chat='42', thread='20197',
                      message=mid, principal='42') is not None
    r.adapter.config.extra['project_routing']['eligible_projects'] = []
    with pytest.raises(RuntimeStoreError, match='permission_denied'):
        await consumer._delivery_adapter.edit_message(chat_id='42', message_id='810', content='revoked')
    assert edit.await_count == 1


@pytest.mark.asyncio
async def test_connector_role_stamp_can_be_staged_but_is_rechecked_before_claim(routed):
    r = routed
    ev = r.incoming('trading role-authorized request', 1)
    ev.source.role_authorized = True
    await r.adapter.handle_message(ev)
    await idle(r)
    assert len(r.calls) == 1
