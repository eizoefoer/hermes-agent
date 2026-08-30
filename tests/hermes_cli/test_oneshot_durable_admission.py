"""Production-path durability regressions for ``hermes -z``."""

from __future__ import annotations

import sys
import types

import pytest

from hermes_state import SessionDB


def _module(name, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


def _install_runtime(monkeypatch, agent_cls):
    monkeypatch.setitem(sys.modules, "run_agent", _module("run_agent", AIAgent=agent_cls))
    monkeypatch.setitem(
        sys.modules,
        "hermes_cli.config",
        _module("hermes_cli.config", load_config=lambda: {"model": {"default": "m"}}),
    )
    monkeypatch.setitem(
        sys.modules,
        "hermes_cli.models",
        _module("hermes_cli.models", detect_provider_for_model=lambda *_a, **_k: None),
    )
    monkeypatch.setitem(
        sys.modules,
        "hermes_cli.runtime_provider",
        _module(
            "hermes_cli.runtime_provider",
            resolve_runtime_provider=lambda **_k: {
                "api_key": "k",
                "base_url": "u",
                "provider": "p",
                "api_mode": "chat_completions",
                "credential_pool": None,
            },
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "hermes_cli.tools_config",
        _module("hermes_cli.tools_config", _get_platform_tools=lambda *_a, **_k: set()),
    )
    monkeypatch.setattr(
        "hermes_cli.mcp_startup.ensure_mcp_discovery_before_agent_build",
        lambda **_kwargs: None,
    )


class _Agent:
    calls = 0

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.suppress_status_output = False
        self.stream_delta_callback = None
        self.tool_gen_callback = None
        self._session_messages = []

    def run_conversation(self, prompt, task_id=None):
        type(self).calls += 1
        assert task_id is None
        return {"final_response": f"done:{prompt}", "messages": []}

    def shutdown_memory_provider(self, *_args):
        return None

    def close(self):
        return None


def test_oneshot_uses_real_logical_turn_and_truthful_correlation(monkeypatch, tmp_path):
    from hermes_cli import oneshot
    from hermes_cli.oneshot import _run_agent

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_ONESHOT_SESSION_ID", "oneshot-session")
    monkeypatch.setenv("HERMES_ONESHOT_SOURCE_ID", "oneshot-request-1")
    _Agent.calls = 0
    _install_runtime(monkeypatch, _Agent)
    monkeypatch.setattr(
        oneshot,
        "_create_session_db_for_oneshot",
        lambda: SessionDB(tmp_path / "state.db"),
    )

    response, _ = _run_agent("hello")

    db = SessionDB(tmp_path / "state.db")
    turns = db.list_session_logical_turns("oneshot-session")
    assert response == "done:hello"
    assert _Agent.calls == 1
    assert len(turns) == 1
    assert turns[0]["state"] == "completed"
    assert turns[0]["task_id"] is None


def test_oneshot_authoritative_replay_does_not_execute_twice(monkeypatch, tmp_path):
    from hermes_cli import oneshot
    from hermes_cli.oneshot import _run_agent

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_ONESHOT_SESSION_ID", "oneshot-replay")
    monkeypatch.setenv("HERMES_ONESHOT_SOURCE_ID", "accepted-request")
    _Agent.calls = 0
    _install_runtime(monkeypatch, _Agent)
    monkeypatch.setattr(
        oneshot,
        "_create_session_db_for_oneshot",
        lambda: SessionDB(tmp_path / "state.db"),
    )

    first = _run_agent("same")
    second = _run_agent("same")

    assert first[0] == second[0] == "done:same"
    assert _Agent.calls == 1
    assert SessionDB(tmp_path / "state.db").count_logical_turns("oneshot-replay") == 1


def test_oneshot_valid_owner_prevents_competing_execution(monkeypatch, tmp_path):
    from hermes_cli import oneshot
    from hermes_cli.oneshot import _run_agent

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_ONESHOT_SESSION_ID", "oneshot-busy")
    monkeypatch.setenv("HERMES_ONESHOT_SOURCE_ID", "second-request")
    _install_runtime(monkeypatch, _Agent)
    monkeypatch.setattr(
        oneshot,
        "_create_session_db_for_oneshot",
        lambda: SessionDB(tmp_path / "state.db"),
    )
    db = SessionDB(tmp_path / "state.db")
    db.create_session("oneshot-busy", "cli-oneshot")
    owner = db.admit_session_event(
        session_id="oneshot-busy",
        session_key="cli:oneshot-busy",
        source_identity="first-request",
        event_type="cli-oneshot",
    )
    assert db.claim_logical_turn(owner["logical_turn_id"], owner="other")["outcome"] == "claimed"
    db.close()

    with pytest.raises(RuntimeError, match="durably queued"):
        _run_agent("wait")

    db = SessionDB(tmp_path / "state.db")
    assert [turn["state"] for turn in db.list_session_logical_turns("oneshot-busy")] == [
        "claimed",
        "queued",
    ]
