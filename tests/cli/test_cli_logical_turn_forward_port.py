"""Phase 1 durable-admission regressions for current-line CLI producers."""

from cli import (
    HermesCLI,
    _cli_kanban_turn_source_identity,
    _cli_request_digest,
    _resolve_cli_query_source_identity,
    _resolve_kanban_iteration,
)
from hermes_state import SessionDB
from tests.cli.test_cli_approval_ui import _make_background_cli_stub


def _cli(db: SessionDB, session_id: str) -> HermesCLI:
    if not db.get_session(session_id):
        db.create_session(session_id, "cli")
    instance = HermesCLI.__new__(HermesCLI)
    instance._session_db = db
    instance.session_id = session_id
    return instance


def test_quiet_recovery_uses_unfinished_acceptance_not_transcript(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    cli = _cli(db, "quiet-session")
    prompt = "identical query"
    source = _resolve_cli_query_source_identity(db, cli.session_id, prompt)
    first = cli._admit_cli_logical_turn(
        prompt,
        event_type="cli-query",
        source_identity=source,
        payload={"request_digest": _cli_request_digest(prompt)},
    )
    assert first["outcome"] == "claimed"

    # Simulate transcript/model mutation followed by process interruption.
    db.update_session_meta(cli.session_id, '{"transcript_mutated":true}')
    db.fail_logical_turn(
        first["logical_turn_id"], first["attempt_id"], "process lost", retryable=True
    )
    recovered_source = _resolve_cli_query_source_identity(db, cli.session_id, prompt)
    recovered = cli._admit_cli_logical_turn(
        prompt,
        event_type="cli-query",
        source_identity=recovered_source,
        payload={"request_digest": _cli_request_digest(prompt)},
    )
    assert recovered["logical_turn_id"] == first["logical_turn_id"]
    cli._finish_cli_logical_turn(recovered, {"final_response": "done"})

    later_source = _resolve_cli_query_source_identity(db, cli.session_id, prompt)
    later = cli._admit_cli_logical_turn(
        prompt,
        event_type="cli-query",
        source_identity=later_source,
        payload={"request_digest": _cli_request_digest(prompt)},
    )
    assert later["logical_turn_id"] != first["logical_turn_id"]
    cli._finish_cli_logical_turn(later, {"final_response": "done again"})


def test_valid_durable_owner_queues_second_cli_occurrence(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    cli = _cli(db, "contended")
    first = cli._admit_cli_logical_turn("one", source_identity="cli-event-one")
    second = cli._admit_cli_logical_turn("two", source_identity="cli-event-two")
    assert first["outcome"] == "claimed"
    assert second["outcome"] == "busy"
    turns = db.list_session_logical_turns("contended")
    assert [turn["state"] for turn in turns] == ["executing", "queued"]
    cli._finish_cli_logical_turn(first, {"final_response": "one"})


def test_kanban_cursor_is_durable_and_exact_replay_is_idempotent(tmp_path, monkeypatch):
    db = SessionDB(tmp_path / "state.db")
    cli = _cli(db, "kanban-session")
    task_id = "task-real"
    ids = []
    for iteration in (1, 2, 3):
        source = _cli_kanban_turn_source_identity(task_id, iteration)
        claim = cli._admit_cli_logical_turn(
            "same continuation",
            event_type="kanban-goal-turn",
            source_identity=source,
            task_id=task_id,
            goal_id=None,
            payload={"iteration": iteration, "evaluation_identity": iteration},
        )
        ids.append(claim["logical_turn_id"])
        cli._finish_cli_logical_turn(claim, {"final_response": str(iteration)})
    assert len(set(ids)) == 3
    assert _resolve_kanban_iteration(db, cli.session_id, task_id) == 4

    iteration_two = _cli_kanban_turn_source_identity(task_id, 2)
    monkeypatch.setenv("HERMES_CLI_SOURCE_ID", iteration_two)
    assert _resolve_kanban_iteration(db, cli.session_id, task_id) == 2
    replay = cli._admit_cli_logical_turn(
        "same continuation",
        event_type="kanban-goal-turn",
        source_identity=iteration_two,
        task_id=task_id,
        goal_id=None,
        payload={"iteration": 2, "evaluation_identity": 2},
    )
    assert replay["logical_turn_id"] == ids[1]
    assert replay["outcome"] == "terminal"
    assert len(db.list_session_logical_turns(cli.session_id)) == 3


def test_ordinary_cli_execution_does_not_fabricate_task_or_goal(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    cli = _cli(db, "ordinary")
    claim = cli._admit_cli_logical_turn("hello", source_identity="ordinary-1")
    turn = db.get_logical_turn(claim["logical_turn_id"])
    assert turn["task_id"] is None
    assert turn["goal_id"] is None
    cli._finish_cli_logical_turn(claim, {"final_response": "hi"})


def test_background_child_is_recovered_by_supported_startup_executor(tmp_path, monkeypatch):
    db = SessionDB(tmp_path / "state.db")
    cli = _cli(db, "parent")
    cli._background_tasks = {}
    cli.max_turns = 3
    cli.enabled_toolsets = []
    cli.reasoning_config = {}
    cli.service_tier = None
    cli._resolve_turn_agent_config = lambda _prompt: {
        "model": "test-model",
        "runtime": {},
        "request_overrides": None,
    }
    db.create_session("child", "cli-background")
    child = db.admit_session_event(
        session_id="child",
        session_key="cli:child",
        source_identity="child-event-1",
        event_type="cli-background-child",
        payload={"text": "do work", "background_task_id": "task-1"},
        task_id="task-1",
    )

    class FakeAgent:
        def __init__(self, **_kwargs):
            pass

        def run_conversation(self, *, user_message, task_id):
            assert user_message == "do work"
            assert task_id == "task-1"
            return {"final_response": "done"}

    monkeypatch.setattr("cli.AIAgent", FakeAgent)
    recovered = cli._recover_cli_background_children(limit=4)
    assert recovered == [child["logical_turn_id"]]
    for thread in list(cli._background_tasks.values()):
        thread.join(timeout=5)
    turn = db.get_logical_turn(child["logical_turn_id"])
    assert turn["state"] == "completed"
    assert turn["result"]["response"] == "done"


def test_identical_background_commands_are_distinct_but_exact_replay_is_stable(
    tmp_path, monkeypatch
):
    cli = _make_background_cli_stub(tmp_path)

    class FakeAgent:
        def __init__(self, **_kwargs):
            self._print_fn = None
            self.thinking_callback = None

        def run_conversation(self, **_kwargs):
            return {"final_response": "done"}

    monkeypatch.setattr("cli.AIAgent", FakeAgent)
    monkeypatch.setattr("cli._cprint", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("cli.ChatConsole", lambda: SimpleConsole())
    cli._handle_background_command("/background same")
    for thread in list(cli._background_tasks.values()):
        thread.join(timeout=5)
    cli._handle_background_command("/background same")
    for thread in list(cli._background_tasks.values()):
        thread.join(timeout=5)

    parents = [
        turn for turn in cli._session_db.list_session_logical_turns(cli.session_id)
        if (turn.get("payload") or {}).get("event_type") == "cli-background-command"
    ]
    assert len(parents) == 2
    child_ids = {(turn.get("payload") or {}).get("child_session_id") for turn in parents}
    assert len(child_ids) == 2

    replay_source = parents[0]["source_identity"]
    monkeypatch.setenv("HERMES_CLI_SOURCE_ID", replay_source)
    cli._handle_background_command("/background same")
    replayed = [
        turn for turn in cli._session_db.list_session_logical_turns(cli.session_id)
        if (turn.get("payload") or {}).get("event_type") == "cli-background-command"
    ]
    assert len(replayed) == 2


class SimpleConsole:
    def print(self, *_args, **_kwargs):
        return None
