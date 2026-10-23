"""A project button selects an exact admitted input, never a new inferred action."""
import json

import pytest

from gateway.project_choices import choice_identity, digest_input, input_identity, prepare_choice
from hermes_state import SessionDB
from hermes_state_project_routing import bind_choice, choice_payload, choose, create_choice, get_choice
from hermes_state_runtime import RuntimeStoreError, admit_session_input, begin_runtime_epoch
from tests.gateway.test_project_routing import event, register_projects


def test_choice_is_identity_bound_expiring_and_fixed_after_first_use(tmp_path):
    db = SessionDB(tmp_path / 'state.db')
    try:
        db.create_session('physical', source='telegram')
        identity = choice_identity(event().source, 'bot', 'default')
        row = create_choice(db, receipt_id='input-1', digest='digest', identity=identity,
                            options=[{'id': 'p1', 'slug': 'one'}, {'id': 'p2', 'slug': 'two'}], now=100)
        db._execute_write(lambda conn: bind_choice(conn, row['token'], session_id='physical',
                                                  principal_id='principal', request_id='17'))
        with pytest.raises(RuntimeStoreError, match='permission_denied'):
            choose(db, token=row['token'], index=0, identity={**identity, 'principal': 'other'}, now=101)
        with pytest.raises(RuntimeStoreError, match='permission_denied'):
            choose(db, token=row['token'], index=0, identity={**identity, 'chat_type': 'group'}, now=101)
        assert get_choice(db, row['token'])['state'] == 'pending'
        _, selected = choose(db, token=row['token'], index=0, identity=identity, now=101)
        assert selected == 'p1'
        assert choose(db, token=row['token'], index=0, identity=identity, now=102)[1] == 'p1'
        with pytest.raises(RuntimeStoreError, match='choice_used'):
            choose(db, token=row['token'], index=1, identity=identity, now=102)
        with pytest.raises(RuntimeStoreError, match='choice_expired'):
            choose(db, token=row['token'], index=0, identity=identity, now=1000)
    finally:
        db.close()


def test_selection_keeps_input_in_native_admission_and_pin_survives_restart(tmp_path):
    from gateway.project_routing import pin_project_route, admission_guard
    from tests.gateway.test_project_routing import settings
    register_projects(tmp_path, tmp_path)
    db = SessionDB(tmp_path / 'state.db')
    ev = event('trading and cashflow: change this')
    identity = choice_identity(ev.source, 'bot', 'default')
    try:
        epoch = begin_runtime_epoch(db, instance_id='owner')
        db.create_session('physical', source='telegram')
        descriptor = prepare_choice(db, ev, home=tmp_path, bot='bot', profile='default', config=settings())
        payload = {'text': ev.text, 'native_text_v1': {'source': ev.source.to_dict(),
                    'event': {'message_id': ev.message_id}, 'project_choice': descriptor}}
        native_principal = 'messaging:' + json.dumps([None, 'telegram', '42', '20197', '42'], separators=(',', ':'))
        admitted = admit_session_input(db, epoch=epoch, principal_id=native_principal, session_id='physical',
            request_id='17', payload=payload, _authorize_write=admission_guard(payload, 'physical', lambda conn: None))
        row, pid = choose(db, token=descriptor['token'], index=0, identity=identity, now=0)
        assert choice_payload(db, row) == admitted['payload']
        chosen = pin_project_route(db, ev, config=settings(), home=tmp_path, bot='bot', profile='default')
        assert chosen.project_id == pid and chosen.reason == 'selection'
        db.close()
        db = SessionDB(tmp_path / 'state.db')
        assert pin_project_route(db, ev, config=settings(), home=tmp_path, bot='bot', profile='default') == chosen
    finally:
        db.close()
