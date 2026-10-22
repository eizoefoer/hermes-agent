"""Durable native Telegram project choice; callback data carries no execution authority."""
from dataclasses import asdict
import json
from pathlib import Path
import time

from hermes_state_runtime import RuntimeStoreError
from hermes_state_project_routing import create_choice, choose, choice_payload, get_choice


def input_identity(event, bot, profile):
    from gateway.project_routing import _hash
    source = event.source
    return _hash([bot, profile, str(source.user_id), source.chat_id, source.thread_id, str(event.message_id)])


def digest_input(event):
    from gateway.project_routing import _hash
    import hashlib
    def identity(value):
        path = Path(value)
        if not path.is_file():
            return value
        with path.open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest()
    return _hash([event.text, event.reply_to_message_id, event.source.chat_type, event.media_types,
                  [identity(value) for value in event.media_urls]])


def choice_identity(source, bot, profile):
    return {'bot': bot, 'profile': profile, 'principal': str(source.user_id),
            'chat_id': str(source.chat_id), 'thread_id': source.thread_id, 'chat_type': source.chat_type}


def prepare_choice(db, event, *, home, bot, profile, config):
    from hermes_cli import projects_db
    registry = Path(home) / 'projects.db'
    if not registry.is_file():
        raise RuntimeStoreError('project_unavailable')
    from gateway.project_routing import eligible_projects
    projects = eligible_projects(home, config)
    options = [{'id': p.id, 'slug': p.slug} for p in projects if p.primary_path and Path(p.primary_path).is_dir()][:16]
    if not options:
        raise RuntimeStoreError('project_unavailable')
    row = create_choice(db, receipt_id=input_identity(event, bot, profile), digest=digest_input(event),
                        identity=choice_identity(event.source, bot, profile), options=options, now=time.time())
    descriptor = {'token': row['token'], 'options': json.loads(row['options_json'])}
    event.source._project_choice = descriptor
    return descriptor


def pending_choice(source):
    found = getattr(source, '_project_choice', None)
    return found if isinstance(found, dict) else None


async def deliver_choice(adapter, event, text, metadata, record_delivery):
    descriptor = pending_choice(event.source)
    if descriptor is None:
        return False
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    def build():
        buttons = [[InlineKeyboardButton(option['slug'], callback_data=f"pr:{descriptor['token']}:{i}")]
                   for i, option in enumerate(descriptor['options'])]
        return adapter.format_message(text), InlineKeyboardMarkup(buttons), None
    result = await adapter._send_prompt('project choice', event.source.chat_id, metadata, build,
                                       thread_id=event.source.thread_id)
    record_delivery(result)
    return True


async def handle_callback(adapter, query, data, cb):
    if not await adapter._callback_authorized(query, cb, 'This project choice is not authorized.'):
        return
    runner = adapter.gateway_runner
    source = adapter.build_source(chat_id=str(cb['chat_id']), chat_type='dm' if cb['chat_type'] == 'private' else 'group',
                                  user_id=str(query.from_user.id), thread_id=str(cb['thread_id']) if cb['thread_id'] else None)
    runner._canonicalize(source)
    from gateway.project_routing import routing_context
    from gateway.session_authorities import authority_for_home, owner_scope
    home, bot, profile, config = routing_context(runner, source)
    authority = authority_for_home(runner, home)
    if authority is None or config.get('enabled') is not True:
        await query.answer(text='Project routing is unavailable.')
        return
    try:
        _, token, raw_index = data.split(':')
        from gateway.project_routing import eligible_projects
        def authorize(project_id):
            if project_id not in {p.id for p in eligible_projects(home, config)}:
                raise RuntimeStoreError('permission_denied')
        with owner_scope(authority):
            row, _selected = choose(authority.db, token=token, index=int(raw_index),
                            identity=choice_identity(source, bot, profile), now=time.time(), authorize=authorize)
            payload = choice_payload(authority.db, row)
            from gateway.session_envelope import restore_native
            original = restore_native(payload, runner)
            # The original authored input, attachments and input/action ID are unchanged.
            delattr(original.source, '_project_choice')
            from gateway.project_routing import pin_project_route
            pin_project_route(authority.db, original, config=config, home=home, bot=bot, profile=profile)
            original._gateway_pending_stt_text = original.text
            original._gateway_pending_stt_transcripts = []
            from gateway.session_ingress_context import native_callback
            original._turn_marker_handoff = True
            with native_callback(runner, original, home, getattr(adapter, '_owner_profile', None)):
                reply = await runner._handle_message(original)
            from gateway.session_ingress import deliver_response
            await deliver_response(adapter, original, runner._session_key_for_source(original.source), reply)
        await query.answer(text='Project selected.')
        await query.edit_message_reply_markup(reply_markup=None)
    except (RuntimeStoreError, ValueError) as exc:
        await query.answer(text=f'Project choice refused: {str(exc)[:100]}')
