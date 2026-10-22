"""Opt-in native project execution identities, independent of Telegram reply origins."""
from contextlib import contextmanager
import asyncio
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re

from hermes_state_runtime import RuntimeStoreError
from hermes_state_project_routing import anchor, ensure_schema, pin, receipt


class ProjectChoiceRequired(ValueError):
    """No single project has explicit routing authority; no work may execute."""


@dataclass(frozen=True)
class ProjectRoute:
    receipt_id: str
    project_id: str
    project_slug: str
    cwd: str
    execution_key: str
    bot: str
    profile: str
    principal: str
    visibility: str
    chat_id: str
    thread_id: str | None
    input_id: str
    reply_id: str | None
    reason: str
    version: int = 1


def route_of(source):
    found = getattr(source, '_project_route', None)
    return found if isinstance(found, ProjectRoute) else None


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _visibility(source):
    return 'private' if source.chat_type == 'dm' else 'group:' + str(source.chat_id)


def _authored(event):
    # Quote/forward payloads are context, never routing instructions. Telegram forwards keep
    # their text in event.text, so the provider's forwarding stamp must exclude it as a whole.
    if getattr(event, '_project_routing_authored', True) is False:
        return ''
    raw = event.raw_message
    if raw is not None and (getattr(raw, 'forward_origin', None) or getattr(raw, 'forward_date', None)):
        return ''
    return '\n'.join(line for line in event.text.splitlines() if not line.lstrip().startswith('>'))


def _matches(text, projects, config):
    selected = set()
    aliases = config.get('aliases', {})
    references = config.get('references', {})
    for project in projects:
        terms = [project.slug, project.name, *aliases.get(project.slug, [])]
        exact_refs = references.get(project.slug, [])
        if (any(re.search(r'(?<![\w-])' + re.escape(term) + r'(?![\w-])', text, re.IGNORECASE)
                for term in terms if isinstance(term, str) and term)
                or any(ref in text for ref in exact_refs if isinstance(ref, str) and ref)):
            selected.add(project.id)
    return selected


def eligible_projects(home, config):
    """Resolve an explicit allowlist to registry IDs; aliases never grant access."""
    from hermes_cli import projects_db
    registry = Path(home) / 'projects.db'
    selectors = config.get('eligible_projects', [])
    if not registry.is_file() or not isinstance(selectors, (list, dict)):
        return []
    with projects_db.connect_closing(registry) as conn:
        projects = [projects_db.get_project(conn, key) for key in selectors if isinstance(key, str)]
    return [p for p in projects if p and not p.archived]


def pin_project_route(db, event, *, config, home, bot, profile):
    """Freeze the decision synchronously before a connector/media await. Retry reads the pin,
    never reclassifies under new aliases. Only registry-owned paths enter execution."""
    if not isinstance(config, dict) or config.get('enabled') is not True:
        return None
    source = event.source
    if not source.user_id or not event.message_id or source.platform.value != 'telegram':
        raise ProjectChoiceRequired('Choose a project with an authenticated Telegram input.')
    principal = str(source.user_id)
    from gateway.project_choices import input_identity, digest_input
    rid = input_identity(event, bot, profile)
    digest = digest_input(event)
    ensure_schema(db)
    old = receipt(db, rid)
    if old:
        if old['input_digest'] != digest:
            raise RuntimeStoreError('admission_conflict')
        result = ProjectRoute(**json.loads(old['descriptor_json']))
        validate_route(db, source, asdict(result), home=home, bot=bot, profile=profile, config=config)
        return result
    from hermes_cli import projects_db
    registry = Path(home) / 'projects.db'
    if not registry.is_file():
        raise ProjectChoiceRequired('Register a project with hermes project create first.')
    projects = eligible_projects(home, config)
    with projects_db.connect_closing(registry) as conn:
        registered = projects_db.list_projects(conn, include_archived=True)
    direct = _matches(_authored(event), registered, config)
    if direct - {p.id for p in projects}:
        raise ProjectChoiceRequired('An explicitly named project is not eligible for routing.')
    anchored = anchor(db, bot=bot, profile=profile, chat=str(source.chat_id), thread=source.thread_id,
                      message=str(event.reply_to_message_id), principal=principal) if event.reply_to_message_id else None
    selected = set(direct)
    from hermes_state_project_routing import selected_project
    explicit = selected_project(db, rid)
    if anchored:
        selected.add(anchored[0]['project_id'])
    reason = 'reply' if anchored else 'authored'
    if explicit:
        selected = {explicit}
        reason = 'selection'
    if not selected:
        selected = _matches(source.chat_topic or '', projects, config)
        reason = 'topic'
    if len(selected) != 1:
        raise ProjectChoiceRequired('Choose one project explicitly; this input has no unique project authority.')
    project = next((p for p in projects if p.id in selected), None)
    if project is None or not project.primary_path or not Path(project.primary_path).is_dir():
        raise ProjectChoiceRequired('The chosen project needs an available primary folder.')
    visibility = _visibility(source)
    lane = _hash([bot, profile, principal, visibility, project.id])
    from gateway.session import _session_key_namespace
    namespace = _session_key_namespace(source.profile if source.profile else (profile if profile != 'default' else None))
    result = ProjectRoute(rid, project.id, project.slug, str(Path(project.primary_path).resolve()),
                          namespace + ':telegram:project:' + lane, bot, profile, principal,
                          visibility, str(source.chat_id), source.thread_id, str(event.message_id),
                          event.reply_to_message_id, reason)
    result = ProjectRoute(**pin(db, receipt_id=rid, digest=digest, descriptor=asdict(result)))
    source._project_route = result
    return result


def validate_route(db, source, descriptor, *, home, bot, profile, session_id=None, config=None):
    """Independent preclaim/delivery authority: the selector's verdict grants nothing."""
    route = ProjectRoute(**descriptor)
    if (route.bot != bot or route.profile != profile or route.principal != str(source.user_id)
            or route.visibility != _visibility(source) or route.chat_id != str(source.chat_id)
            or route.thread_id != source.thread_id):
        raise RuntimeStoreError('permission_denied')
    saved = receipt(db, route.receipt_id)
    if saved is None or json.loads(saved['descriptor_json']) != descriptor:
        raise RuntimeStoreError('admission_conflict')
    if session_id is not None:
        tip = db.get_compression_tip(saved['session_id']) if saved['session_id'] else None
        if session_id not in (saved['session_id'], tip):
            raise RuntimeStoreError('admission_conflict')
    from hermes_cli import projects_db
    with projects_db.connect_closing(Path(home) / 'projects.db') as conn:
        project = projects_db.get_project(conn, route.project_id)
    if (not project or project.archived or not project.primary_path
            or str(Path(project.primary_path).resolve()) != route.cwd or not Path(route.cwd).is_dir()):
        raise RuntimeStoreError('project_unavailable')
    if config is None or config.get('enabled') is not True or route.project_id not in {p.id for p in eligible_projects(home, config)}:
        raise RuntimeStoreError('permission_denied')
    source._project_route = route
    return route


def routing_context(runner, source):
    adapter = runner._intake_adapter_for(source) or runner._delivery_adapter_for(source)
    from gateway.session_identity import identity_of
    from hermes_constants import get_hermes_home
    identity = identity_of(source)
    home = identity.runtime_home if identity else get_hermes_home()
    profile = identity.runtime_profile if identity else (source.profile or 'default')
    owner = identity.transport_profile if identity else 'default'
    token = getattr(getattr(adapter, 'config', None), 'token', None)
    # Only hash string tokens; tests may pass mocks or None
    if isinstance(token, str) and token:
        bot = _hash([owner, token])
    else:
        bot = owner  # fallback for tests/disabled routing
    config = getattr(getattr(adapter, 'config', None), 'extra', {}).get('project_routing', {})
    return home, bot, profile, config


async def route_inbound(runner, authority, event):
    if event.source.platform.value != 'telegram' or event.internal or event.get_command():
        return None
    home, bot, profile, config = routing_context(runner, event.source)
    if config.get('enabled') is not True:
        return None
    if route_of(event.source) is not None or getattr(event.source, '_project_choice', None) is not None:
        return route_of(event.source)
    from gateway.project_choices import input_identity, digest_input
    from gateway.session_envelope import _snapshot_native
    from gateway.session_ingress_context import capture_provenance
    from hermes_state_project_routing import capture_input, prepare_input
    rid = input_identity(event, bot, profile)
    # Staging records the connector's observed role stamp; native admission and claim
    # independently await fresh roles before granting any execution authority.
    payload = _snapshot_native(runner, event, capture_provenance(runner, event),
                               fresh_roles=bool(event.source.role_authorized))
    payload['project_routing_authored'] = bool(_authored(event)) or not bool(event.text)
    captured = capture_input(authority.db, rid, digest_input(event), payload)
    text = captured['prepared_text']
    if text is None:
        text, _ = await runner._transcribe_pending_audio_event_once(event)
        text = prepare_input(authority.db, rid, text if text is not None else event.text)
    event.text = text
    if re.fullmatch(r'(?:all |project |all projects )?status[?.!]*', _authored(event).strip(), re.IGNORECASE):
        event.source._project_status = project_status(authority.db, event.source, home, bot, profile, config)
        from hermes_state_project_routing import settle_input
        settle_input(authority.db, rid)
        return None
    try:
        return pin_project_route(authority.db, event, config=config, home=home, bot=bot, profile=profile)
    except ProjectChoiceRequired:
        from gateway.project_choices import prepare_choice
        prepare_choice(authority.db, event, home=home, bot=bot, profile=profile, config=config)
        return None


@contextmanager
def execution_scope(source):
    route = route_of(source)
    if route is None:
        yield
        return
    from agent.runtime_cwd import set_session_cwd, reset_session_cwd
    from tools.terminal_scope import default_terminal_scope, get_terminal_scope, set_terminal_scope, reset_terminal_scope
    terminal = dict(get_terminal_scope() or default_terminal_scope())
    terminal['TERMINAL_CWD'] = route.cwd
    ct = set_session_cwd(route.cwd)
    tt = set_terminal_scope(terminal)
    try:
        yield
    finally:
        reset_terminal_scope(tt)
        reset_session_cwd(ct)


def admission_guard(payload, session_id, authorize):
    envelope = payload.get('native_text_v1', {})
    descriptor = envelope.get('project_route')
    choice = envelope.get('project_choice')
    if descriptor is None and choice is None:
        return authorize
    def guard(conn):
        authorize(conn)
        if descriptor is not None:
            from hermes_state_project_routing import bind
            bind(conn, descriptor, session_id)
        if choice is not None:
            from hermes_state_project_routing import bind_choice
            source = envelope['source']
            principal = 'messaging:' + json.dumps([source.get('profile'), source['platform'], source['chat_id'],
                                                   source['thread_id'], source['user_id']], separators=(',', ':'))
            bind_choice(conn, choice['token'], session_id=session_id, principal_id=principal,
                        request_id=envelope['event']['message_id'])
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='project_route_inputs'").fetchone():
            rid = descriptor['receipt_id'] if descriptor else conn.execute(
                'SELECT receipt_id FROM project_route_choices WHERE token=?', (choice['token'],)).fetchone()[0]
            conn.execute("UPDATE project_route_inputs SET state='admitted' WHERE receipt_id=?", (rid,))
    return guard


def check_native_project_route(runner, payload, session_id, available_source):
    from gateway.session_authorities import active_authority
    authority = active_authority(runner)
    descriptor = payload['native_text_v1']['project_route']
    from gateway.session_envelope import restore_native_source
    source = restore_native_source(payload, runner)
    home, bot, profile, config = routing_context(runner, source)
    if config.get('enabled') is not True:
        raise RuntimeStoreError('project_routing_disabled')
    validate_route(authority.db, source, descriptor, home=home, bot=bot, profile=profile, session_id=session_id, config=config)
    # A lane may have multiple physical DM topic origins. Never widen visibility or principal.
    if (str(available_source.user_id) != descriptor['principal']
            or _visibility(available_source) != descriptor['visibility']):
        raise RuntimeStoreError('permission_denied')
    available_source._project_route = ProjectRoute(**descriptor)


async def record_reply_anchor(runner, event, result):
    route = route_of(event.source)
    if route is None or not (result.success or result.message_id or getattr(result, 'continuation_message_ids', ())):
        return
    from gateway.session_authorities import active_authority, authority_for_home
    home, bot, profile, config = routing_context(runner, event.source)
    authority = authority_for_home(runner, home) or active_authority(runner)
    validate_route(authority.db, event.source, asdict(route), home=home, bot=bot, profile=profile, config=config)
    from hermes_state_project_routing import record_anchor
    for mid in (result.message_id, *getattr(result, 'continuation_message_ids', ())):
        if mid:
            await asyncio.to_thread(record_anchor, authority.db, asdict(route), mid)


def adapter_routing_enabled(adapter, event):
    return (event.source.platform.value == 'telegram' and not event.internal
            and isinstance(adapter.config.extra.get('project_routing'), dict)
            and adapter.config.extra['project_routing'].get('enabled') is True)


def check_delivery(runner, source):
    route = route_of(source)
    if route is None:
        return
    from gateway.session_authorities import authority_for_home
    home, bot, profile, config = routing_context(runner, source)
    authority = authority_for_home(runner, home)
    if authority is None or not runner._is_user_authorized_for_source(source):
        raise RuntimeStoreError('permission_denied')
    validate_route(authority.db, source, asdict(route), home=home, bot=bot, profile=profile, config=config)


def routing_enabled_source(runner, source):
    if source.platform.value != 'telegram':
        return False
    if route_of(source) is not None:
        return True
    _, _, _, config = routing_context(runner, source)
    return isinstance(config, dict) and config.get('enabled') is True


def route_control(runner, event):
    """Only an exact persisted bot reply can explicitly address a project control command."""
    if (event.source.platform.value != 'telegram' or event.internal
            or event.get_command() not in {'stop', 'new', 'reset', 'compress', 'model'}
            or not event.reply_to_message_id):
        return
    home, bot, profile, config = routing_context(runner, event.source)
    if not isinstance(config, dict) or config.get('enabled') is not True:
        return
    from gateway.session_authorities import authority_for_home
    authority = authority_for_home(runner, home)
    if authority is None:
        raise RuntimeStoreError('project_routing_unavailable')
    ensure_schema(authority.db)
    found = anchor(authority.db, bot=bot, profile=profile, chat=str(event.source.chat_id),
                   thread=event.source.thread_id, message=str(event.reply_to_message_id),
                   principal=str(event.source.user_id))
    if not found:
        return
    descriptor, sid, generation, action_id = found
    row = authority.db.get_session(sid)
    tip = authority.db.get_compression_tip(sid) or sid
    entry = runner.session_store.lookup_by_session_key(descriptor['execution_key'])
    if (entry is None or entry.session_id != tip or row is None
            or generation is None or row['runtime_generation'] != generation):
        raise RuntimeStoreError('stale_project_action')
    if event.get_command() == 'stop':
        from hermes_state_runtime import list_session_admissions
        active = [row['admission_id'] for row in list_session_admissions(authority.db, session_id=sid)
                  if row['status'] == 'started']
        if active and active != [action_id]:
            raise RuntimeStoreError('stale_project_action')
    validate_route(authority.db, event.source, descriptor, home=home, bot=bot, profile=profile, session_id=tip, config=config)


async def prepare_adapter_event(adapter, event):
    """Authenticated preparation before the adapter chooses a busy lane."""
    if not adapter_routing_enabled(adapter, event):
        return
    runner = adapter.gateway_runner
    if runner is None or not runner._is_user_authorized_for_source(event.source):
        raise RuntimeStoreError('permission_denied')
    if event.get_command():
        route_control(runner, event)
        return
    from gateway.session_authorities import authority_for_home, owner_scope
    home, _, _, _ = routing_context(runner, event.source)
    authority = authority_for_home(runner, home)
    if authority is None:
        raise RuntimeStoreError('project_routing_unavailable')
    from gateway.session_ingress_context import native_callback
    with owner_scope(authority), native_callback(runner, event, home, getattr(adapter, '_owner_profile', None)):
        key = runner._session_key_for_source(event.source)
        reply = await runner._hm_pending_reply_intercepts(event, event.source, key)
        if reply is not None:
            from gateway.session_ingress import deliver_response
            await deliver_response(adapter, event, key, reply)
            return True
        if await runner._route_plaintext_approval_while_busy(event, key):
            return True
        await route_inbound(runner, authority, event)
    return False


def project_status(db, source, home, bot, profile, config):
    """Only bounded native pointers; no repository files, transcripts or model tools."""
    lines = ['Project status:']
    for project in sorted(eligible_projects(home, config), key=lambda p: p.slug)[:16]:
        lane = _hash([bot, profile, str(source.user_id), _visibility(source), project.id])
        with db._read_ctx() as conn:
            row = conn.execute("SELECT session_id FROM project_route_receipts WHERE json_extract(descriptor_json,'$.execution_key') LIKE ? AND session_id IS NOT NULL ORDER BY rowid DESC LIMIT 1",
                               ('%:telegram:project:' + lane,)).fetchone()
        tip = (db.get_compression_tip(row[0]) or row[0]) if row else None
        session = db.get_session(tip) if tip else None
        ended = session and (session.get('end_reason') or session.get('archived'))
        status = 'no routed session' if not session else ('ended' if ended else 'available')
        lines.append(f'{project.slug[:80]}: {status}')
    return '\n'.join(lines) if len(lines) > 1 else 'No eligible projects are configured.'


def link_delivery_obligation(runner, event, obligation_id):
    route = route_of(event.source)
    if route is None:
        return
    from gateway.session_authorities import authority_for_home
    from hermes_state_project_routing import link_delivery
    home, _, _, _ = routing_context(runner, event.source)
    authority = authority_for_home(runner, home)
    link_delivery(authority.db, obligation_id, asdict(route), event.source.to_dict())


def recovered_delivery_source(runner, row):
    """A project ledger row requires an independent durable origin association."""
    if ':telegram:project:' not in row['session_key']:
        return None
    from types import SimpleNamespace
    from gateway.session import SessionSource
    from gateway.session_authorities import all_authorities
    from hermes_state_project_routing import delivery_pointer
    matches = [found for authority in all_authorities(runner)
               if (found := delivery_pointer(authority.db, row['obligation_id'])) is not None]
    if len(matches) != 1:
        raise RuntimeStoreError('permission_denied')
    descriptor, encoded = matches[0]
    if (descriptor['execution_key'] != row['session_key'] or descriptor['chat_id'] != str(row['chat_id'])
            or (descriptor['thread_id'] or '') != (row.get('thread_id') or '')):
        raise RuntimeStoreError('permission_denied')
    source = runner._restored_source(SimpleNamespace(origin=SessionSource.from_dict(encoded), transport_profile=row['profile']))
    source._project_route = ProjectRoute(**descriptor)
    check_delivery(runner, source)
    return source


def restore_project_origin(runner, entry, source):
    """Restore a pointer only for a server-owned isolated lane, then reauthorize."""
    key = getattr(entry, 'session_key', '')
    if ':telegram:project:' not in key:
        return
    from gateway.session_authorities import authority_for_home
    home, bot, profile, config = routing_context(runner, source)
    authority = authority_for_home(runner, home)
    if authority is None:
        raise RuntimeStoreError('permission_denied')
    row = authority.db.get_session(entry.session_id)
    encoded = json.loads(row.get('origin_json') or '{}') if row else {}
    receipt_id = encoded.get('project_route_receipt')
    saved = receipt(authority.db, receipt_id) if receipt_id else None
    if saved is None:
        raise RuntimeStoreError('permission_denied')
    descriptor = json.loads(saved['descriptor_json'])
    if descriptor['execution_key'] != key:
        raise RuntimeStoreError('permission_denied')
    validate_route(authority.db, source, descriptor, home=home, bot=bot, profile=profile, config=config)


def project_handoff(source):
    route = route_of(source)
    if route is None:
        return None
    pointers = [name for name in ('AGENTS.md', 'HANDOFF.md', 'README.md') if (Path(route.cwd) / name).is_file()]
    return (f'Isolated project session: {route.project_slug}; workspace: {route.cwd}. '
            f'Repository handoff pointers: {", ".join(pointers) or "none"}. '
            'Use repository state to establish context. Physical chat history is not project history.')


def project_command_reply(runner, event):
    from gateway.project_choices import pending_choice
    from gateway.session_ingress import executing_admission
    try:
        route_control(runner, event)
    except RuntimeStoreError as exc:
        return f'Project control refused: {exc.reason}.'
    if executing_admission.get() and pending_choice(event.source) is not None:
        return 'Choose one project for this input. This choice expires in 15 minutes.'
    return None


def project_write_guard(authority, payload, authorize=None):
    """Fresh local project authority inside the native admission/claim transaction."""
    envelope = payload.get('native_text_v1', {}) if payload else {}
    if 'project_route' not in envelope:
        return authorize
    def guard(conn):
        if authorize is not None:
            authorize(conn)
        from gateway.session_envelope import restore_native_source
        check_delivery(authority.runner, restore_native_source(payload, authority.runner))
    return guard


async def pending_project_reply(runner, event, internal, physical_key):
    if not internal:
        reply = await runner._hm_pending_reply_intercepts(event, event.source, physical_key)
        if reply is not None:
            return reply
    return project_command_reply(runner, event)


async def recover_project_inputs(authority):
    """Only captured, never-admitted ingress is prepared again; committed work stays native."""
    from gateway.session_envelope import restore_native, _validate_native
    from gateway.session_ingress_context import native_callback, reauthorize_roles
    from gateway.session_authorities import owner_scope
    runner = authority.runner
    from hermes_state_project_routing import captured_inputs
    for payload in captured_inputs(authority.db):
        try:
            event = restore_native(payload, runner)
            event._project_routing_authored = payload.get('project_routing_authored', False)
            provenance = payload['native_text_v1'].get('provenance')
            roles = await reauthorize_roles(runner, event.source, provenance)
            _validate_native(runner, event, provenance, roles)
            home, _, _, config = routing_context(runner, event.source)
            if config.get('enabled') is not True:
                continue
            adapter = runner._delivery_adapter_for(event.source)
            with owner_scope(authority), native_callback(runner, event, home, getattr(adapter, '_owner_profile', None)):
                await route_inbound(runner, authority, event)
                status = getattr(event.source, '_project_status', None)
                if status is None:
                    await authority.admit_native(event)
                else:
                    from gateway.session_ingress import deliver_response
                    await deliver_response(adapter, event, runner._session_key_for_source(event.source), status)
        except RuntimeStoreError:
            # The immutable input remains available for an explicit retry after policy repair.
            import logging
            logging.getLogger(__name__).warning('Captured project input recovery refused', exc_info=True)


def delivery_authorization(runner, source):
    if route_of(source) is None:
        return {}
    return {'authorize_send': lambda: check_delivery(runner, source)}
