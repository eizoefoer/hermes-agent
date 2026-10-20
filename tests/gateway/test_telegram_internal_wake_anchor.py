"""Internal admission IDs are not Telegram transport message IDs."""
import json

import pytest

from gateway.config import Platform
from gateway.platforms.base import _reply_anchor_for_event, _thread_metadata_for_event
from gateway.platforms.event import MessageEvent
from gateway.session import SessionSource


@pytest.mark.parametrize('thread_id,chat_type', [(None, 'dm'), ('71', 'dm'), ('71', 'group')])
def test_internal_wake_identity_is_not_a_telegram_reply(thread_id, chat_type):
    identity = json.dumps(['kanban-wake', 'route', '6233'])
    source = SessionSource(platform=Platform.TELEGRAM, chat_id='42', chat_type=chat_type,
                           thread_id=thread_id)
    event = MessageEvent(text='completed', source=source, internal=True, message_id=identity)
    assert _reply_anchor_for_event(event) is None
    assert event.message_id == identity  # durable admission/deduplication identity survives
    metadata = _thread_metadata_for_event(event) or {}
    assert metadata.get('telegram_reply_to_message_id') is None
    assert metadata.get('thread_id') == thread_id


@pytest.mark.parametrize('internal', [False, True])
def test_real_telegram_message_anchor_survives(internal):
    event = MessageEvent(text='hello', source=SessionSource(platform=Platform.TELEGRAM,
                         chat_id='42', chat_type='dm'), internal=internal, message_id='123')
    assert _reply_anchor_for_event(event) == '123'


def test_other_platform_internal_ids_are_unchanged():
    event = MessageEvent(text='hello', source=SessionSource(platform=Platform.DISCORD,
                         chat_id='42'), internal=True, message_id='opaque-id')
    assert _reply_anchor_for_event(event) == 'opaque-id'
