"""Durable admission regressions for persisted CLI execution producers."""

from __future__ import annotations

from pathlib import Path

import pytest

from cli import (
    HermesCLI,
    _cli_background_source_identity,
    _cli_kanban_turn_source_identity,
    _cli_query_source_identity,
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
