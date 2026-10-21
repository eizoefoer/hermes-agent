"""Per-message reply anchors do not own a background completion's conversation."""
from copy import deepcopy

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.event import MessageEvent
from gateway.run import GatewayRunner
from gateway.session_authority import SessionAuthority, initialize_session_authority
from gateway.session_identity import replace_source
from gateway.session_ingress_context import native_callback
from hermes_constants import get_hermes_home
from hermes_state_runtime import (RuntimeStoreError, claim_session_input,
                                  list_session_admissions, settle_session_input)
from plugins.platforms.discord.adapter import DiscordAdapter


@pytest.mark.asyncio
@pytest.mark.parametrize('changed_user', [False, True])
async def test_completion_ignores_reply_anchor_but_preserves_sender_ownership(tmp_path, monkeypatch, changed_user):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('DISCORD_ALLOWED_USERS', '42')
    monkeypatch.setattr(SessionAuthority, '_schedule', lambda self, ref: None)
    runner = GatewayRunner(GatewayConfig(sessions_dir=tmp_path / 'sessions'))
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token='fixture-token', typing_indicator=False))
    runner.adapters = {Platform.DISCORD: adapter}
    from hermes_state import SessionDB
    db = SessionDB(tmp_path / 'state.db')
    runner.session_store._db = db
    runner._session_db = db
    authority = await initialize_session_authority(runner, profile_id='default', instance_id='reply-identity', db=db)
    try:
        runner._wire_adapter_handlers(adapter)
        source = replace_source(adapter.build_source(chat_id='42', chat_type='dm', user_id='42'),
                                message_id='human-message')
        human = MessageEvent(text='finish the task', source=source, message_id='human-message')
        with native_callback(runner, human, get_hermes_home()):
            receipt = await authority.admit_native(human)
        sid = receipt.ref.session_id
        claim = claim_session_input(authority.db, epoch=authority.epoch, session_id=sid)
        settle_session_input(authority.db, epoch=authority.epoch, admission_id=receipt.admission_id,
                             generation=claim['generation'], outcome='completed')
        route = runner.session_store._generate_session_key(source)
        completion_source = replace_source(source, message_id=None,
                                           user_id='other-user' if changed_user else '42')
        event = MessageEvent(text='task completed', source=completion_source, internal=True,
                             metadata={'gateway_session_key': route, 'gateway_session_id': sid})
        if changed_user:
            with pytest.raises(RuntimeStoreError, match='admission_conflict'):
                await authority.admit_automation(adapter, event, 'completion-one')
            assert len(list_session_admissions(authority.db, session_id=sid, pending_only=False)) == 1
        else:
            first = await authority.admit_automation(adapter, event, 'completion-one')
            replay = deepcopy(event)
            replay.source = replace_source(completion_source, message_id='another-stale-anchor')
            assert (await authority.admit_automation(adapter, replay, 'completion-one')).admission_id == first.admission_id
            rows = list_session_admissions(authority.db, session_id=sid, pending_only=False)
            assert len(rows) == 2
            assert not rows[-1]['payload']['native_text_v1']['source'].get('message_id')
    finally:
        runner.session_store.close_all_db_handles()
        runner.close_all_session_db_handles()
