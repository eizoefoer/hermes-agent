"""Project routing authority, persistence and native Telegram execution contracts."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.event import MessageEvent
from gateway.session import SessionSource
from hermes_cli import projects_db
from hermes_state import SessionDB


def register_projects(home, tmp_path):
    paths = {}
    with projects_db.connect_closing(home / 'projects.db') as conn:
        for slug in ('heisentick', 'property'):
            path = tmp_path / slug
            path.mkdir(exist_ok=True)
            projects_db.create_project(conn, name=slug, slug=slug, primary_path=str(path), folders=[str(path)])
            paths[slug] = str(path)
    return paths


def settings(enabled=True):
    return {'enabled': enabled, 'eligible_projects': ['heisentick', 'property'], 'aliases': {'heisentick': ['trading'], 'property': ['cashflow']}}


def event(text='trading: inspect code', mid='17', chat='42', thread='20197', user='42', kind='dm'):
    return MessageEvent(text=text, message_id=mid, source=SessionSource(
        platform=Platform.TELEGRAM, chat_id=chat, chat_type=kind, user_id=user, thread_id=thread))


def test_routing_off_creates_no_schema(tmp_path):
    from gateway.project_routing import pin_project_route
    db = SessionDB(tmp_path / 'state.db')
    try:
        assert pin_project_route(db, event(), config=settings(False), home=tmp_path,
                                 bot='bot', profile='default') is None
        with db._read_ctx() as conn:
            assert not conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'project_route%'").fetchall()
    finally:
        db.close()


def test_receipt_pins_target_under_retry_and_rejects_changed_input(tmp_path):
    from gateway.project_routing import pin_project_route
    from hermes_state_runtime import RuntimeStoreError
    paths = register_projects(tmp_path, tmp_path)
    db = SessionDB(tmp_path / 'state.db')
    kwargs = dict(config=settings(), home=tmp_path, bot='bot', profile='default')
    try:
        first = pin_project_route(db, event(), **kwargs)
        assert first.project_slug == 'heisentick' and first.cwd == paths['heisentick']
        changed = settings()
        changed['aliases'] = {'property': ['trading']}
        assert pin_project_route(db, event(), **{**kwargs, 'config': changed}) == first
        with pytest.raises(RuntimeStoreError, match='admission_conflict'):
            pin_project_route(db, event('cashflow: write code'), **kwargs)
        db.close()
        db = SessionDB(tmp_path / 'state.db')
        assert pin_project_route(db, event(), **kwargs) == first
    finally:
        db.close()


def test_direct_conflict_never_selects_topic_or_quote(tmp_path):
    from gateway.project_routing import pin_project_route, ProjectChoiceRequired
    register_projects(tmp_path, tmp_path)
    db = SessionDB(tmp_path / 'state.db')
    kwargs = dict(config=settings(), home=tmp_path, bot='bot', profile='default')
    try:
        ev = event('trading and cashflow: change both')
        ev.source.chat_topic = 'trading'
        with pytest.raises(ProjectChoiceRequired):
            pin_project_route(db, ev, **kwargs)
        ev = event('please fix this', mid='18')
        ev.reply_to_text = 'trading'
        with pytest.raises(ProjectChoiceRequired):
            pin_project_route(db, ev, **kwargs)
    finally:
        db.close()


def test_lanes_partition_private_history_and_transport(tmp_path):
    from gateway.project_routing import pin_project_route
    register_projects(tmp_path, tmp_path)
    db = SessionDB(tmp_path / 'state.db')
    def pin(ev, bot='bot'):
        return pin_project_route(db, ev, config=settings(), home=tmp_path, bot=bot, profile='default')
    try:
        dm = pin(event())
        assert dm.execution_key == pin(event(mid='18', thread='2')).execution_key
        group = pin(event(mid='19', chat='-99', kind='group'))
        assert group.execution_key != dm.execution_key
        assert pin(event(mid='20', user='43')).execution_key != dm.execution_key
        assert pin(event(), bot='other').execution_key != dm.execution_key
    finally:
        db.close()


@pytest.mark.asyncio
async def test_native_fifo_runs_project_cwd_and_replies_to_immutable_origin(tmp_path, monkeypatch):
    from agent.runtime_cwd import scoped_session_cwd
    from gateway.run import GatewayRunner
    from gateway.session_authority import initialize_session_authority
    from gateway.session_results import execution_result
    from plugins.platforms.telegram.adapter import TelegramAdapter
    from tests.waiting import async_wait_until
    from gateway.project_routing import route_of

    monkeypatch.setenv('TELEGRAM_ALLOWED_USERS', '42')
    from hermes_constants import get_hermes_home
    paths = register_projects(get_hermes_home(), tmp_path)
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
    authority = await initialize_session_authority(runner, profile_id='default', instance_id='router-test')
    runner._wire_adapter_handlers(adapter)
    seen = []
    async def turn(ev, source, key, generation):
        seen.append((key, scoped_session_cwd(), route_of(source)))
        execution_result.get()['result'] = {'final_response': 'done', 'messages': []}
        return 'done'
    monkeypatch.setattr(runner, '_handle_message_with_agent', turn)
    ev = event()
    ev.source = adapter.build_source(chat_id='42', chat_type='dm', user_id='42', thread_id='20197')
    physical = ev.source.to_dict()
    await adapter.handle_message(ev)
    await async_wait_until(lambda: bool(sent), timeout=15, what='project reply')
    assert seen[0][1] == paths['heisentick']
    assert seen[0][0] == seen[0][2].execution_key
    assert ev.source.to_dict() == physical
    assert str(sent[0]['chat_id']) == '42'
    assert sent[0]['message_thread_id'] == 20197
    assert sent[0]['reply_to_message_id'] == 17
    await async_wait_until(lambda: not adapter._background_tasks, timeout=15, what='first reply anchored')
    follow = event('continue', mid='18')
    follow.source = adapter.build_source(chat_id='42', chat_type='dm', user_id='42', thread_id='20197')
    follow.reply_to_message_id = '701'
    follow.reply_to_is_own_message = True
    await adapter.handle_message(follow)
    await async_wait_until(lambda: len(sent) == 2, timeout=15, what='anchored continuation')
    assert seen[1][0] == seen[0][0]
    assert len(authority.sessions) == 1
