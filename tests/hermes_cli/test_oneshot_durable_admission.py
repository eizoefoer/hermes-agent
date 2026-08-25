"""Production-path durability coverage for ``hermes -z``."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from hermes_state import SessionDB


@pytest.fixture
def state(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return SessionDB(db_path=home / "state.db")


def _install_oneshot_runtime(monkeypatch, agent_cls):
    """Keep the production `_run_agent` path deterministic and offline."""
    monkeypatch.setitem(sys.modules, "hermes_cli.config", types.SimpleNamespace(load_config=lambda: {}))
    monkeypatch.setitem(sys.modules, "hermes_cli.models", types.SimpleNamespace(detect_provider_for_model=lambda *_: None))
    monkeypatch.setitem(
        sys.modules,
        "hermes_cli.runtime_provider",
        types.SimpleNamespace(resolve_runtime_provider=lambda **_: {"provider": "test"}),
    )
    monkeypatch.setitem(sys.modules, "hermes_cli.tools_config", types.SimpleNamespace(_get_platform_tools=lambda *_: set()))
    monkeypatch.setitem(sys.modules, "run_agent", types.SimpleNamespace(AIAgent=agent_cls))


def test_oneshot_admits_before_execution_and_completes(state, monkeypatch):
    import hermes_cli.oneshot as oneshot

    calls = []

    class Agent:
        def __init__(self, **kwargs):
            calls.append(kwargs)
            self.suppress_status_output = False
            self.stream_delta_callback = object()
            self.tool_gen_callback = object()

        def run_conversation(self, *, user_message, task_id):
            assert task_id is None
            return {"final_response": "done", "completed": True}

    _install_oneshot_runtime(monkeypatch, Agent)
    monkeypatch.setattr(oneshot, "_create_session_db_for_oneshot", lambda: state)
    monkeypatch.setattr(oneshot, "get_fallback_chain", lambda _: [])
    monkeypatch.setenv("HERMES_ONESHOT_SESSION_ID", "oneshot-test")
    monkeypatch.setenv("HERMES_ONESHOT_SOURCE_ID", "oneshot-source")

    assert oneshot._run_agent("work") == "done"
    turn = state.list_session_logical_turns("oneshot-test")[0]
    assert turn["state"] == "completed"
    assert turn["task_id"] is None and turn["goal_id"] is None
    assert calls[0]["session_id"] == "oneshot-test"


def test_oneshot_owner_contention_and_crash_recovery(state, monkeypatch):
    import hermes_cli.oneshot as oneshot

    class Agent:
        def __init__(self, **kwargs):
            self.suppress_status_output = False
            self.stream_delta_callback = None
            self.tool_gen_callback = None

        def run_conversation(self, *, user_message, task_id):
            return {"final_response": "recovered", "completed": True}

    _install_oneshot_runtime(monkeypatch, Agent)
    monkeypatch.setattr(oneshot, "_create_session_db_for_oneshot", lambda: state)
    monkeypatch.setattr(oneshot, "get_fallback_chain", lambda _: [])
    monkeypatch.setenv("HERMES_ONESHOT_SESSION_ID", "oneshot-recovery")
    monkeypatch.setenv("HERMES_ONESHOT_SOURCE_ID", "oneshot-recovery-source")

    state.create_session("oneshot-recovery", "cli-oneshot")
    admitted = state.admit_session_event(
        session_id="oneshot-recovery", session_key="cli:oneshot-recovery",
        source_identity="oneshot-recovery-source", event_type="cli-oneshot",
    )
    first = state.claim_logical_turn(admitted["logical_turn_id"], owner="dead", pid=1)
    state.mark_logical_turn_started(admitted["logical_turn_id"], first["attempt_id"])
    assert state.claim_logical_turn(admitted["logical_turn_id"], owner="other")["outcome"] == "busy"
    # Model process died after admission. Reconciliation makes the same source
    # identity reclaimable instead of creating a second logical request.
    state.fail_logical_turn(admitted["logical_turn_id"], first["attempt_id"], "crashed", retryable=True)

    assert oneshot._run_agent("work") == "recovered"
    turns = state.list_session_logical_turns("oneshot-recovery")
    assert len(turns) == 1
    assert turns[0]["logical_turn_id"] == admitted["logical_turn_id"]
    assert turns[0]["state"] == "completed"


def test_oneshot_fails_closed_without_sessiondb(monkeypatch):
    import hermes_cli.oneshot as oneshot

    _install_oneshot_runtime(monkeypatch, object)
    monkeypatch.setattr(oneshot, "_create_session_db_for_oneshot", lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    with pytest.raises(RuntimeError, match="no db"):
        oneshot._run_agent("work")
