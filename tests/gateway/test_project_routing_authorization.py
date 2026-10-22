"""Explicit project eligibility is independent of registry membership and aliases."""
from dataclasses import asdict

import pytest

from gateway.project_routing import pin_project_route, validate_route, ProjectChoiceRequired
from hermes_state import SessionDB
from hermes_state_runtime import RuntimeStoreError
from tests.gateway.test_project_routing import event, register_projects, settings


def test_registry_is_not_authorization_and_retry_rechecks_eligibility(tmp_path):
    register_projects(tmp_path, tmp_path)
    db = SessionDB(tmp_path / 'state.db')
    kw = dict(home=tmp_path, bot='bot', profile='default')
    try:
        with pytest.raises(ProjectChoiceRequired):
            pin_project_route(db, event(), config={'enabled': True}, **kw)
        config = {**settings(), 'eligible_projects': ['heisentick']}
        route = pin_project_route(db, event(), config=config, **kw)
        with pytest.raises(ProjectChoiceRequired):
            pin_project_route(db, event('cashflow work', mid='18'), config=config, **kw)
        with pytest.raises(RuntimeStoreError, match='permission_denied'):
            validate_route(db, event().source, asdict(route), config={'enabled': True}, **kw)
        with pytest.raises(RuntimeStoreError, match='permission_denied'):
            pin_project_route(db, event(), config={'enabled': True}, **kw)
    finally:
        db.close()


def test_config_loader_reaches_adapter_extra(tmp_path, monkeypatch):
    import json
    from gateway.config import load_gateway_config, Platform
    from hermes_constants import get_hermes_home
    home = get_hermes_home()
    (home / 'config.yaml').write_text(json.dumps({'platforms': {'telegram': {'enabled': True, 'token': 'fixture'}}, 'telegram': {'project_routing': {
        'enabled': True, 'eligible_projects': ['property']}}}))
    config = load_gateway_config()
    assert config.platforms[Platform.TELEGRAM].extra['project_routing']['eligible_projects'] == ['property']


def test_ineligible_authored_project_never_falls_back_to_a_different_topic(tmp_path):
    register_projects(tmp_path, tmp_path)
    db = SessionDB(tmp_path / 'state.db')
    try:
        ev = event('trading inspect', mid='20')
        ev.source.chat_topic = 'cashflow'
        with pytest.raises(ProjectChoiceRequired):
            pin_project_route(db, ev, config={**settings(), 'eligible_projects': ['property']},
                              home=tmp_path, bot='bot', profile='default')
    finally:
        db.close()


def test_binding_and_admission_rollback_together_after_writer_fault(tmp_path):
    from gateway.project_routing import admission_guard
    from hermes_state_runtime import admit_session_input, begin_runtime_epoch
    from hermes_state_project_routing import receipt
    register_projects(tmp_path, tmp_path)
    db = SessionDB(tmp_path / 'state.db')
    try:
        route = pin_project_route(db, event(), config=settings(), home=tmp_path, bot='bot', profile='default')
        db.create_session('isolated', source='telegram', cwd=route.cwd)
        epoch = begin_runtime_epoch(db, instance_id='fault-test')
        payload = {'text': 'trading inspect', 'native_text_v1': {'project_route': asdict(route)}}
        bind = admission_guard(payload, 'isolated', lambda conn: None)
        def fail_after_bind(conn):
            bind(conn)
            raise OSError('isolated writer fault after receipt bind')
        with pytest.raises(OSError, match='isolated writer fault'):
            admit_session_input(db, epoch=epoch, principal_id='principal', session_id='isolated',
                                request_id='17', payload=payload, _authorize_write=fail_after_bind)
        assert receipt(db, route.receipt_id)['session_id'] is None
        assert not db._read_all('SELECT admission_id FROM session_admissions')
    finally:
        db.close()
