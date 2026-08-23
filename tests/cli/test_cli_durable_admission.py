"""Durable admission regressions for persisted CLI execution producers."""

from __future__ import annotations

from pathlib import Path

import pytest
import cli as cli_module

from cli import (
    DurableAdmissionUnavailable,
    HermesCLI,
    _cli_background_source_identity,
    _cli_fresh_source_identity,
    _cli_kanban_turn_source_identity,
    _cli_query_source_identity,
    _next_kanban_iteration,
    recover_cli_background_children,
)
from hermes_state import SessionDB


@pytest.fixture
def state(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return SessionDB(db_path=home / "state.db")


def _cli(state: SessionDB, session_id: str) -> HermesCLI:
    cli = HermesCLI.__new__(HermesCLI)
    cli._session_db = state
    cli.session_id = session_id
    return cli


def test_quiet_query_identity_is_stable_and_has_no_fake_task_id(state):
    state.create_session("resumed", "cli")
    cli = _cli(state, "resumed")
    identity = _cli_query_source_identity("resumed", 0, "continue")

    first = cli._admit_cli_logical_turn(
        "continue", event_type="cli-query", source_identity=identity
    )
    replay = cli._admit_cli_logical_turn(
        "continue", event_type="cli-query", source_identity=identity
    )

    assert first["outcome"] == "claimed"
    assert replay["outcome"] == "busy"
    turns = state.list_session_logical_turns("resumed")
    assert len(turns) == 1
    assert turns[0]["source_identity"] == identity
    assert turns[0]["task_id"] is None


def test_quiet_query_contention_and_recovery_reuse_the_same_logical_turn(state):
    state.create_session("query-session", "cli")
    identity = _cli_query_source_identity("query-session", 0, "do work")
    first_cli = _cli(state, "query-session")
    first = first_cli._admit_cli_logical_turn(
        "do work", event_type="cli-query", source_identity=identity
    )
    assert first["outcome"] == "claimed"

    second_cli = _cli(state, "query-session")
    assert second_cli._admit_cli_logical_turn(
        "do work", event_type="cli-query", source_identity=identity
    )["outcome"] == "busy"

    state.fail_logical_turn(first["logical_turn_id"], first["attempt_id"], "interrupted", retryable=True)
    recovered = second_cli._admit_cli_logical_turn(
        "do work", event_type="cli-query", source_identity=identity
    )
    assert recovered["outcome"] == "claimed"
    assert recovered["logical_turn_id"] == first["logical_turn_id"]
    assert state.count_logical_turns("query-session") == 1


def test_quiet_source_event_is_fresh_but_explicit_crash_recovery_reuses_it(state, monkeypatch):
    state.create_session("quiet", "cli")
    cli = _cli(state, "quiet")
    monkeypatch.setenv("HERMES_CLI_SOURCE_ID", "quiet-event-1")
    identity = _cli_fresh_source_identity("quiet", "query")
    first = cli._admit_cli_logical_turn("same query", event_type="cli-query", source_identity=identity)
    state.fail_logical_turn(first["logical_turn_id"], first["attempt_id"], "crashed", retryable=True)
    recovered = cli._admit_cli_logical_turn("same query", event_type="cli-query", source_identity=identity)
    assert recovered["logical_turn_id"] == first["logical_turn_id"]
    cli._finish_cli_logical_turn(recovered, {"final_response": "done"})
    monkeypatch.delenv("HERMES_CLI_SOURCE_ID")
    assert _cli_fresh_source_identity("quiet", "query") != _cli_fresh_source_identity("quiet", "query")


def test_background_recovery_executor_repairs_parent_then_runs_same_child(state):
    state.create_session("parent-recover", "cli")
    parent = state.admit_session_event(
        session_id="parent-recover", session_key="cli:parent-recover",
        source_identity="background-command-event", event_type="cli-background-command",
        payload={"text": "recover child", "background_task_id": "bg-recover", "child_session_id": "child-recover"},
    )
    parent_claim = state.claim_logical_turn(parent["logical_turn_id"], owner="dead")
    state.mark_logical_turn_started(parent["logical_turn_id"], parent_claim["attempt_id"])
    state.fail_logical_turn(parent["logical_turn_id"], parent_claim["attempt_id"], "crashed", retryable=True)

    executed = []
    recovered = recover_cli_background_children(
        state, lambda turn: executed.append(turn["logical_turn_id"]) or {"final_response": "ok"}, limit=4
    )
    child_turns = state.list_session_logical_turns("child-recover")
    assert len(child_turns) == 1
    assert child_turns[0]["logical_turn_id"] in recovered
    assert executed == [child_turns[0]["logical_turn_id"]]
    assert child_turns[0]["state"] == "completed"
    assert state.get_logical_turn(parent["logical_turn_id"])["state"] == "completed"


def test_background_handler_persists_child_before_parent_success(state, monkeypatch):
    state.create_session("background-parent", "cli")
    cli = _cli(state, "background-parent")
    cli._background_task_counter = 0
    cli._background_tasks = {}
    cli._ensure_runtime_credentials = lambda: True
    cli._resolve_turn_agent_config = lambda _: {"model": "test", "runtime": {}, "request_overrides": None}
    cli.max_turns = 1
    cli.enabled_toolsets = []
    cli.reasoning_config = {}
    cli.service_tier = None
    cli._providers_only = cli._providers_ignore = cli._providers_order = cli._provider_sort = None
    cli._provider_require_params = cli._provider_data_collection = cli._openrouter_min_coding_score = None
    cli._fallback_model = None
    cli._agent_running = False
    cli._spinner_text = ""
    cli._app = None
    cli.bell_on_complete = False
    cli.final_response_markdown = "strip"
    cli._sudo_password_callback = lambda: ""
    cli._approval_callback = lambda *_: "deny"
    cli._secret_capture_callback = lambda *_: ""
    cli._scrollback_box_width = lambda: None

    class Agent:
        def __init__(self, **kwargs):
            self._print_fn = None
            self.thinking_callback = None

        def run_conversation(self, **kwargs):
            return {"final_response": "done"}

    monkeypatch.setattr(cli_module, "AIAgent", Agent)
    monkeypatch.setattr(cli_module, "_cprint", lambda *_: None)
    cli._handle_background_command("/background durable child")
    for thread in list(cli._background_tasks.values()):
        thread.join(timeout=2)

    parent_turns = state.list_session_logical_turns("background-parent")
    assert len(parent_turns) == 1 and parent_turns[0]["state"] == "completed"
    task_id = parent_turns[0]["payload"]["background_task_id"]
    child_turns = state.list_session_logical_turns(task_id)
    assert len(child_turns) == 1
    assert child_turns[0]["state"] == "completed"
    assert child_turns[0]["payload"]["parent_session_id"] == "background-parent"


def test_interrupted_cli_attempt_is_retryable_for_recovery(state):
    state.create_session("interrupted", "cli")
    cli = _cli(state, "interrupted")
    identity = _cli_query_source_identity("interrupted", 0, "recover me")
    claim = cli._admit_cli_logical_turn(
        "recover me", event_type="cli-query", source_identity=identity
    )

    cli._finish_cli_logical_turn(claim, {"interrupted": True})

    assert state.get_logical_turn(claim["logical_turn_id"])["state"] == "queued"
    recovered = cli._admit_cli_logical_turn(
        "recover me", event_type="cli-query", source_identity=identity
    )
    assert recovered["outcome"] == "claimed"
    assert recovered["logical_turn_id"] == claim["logical_turn_id"]


def test_kanban_iterations_are_distinct_but_iteration_replay_is_idempotent(state):
    state.create_session("worker-session", "cli")
    cli = _cli(state, "worker-session")
    first_id = _cli_kanban_turn_source_identity("task-7", "goal-9", 1)
    second_id = _cli_kanban_turn_source_identity("task-7", "goal-9", 2)

    first = cli._admit_cli_logical_turn(
        "same continuation", event_type="kanban-goal-turn", source_identity=first_id,
        task_id="task-7", goal_id="goal-9", payload={"iteration": 1},
    )
    cli._finish_cli_logical_turn(first, {"final_response": "one"})
    second = cli._admit_cli_logical_turn(
        "same continuation", event_type="kanban-goal-turn", source_identity=second_id,
        task_id="task-7", goal_id="goal-9", payload={"iteration": 2},
    )
    replay = cli._admit_cli_logical_turn(
        "same continuation", event_type="kanban-goal-turn", source_identity=second_id,
        task_id="task-7", goal_id="goal-9", payload={"iteration": 2},
    )

    assert second["outcome"] == "claimed"
    assert replay["outcome"] == "busy"
    turns = state.list_session_logical_turns("worker-session")
    assert [turn["source_identity"] for turn in turns] == [first_id, second_id]
    assert [(turn["task_id"], turn["goal_id"], turn["payload"]["iteration"]) for turn in turns] == [
        ("task-7", "goal-9", 1), ("task-7", "goal-9", 2),
    ]


def test_kanban_cursor_reconstructs_restart_and_replays_unfinished_iteration(state):
    state.create_session("worker-restart", "cli")
    cli = _cli(state, "worker-restart")
    for iteration in (1, 2):
        claim = cli._admit_cli_logical_turn(
            "identical continuation", event_type="kanban-goal-turn",
            source_identity=_cli_kanban_turn_source_identity("task-r", None, iteration),
            task_id="task-r", goal_id=None, payload={"iteration": iteration},
        )
        cli._finish_cli_logical_turn(claim, {"final_response": f"turn {iteration}"})
    assert _next_kanban_iteration(state, "worker-restart", "task-r") == 3

    third = cli._admit_cli_logical_turn(
        "identical continuation", event_type="kanban-goal-turn",
        source_identity=_cli_kanban_turn_source_identity("task-r", None, 3),
        task_id="task-r", goal_id=None, payload={"iteration": 3},
    )
    assert _next_kanban_iteration(state, "worker-restart", "task-r") == 3
    state.fail_logical_turn(third["logical_turn_id"], third["attempt_id"], "crashed", retryable=True)
    replay = cli._admit_cli_logical_turn(
        "identical continuation", event_type="kanban-goal-turn",
        source_identity=_cli_kanban_turn_source_identity("task-r", None, 3),
        task_id="task-r", goal_id=None, payload={"iteration": 3},
    )
    assert replay["logical_turn_id"] == third["logical_turn_id"]
    assert replay["turn"]["goal_id"] is None


def test_completed_kanban_iteration_two_replay_resolves_iteration_two_not_four(state):
    state.create_session("worker-completed-replay", "cli")
    cli = _cli(state, "worker-completed-replay")
    for iteration in (1, 2):
        claim = cli._admit_cli_logical_turn(
            "identical continuation", event_type="kanban-goal-turn",
            source_identity=_cli_kanban_turn_source_identity("task-complete", None, iteration),
            task_id="task-complete", payload={"iteration": iteration},
        )
        cli._finish_cli_logical_turn(claim, {"final_response": f"turn {iteration}"})

    replay = cli._admit_cli_logical_turn(
        "identical continuation", event_type="kanban-goal-turn",
        source_identity=_cli_kanban_turn_source_identity("task-complete", None, 2),
        task_id="task-complete", payload={"iteration": 2},
    )
    assert replay["outcome"] == "terminal"
    assert replay["turn"]["payload"]["iteration"] == 2
    assert _next_kanban_iteration(state, "worker-completed-replay", "task-complete") == 3


def test_cli_refuses_unmanaged_turns():
    cli = HermesCLI.__new__(HermesCLI)
    cli._session_db = None
    cli.session_id = "missing-db"
    with pytest.raises(DurableAdmissionUnavailable):
        cli._admit_cli_logical_turn("must not run")


def test_background_jobs_have_independent_child_sessions_and_parent_correlation(state):
    state.create_session("parent", "cli")
    state.create_session("child-a", "cli-background")
    state.create_session("child-b", "cli-background")
    parent = _cli(state, "parent")
    child_a = _cli(state, "child-a")
    child_b = _cli(state, "child-b")
    a_identity = _cli_background_source_identity("parent", "child-a", "job-a")
    b_identity = _cli_background_source_identity("parent", "child-b", "job-b")

    a = child_a._admit_cli_logical_turn(
        "same prompt", event_type="cli-background", source_identity=a_identity,
        task_id="job-a", payload={"parent_session_id": "parent", "child_session_id": "child-a"},
    )
    duplicate_a = child_a._admit_cli_logical_turn(
        "same prompt", event_type="cli-background", source_identity=a_identity,
        task_id="job-a", payload={"parent_session_id": "parent", "child_session_id": "child-a"},
    )
    b = child_b._admit_cli_logical_turn(
        "same prompt", event_type="cli-background", source_identity=b_identity,
        task_id="job-b", payload={"parent_session_id": "parent", "child_session_id": "child-b"},
    )

    assert a["outcome"] == "claimed"
    assert duplicate_a["outcome"] == "busy"
    assert b["outcome"] == "claimed"
    assert state.count_logical_turns("parent") == 0
    assert state.list_session_logical_turns("child-a")[0]["payload"]["parent_session_id"] == "parent"
    assert state.list_session_logical_turns("child-b")[0]["task_id"] == "job-b"
