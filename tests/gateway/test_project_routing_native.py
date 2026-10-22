"""Real native FIFO, runner session resolution and fake Telegram transport boundaries."""
import asyncio
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.event import MessageEvent, MessageType
from gateway.project_routing import route_of, routing_context, validate_route
from gateway.session import build_session_context
from gateway.session_ingress_context import native_callback
from gateway.session_results import execution_result
from hermes_state_runtime import RuntimeStoreError, list_session_admissions
from tests.gateway.test_project_routing import register_projects, settings
from tests.waiting import async_wait_until


@pytest_asyncio.fixture
async def routed(tmp_path, monkeypatch):
    from gateway.run import GatewayRunner
    from gateway.session_authority import initialize_session_authority
    from plugins.platforms.telegram.adapter import TelegramAdapter
    from hermes_constants import get_hermes_home
    monkeypatch.setenv('TELEGRAM_ALLOWED_USERS', '42,43')
    import telegram
    monkeypatch.setattr(telegram, 'InlineKeyboardButton', lambda text, callback_data: SimpleNamespace(text=text, callback_data=callback_data))
    monkeypatch.setattr(telegram, 'InlineKeyboardMarkup', lambda rows: SimpleNamespace(inline_keyboard=rows))
    home = get_hermes_home()
    paths = register_projects(home, tmp_path)
    pc = PlatformConfig(enabled=True, token='fixture-token', typing_indicator=False,
                        extra={'project_routing': settings()})
    runner = GatewayRunner(GatewayConfig(platforms={Platform.TELEGRAM: pc}))
    adapter = TelegramAdapter(pc)
    sent = []
    async def send(**kw):
        sent.append(kw)
        return SimpleNamespace(message_id=700 + len(sent))
    adapter._bot = SimpleNamespace(send_message=send)
    adapter.gateway_runner = runner
    runner.adapters = {Platform.TELEGRAM: adapter}
    authority = await initialize_session_authority(runner, profile_id='default', instance_id='test-router')
    runner._wire_adapter_handlers(adapter)
    calls, gate = [], {}
    async def turn(ev, source, key, generation):
        from agent.runtime_cwd import resolve_agent_cwd
        from tools.terminal_scope import terminal_env
        resolved = await runner._hmwa_resolve_session(ev, source)
        assert resolved is not None
        source, entry, actual_key = resolved
        tokens = runner._set_session_env(build_session_context(source, runner.config, entry))
        try:
            calls.append((ev, actual_key, str(resolve_agent_cwd()), terminal_env('TERMINAL_CWD'), entry.session_id))
            if ev.text in gate:
                gate[ev.text][0].set()
                await gate[ev.text][1].wait()
            result = {'final_response': 'done: ' + ev.text, 'messages': []}
            execution_result.get()['result'] = result
            return result['final_response']
        finally:
            runner._clear_session_env(tokens)
    monkeypatch.setattr(runner, '_handle_message_with_agent', turn)
    def incoming(text, mid, thread='20197', chat='42', user='42', kind='dm'):
        return MessageEvent(text=text, message_id=str(mid), source=adapter.build_source(
            chat_id=chat, chat_type=kind, user_id=user, thread_id=thread))
    obj = SimpleNamespace(runner=runner, adapter=adapter, authority=authority, calls=calls,
                          sent=sent, paths=paths, home=home, gate=gate, incoming=incoming)
    yield obj
    for entered, release in gate.values():
        release.set()
    await async_wait_until(lambda: all(live.task is None or live.task.done() for live in authority.sessions.values()), timeout=15, what='native drains finish')
    await adapter.cancel_background_tasks()


@pytest.mark.asyncio
async def test_two_origins_one_target_fifo_and_two_targets_concurrent(routed):
    r = routed
    entered, release = asyncio.Event(), asyncio.Event()
    r.gate['trading first'] = (entered, release)
    await r.adapter.handle_message(r.incoming('trading first', 1))
    await asyncio.wait_for(entered.wait(), 15)
    # A second physical topic targets the same project; its independent adapter guard cannot
    # defeat the canonical authority's one FIFO/lease. A different project must still progress.
    await r.adapter.handle_message(r.incoming('trading second', 2, thread='20198'))
    await r.adapter.handle_message(r.incoming('cashflow third', 3))
    await async_wait_until(lambda: len(r.calls) == 2, timeout=15, what='independent project runs')
    assert [c[0].text for c in r.calls] == ['trading first', 'cashflow third']
    release.set()
    await async_wait_until(lambda: len(r.calls) == 3 and len(r.sent) == 3, timeout=15, what='FIFO follower delivered')
    assert r.calls[2][1] == r.calls[0][1] != r.calls[1][1]
    assert r.calls[0][2:4] == (r.paths['heisentick'], r.paths['heisentick'])
    assert r.calls[1][2:4] == (r.paths['property'], r.paths['property'])
    assert {str(s['message_thread_id']) for s in r.sent} == {'20197', '20198'}


@pytest.mark.asyncio
async def test_unknown_input_shows_durable_buttons_and_selection_runs_original_once(routed):
    r = routed
    original = r.incoming('inspect the repository', 17)
    await r.adapter.handle_message(original)
    await async_wait_until(lambda: bool(r.sent), timeout=15, what='project choice')
    assert not r.calls
    keyboard = r.sent[0]['reply_markup']
    data = keyboard.inline_keyboard[0][0].callback_data
    assert data.startswith('pr:'), (r.sent, data)
    query = SimpleNamespace(data=data, from_user=SimpleNamespace(id=42, first_name='Owner'),
                            message=SimpleNamespace(chat_id=42, message_id=701, message_thread_id=20197,
                                                    chat=SimpleNamespace(type='private')),
                            answer=AsyncMock(), edit_message_reply_markup=AsyncMock())
    await r.adapter._handle_callback_query(SimpleNamespace(callback_query=query), SimpleNamespace())
    assert query.answer.call_args.kwargs.get('text') == 'Project selected.', query.answer.call_args
    await async_wait_until(lambda: len(r.calls) == 1, timeout=15, what='selected input executes')
    assert r.calls[0][0].text == original.text
    assert r.calls[0][0].message_id == '17'
    assert r.calls[0][2] == r.paths[keyboard.inline_keyboard[0][0].text]
    await r.adapter._handle_callback_query(SimpleNamespace(callback_query=query), SimpleNamespace())
    assert len(r.calls) == 1


@pytest.mark.asyncio
async def test_reply_anchor_and_async_completion_keep_action_project_and_destination(routed):
    from gateway.wake import deliver_wake
    r = routed
    ev = r.incoming('trading inspect', 17)
    await r.adapter.handle_message(ev)
    await async_wait_until(lambda: len(r.sent) == 1, timeout=15, what='human reply')
    await deliver_wake(r.adapter, source=ev.source, text='background finished', event_identity='action-complete-1')
    await async_wait_until(lambda: len(r.sent) == 2, timeout=15, what='async completion reply')
    follow = r.incoming('continue', 18)
    follow.reply_to_message_id = '702'
    follow.reply_to_is_own_message = True
    await r.adapter.handle_message(follow)
    await async_wait_until(lambda: len(r.sent) == 3, timeout=15, what='async anchor continuation')
    assert len({c[1] for c in r.calls}) == 1
    assert all(c[2] == r.paths['heisentick'] for c in r.calls)
    assert all(str(s['chat_id']) == '42' and s['message_thread_id'] == 20197 for s in r.sent)


@pytest.mark.asyncio
async def test_group_cannot_resume_private_history_or_use_another_principal_anchor(routed):
    r = routed
    await r.adapter.handle_message(r.incoming('trading private', 17))
    await async_wait_until(lambda: len(r.sent) == 1, timeout=15, what='private reply')
    group = r.incoming('trading group', 18, chat='-99', kind='group')
    group.reply_to_message_id = '701'
    await r.adapter.handle_message(group)
    await async_wait_until(lambda: len(r.sent) == 2, timeout=15, what='group isolated reply')
    assert r.calls[0][1] != r.calls[1][1]
    stranger = r.incoming('continue', 19, user='43')
    stranger.reply_to_message_id = '701'
    await r.adapter.handle_message(stranger)
    await async_wait_until(lambda: len(r.sent) == 3, timeout=15, what='other principal choice')
    assert len(r.calls) == 2 and 'reply_markup' in r.sent[2]


@pytest.mark.asyncio
async def test_voice_routes_after_native_transcription_and_keeps_media(routed, tmp_path, monkeypatch):
    r = routed
    audio = tmp_path / 'voice.ogg'
    audio.write_bytes(b'fixture audio')
    ev = r.incoming('', 17)
    ev.message_type = MessageType.VOICE
    ev.media_urls, ev.media_types = [str(audio)], ['audio/ogg']
    async def enrich(text, paths):
        return 'trading inspect voice request', ['trading inspect voice request']
    monkeypatch.setattr(r.runner, '_enrich_message_with_transcription', enrich)
    await r.adapter.handle_message(ev)
    await async_wait_until(lambda: bool(r.sent), timeout=15, what='voice routed reply')
    admitted = r.calls[0][0]
    assert admitted.media_urls and admitted.media_types == ['audio/ogg']
    assert admitted.message_type == MessageType.VOICE
    assert r.calls[0][2] == r.paths['heisentick']


@pytest.mark.asyncio
async def test_project_preclaim_rejects_revocation_and_changed_destination(routed):
    r = routed
    ev = r.incoming('trading inspect', 17)
    await r.adapter.handle_message(ev)
    await async_wait_until(lambda: bool(r.sent), timeout=15, what='routed reply')
    route = route_of(ev.source)
    home, bot, profile, _ = routing_context(r.runner, ev.source)
    from gateway.session_identity import replace_source
    with pytest.raises(RuntimeStoreError, match='permission_denied'):
        validate_route(r.authority.db, replace_source(ev.source, chat_id='-99', chat_type='group'),
                       asdict(route), home=home, bot=bot, profile=profile, config=settings())
    from hermes_cli import projects_db
    with projects_db.connect_closing(home / 'projects.db') as conn:
        projects_db.archive_project(conn, route.project_id)
    with pytest.raises(RuntimeStoreError, match='project_unavailable'):
        validate_route(r.authority.db, ev.source, asdict(route), home=home, bot=bot, profile=profile, config=settings())
