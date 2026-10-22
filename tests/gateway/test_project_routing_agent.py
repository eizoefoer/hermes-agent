"""The production agent resolver preserves a warm prompt only in the same workspace."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

from gateway.run_turn_runner import TurnRunner
from gateway.turn_context import TurnContext
from tests.gateway.test_project_routing import event, settings, register_projects


def test_project_agent_init_and_cache_preserve_workspace_identity(tmp_path, monkeypatch):
    import threading
    from gateway.project_routing import pin_project_route
    from gateway.run import GatewayRunner
    from hermes_state import SessionDB
    register_projects(tmp_path, tmp_path)
    db = SessionDB(tmp_path / 'state.db')
    ev = event()
    route = pin_project_route(db, ev, config=settings(), home=tmp_path, bot='bot', profile='default')
    runner = GatewayRunner.__new__(GatewayRunner)
    runner._agent_cache, runner._agent_cache_lock = {}, threading.Lock()
    runner._session_db = None
    runner._prefill_messages = []
    runner._service_tier = None
    runner._refresh_fallback_model = lambda: None
    runner._enforce_agent_cache_cap = lambda: None
    runner._init_cached_agent_for_turn = lambda *a: None
    runner._apply_fallback_chain_to_agent = lambda *a: None
    runner._extract_cache_busting_config = lambda config: {}
    made = []
    def build(**kwargs):
        agent = SimpleNamespace(session_cwd=kwargs['cwd'], session_id='session', frozen_prompt=object())
        made.append(agent)
        return agent
    ctx = TurnContext(source=ev.source, session_id='session', session_key=route.execution_key,
                      user_config={}, AIAgent=build, enabled_toolsets=[], disabled_toolsets=[])
    turn = TurnRunner(runner, ctx)
    monkeypatch.setattr(turn, '_skip_context_files', lambda key: False)
    runtime = {'model': 'fixture', 'runtime': {}}
    agent, reused = turn._resolve_turn_agent(runtime, 'telegram', '', 5, {}, {})
    assert not reused and agent.session_cwd == route.cwd
    again, reused = turn._resolve_turn_agent(runtime, 'telegram', '', 5, {}, {})
    assert reused and again is agent and again.frozen_prompt is agent.frozen_prompt
    # Workspace changes cannot share frozen context even if a caller retained the lane key.
    other = tmp_path / 'other'
    other.mkdir()
    ev.source._project_route = replace(route, cwd=str(other))
    rebuilt, reused = turn._resolve_turn_agent(runtime, 'telegram', '', 5, {}, {})
    assert not reused and rebuilt is not agent and rebuilt.session_cwd == str(other)
    assert len(made) == 2
    db.close()
