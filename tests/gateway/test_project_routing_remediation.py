"""Independent routing review: neutral status, action origins and ingress lifecycle."""
import asyncio
import json
import time

import pytest

from gateway.project_routing import recover_project_inputs, route_of, routing_context
from gateway.session_authorities import owner_scope
from tests.gateway.test_project_routing_contracts import idle

pytest_plugins = ['tests.gateway.test_project_routing_native']


@pytest.mark.asyncio
@pytest.mark.parametrize('ingress', ['adapter', 'runner'])
async def test_neutral_status_leaves_physical_clarification_waiting(routed, ingress):
    from tools import clarify_gateway as clarify
    r = routed
    ev = r.incoming('all projects status', 1)
    r.runner._canonicalize(ev.source)
    key = r.runner._session_key_for_source(ev.source)
    pending = clarify.register('neutral-status', key, 'What should I do?', None)
    try:
        if ingress == 'adapter':
            await r.adapter.handle_message(ev)
        else:
            from gateway.session_ingress_context import native_callback
            with owner_scope(r.authority), native_callback(r.runner, ev, r.home):
                reply = await r.runner._handle_message(ev)
            from gateway.session_ingress import deliver_response
            await deliver_response(r.adapter, ev, key, reply)
        await idle(r)
        assert pending.response is None and not pending.event.is_set()
        assert not r.calls and len(r.sent) == 1
        assert 'property' in r.sent[0]['text']
    finally:
        clarify.resolve_gateway_clarify(pending.clarify_id, clarify.CANCELLED)
        clarify.wait_for_response(pending.clarify_id, 0.01)


@pytest.mark.asyncio
@pytest.mark.parametrize('producer', ['wake', 'process'])
async def test_async_origin_survives_later_admission_from_another_topic(routed, producer):
    from gateway.wake import deliver_wake
    from gateway.platforms.event import MessageEvent
    r = routed
    first = r.incoming('trading first action', 1)
    await r.adapter.handle_message(first)
    await idle(r)
    await r.adapter.handle_message(r.incoming('trading second action', 2, thread='20198'))
    await idle(r)
    if producer == 'wake':
        await deliver_wake(r.adapter, source=first.source, text='first completed', event_identity='first-done')
    else:
        key = route_of(first.source).execution_key
        sid = r.calls[0][4]
        event = MessageEvent(text='first completed', source=first.source, internal=True,
            message_id='process-done', metadata={'gateway_session_key': key, 'gateway_session_id': sid})
        with owner_scope(r.authority):
            await r.authority.admit_automation(r.adapter, event, 'process-done')
    await idle(r)
    assert len(r.calls) == 3
    assert r.calls[-1][0].source.thread_id == '20197'
    assert r.sent[-1]['message_thread_id'] == 20197
    assert route_of(r.calls[-1][0].source).receipt_id == route_of(first.source).receipt_id


@pytest.mark.asyncio
async def test_chosen_before_crash_recovers_original_attachment_once_with_current_auth(routed, tmp_path):
    from hermes_state_project_routing import choose, get_choice
    from gateway.project_choices import choice_identity
    from gateway.platforms.event import MessageType
    r = routed
    doc = tmp_path / 'original.txt'
    doc.write_text('original attachment')
    original = r.incoming('inspect this repository', 17)
    original.message_type = MessageType.DOCUMENT
    original.media_urls, original.media_types = [str(doc)], ['text/plain']
    await r.adapter.handle_message(original)
    await idle(r)
    token = r.sent[0]['reply_markup'].inline_keyboard[0][0].callback_data.split(':')[1]
    _home, bot, profile, config = routing_context(r.runner, original.source)
    choose(r.authority.db, token=token, index=0, identity=choice_identity(original.source, bot, profile), now=time.time())
    assert get_choice(r.authority.db, token)['state'] == 'chosen'
    # No callback resumes the input after the selection commit. Recovery must reauthorize it.
    config['eligible_projects'] = []
    await recover_project_inputs(r.authority)
    await idle(r)
    assert not r.calls
    config['eligible_projects'] = ['heisentick', 'property']
    entered, release = asyncio.Event(), asyncio.Event()
    r.gate[original.text] = (entered, release)
    await recover_project_inputs(r.authority)
    await asyncio.wait_for(entered.wait(), 15)
    assert len(r.calls) == 1
    recovered = r.calls[0][0]
    assert recovered.text == original.text and recovered.message_id == '17'
    assert recovered.media_types == ['text/plain']
    from pathlib import Path
    assert Path(recovered.media_urls[0]).read_text() == 'original attachment'
    release.set()
    await idle(r)
    await recover_project_inputs(r.authority)
    await idle(r)
    assert len(r.calls) == 1


@pytest.mark.asyncio
async def test_terminal_routed_media_can_be_collected_with_native_retention(routed, tmp_path):
    from gateway.platforms.event import MessageType
    from gateway.session_ingress_media import release_unheld_media
    from hermes_state_runtime import list_session_admissions
    r = routed
    doc = tmp_path / 'finished.txt'
    doc.write_text('finished')
    ev = r.incoming('trading inspect file', 1)
    ev.message_type = MessageType.DOCUMENT
    ev.media_urls, ev.media_types = [str(doc)], ['text/plain']
    await r.adapter.handle_message(ev)
    await idle(r)
    row = list_session_admissions(r.authority.db, session_id=r.calls[0][4], pending_only=False)[0]
    assert row['status'] == 'terminal'
    refs = row['payload']['native_text_v1']['media']
    # This is the native age-based cleanup seam; history retention is tested separately.
    release_unheld_media(r.authority.db, refs, retain_history=False)
    from pathlib import Path
    assert not Path(refs[0]['path']).exists()


@pytest.mark.asyncio
async def test_expired_choice_releases_media_but_pending_choice_holds_it(routed, tmp_path):
    from gateway.platforms.event import MessageType
    from gateway.session_ingress_media import release_unheld_media
    r = routed
    doc = tmp_path / 'choice.txt'
    doc.write_text('choice')
    ev = r.incoming('inspect file', 1)
    ev.message_type = MessageType.DOCUMENT
    ev.media_urls, ev.media_types = [str(doc)], ['text/plain']
    await r.adapter.handle_message(ev)
    await idle(r)
    payload = json.loads(r.authority.db._read_all('SELECT payload_json FROM project_route_inputs')[0][0])
    refs = payload['native_text_v1']['media']
    assert release_unheld_media(r.authority.db, refs, retain_history=False) == 0
    r.authority.db._execute_write(lambda conn: conn.execute('UPDATE project_route_choices SET expires=0'))
    assert release_unheld_media(r.authority.db, refs, retain_history=False) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('action', [0, 1])
async def test_process_notification_uses_its_triggering_admission(routed, action):
    r = routed
    inputs = [r.incoming('trading first', 1), r.incoming('trading second', 2, thread='20198')]
    for ev in inputs:
        await r.adapter.handle_message(ev)
        await idle(r)
    origin = inputs[action]
    completion = {'type': 'completion', 'session_id': 'process-' + str(action),
        'session_key': route_of(origin.source).execution_key, 'parent_session_id': r.calls[action][4],
        'platform': 'telegram', 'chat_type': 'dm', 'chat_id': '42', 'user_id': '42',
        'thread_id': origin.source.thread_id, 'message_id': origin.message_id, 'started_at': 1.0}
    with owner_scope(r.authority):
        assert await r.runner._inject_watch_notification('process completed', completion) is True
    await idle(r)
    assert len(r.calls) == 3
    assert route_of(r.calls[-1][0].source).receipt_id == route_of(origin.source).receipt_id
    assert r.sent[-1]['message_thread_id'] == int(origin.source.thread_id)


@pytest.mark.asyncio
async def test_expired_choice_before_admission_is_collectible_but_captured_input_is_held(routed, tmp_path):
    from gateway.project_routing import route_inbound
    from gateway.session_ingress_context import native_callback
    from gateway.session_ingress_media import release_unheld_media, sweep_settled_media
    from agent.provider_media import MEDIA_CACHE_MAX_AGE_HOURS
    from pathlib import Path
    import os
    r = routed
    doc = tmp_path / 'captured.txt'
    doc.write_text('captured')
    ev = r.incoming('inspect file', 1)
    from gateway.platforms.event import MessageType
    ev.message_type = MessageType.DOCUMENT
    ev.media_urls, ev.media_types = [str(doc)], ['text/plain']
    r.runner._canonicalize(ev.source)
    with owner_scope(r.authority), native_callback(r.runner, ev, r.home):
        await route_inbound(r.runner, r.authority, ev)
    payload = json.loads(r.authority.db._read_all('SELECT payload_json FROM project_route_inputs')[0][0])
    refs = payload['native_text_v1']['media']
    retained = Path(refs[0]['path'])
    assert release_unheld_media(r.authority.db, refs, retain_history=False) == 0
    r.authority.db._execute_write(lambda conn: conn.execute('UPDATE project_route_choices SET expires=0'))
    os.utime(retained, (1, 1))
    assert sweep_settled_media(r.authority.db, now=MEDIA_CACHE_MAX_AGE_HOURS * 3600 + 10) == 1
    assert not retained.exists()
    await recover_project_inputs(r.authority)
    await idle(r)
    assert not r.calls and not r.sent


@pytest.mark.asyncio
async def test_captured_and_queued_project_media_keep_native_ownership(routed, tmp_path, monkeypatch):
    from gateway.project_routing import route_inbound
    from gateway.session_ingress_context import native_callback
    from gateway.session_ingress_media import release_unheld_media
    from hermes_state_runtime import cancel_session_input, get_session_admission
    from gateway.platforms.event import MessageType
    r = routed
    doc = tmp_path / 'queued.txt'
    doc.write_text('queued')
    ev = r.incoming('trading inspect file', 1)
    ev.message_type = MessageType.DOCUMENT
    ev.media_urls, ev.media_types = [str(doc)], ['text/plain']
    r.runner._canonicalize(ev.source)
    monkeypatch.setattr(r.authority, '_schedule', lambda *args: None)
    with owner_scope(r.authority), native_callback(r.runner, ev, r.home):
        await route_inbound(r.runner, r.authority, ev)
        payload = json.loads(r.authority.db._read_all('SELECT payload_json FROM project_route_inputs')[0][0])
        refs = payload['native_text_v1']['media']
        assert release_unheld_media(r.authority.db, refs, retain_history=False) == 0
        admitted = await r.authority.admit_native(ev)
    assert get_session_admission(r.authority.db, admission_id=admitted.admission_id)['status'] == 'queued'
    assert release_unheld_media(r.authority.db, refs, retain_history=False) == 0
    cancel_session_input(r.authority.db, epoch=r.authority.epoch, admission_id=admitted.admission_id)
    assert release_unheld_media(r.authority.db, refs, retain_history=False) == 1


@pytest.mark.asyncio
async def test_session_deletion_releases_completed_project_media_after_history_retention(routed, tmp_path):
    from pathlib import Path
    from gateway.platforms.event import MessageType
    r = routed
    doc = tmp_path / 'history.txt'
    doc.write_text('history context')
    ev = r.incoming('trading remember file', 1)
    ev.message_type = MessageType.DOCUMENT
    ev.media_urls, ev.media_types = [str(doc)], ['text/plain']
    entered, release = asyncio.Event(), asyncio.Event()
    r.gate[ev.text] = (entered, release)
    await r.adapter.handle_message(ev)
    await asyncio.wait_for(entered.wait(), 15)
    retained = Path(r.calls[0][0].media_urls[0])
    sid = r.calls[0][4]
    r.authority.db.append_message(sid, 'user', 'Remember file: ' + str(retained))
    release.set()
    await idle(r)
    assert retained.exists(), 'native transcript ownership must retain completed media'
    r.authority.db.delete_session(sid)
    assert not retained.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['missing_trigger', 'invalid_authority'])
async def test_process_origin_refusal_never_borrows_latest_topic(routed, monkeypatch, failure):
    from hermes_state_runtime import RuntimeStoreError
    r = routed
    first = r.incoming('trading original', 101)
    second = r.incoming('trading later', 102, thread='20198')
    for ev in [first, second]:
        await r.adapter.handle_message(ev)
        await idle(r)
    key = route_of(first.source).execution_key
    monkeypatch.setattr(r.runner, '_get_cached_session_source', lambda _key: second.source)
    event = {'session_key': key, 'message_id': first.message_id}
    if failure == 'missing_trigger':
        event.pop('message_id')
    else:
        def refuse(*args, **kwargs):
            raise RuntimeStoreError('permission_denied')
        monkeypatch.setattr('gateway.project_routing.validate_route', refuse)
    with owner_scope(r.authority):
        assert r.runner._build_process_event_source(event) is None
