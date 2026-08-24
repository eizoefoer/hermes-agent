"""Durable admission regressions for persisted CLI execution producers."""

from __future__ import annotations

import multiprocessing
from pathlib import Path

import pytest
import cli as cli_module

from cli import (
    DurableAdmissionUnavailable,
    DurableRecoveryAmbiguous,
    HermesCLI,
    _cli_background_source_identity,
    _cli_fresh_source_identity,
    _cli_kanban_turn_source_identity,
    _cli_query_source_identity,
    _cli_request_digest,
    _next_kanban_iteration,
    _resolve_cli_query_source_identity,
    _resolve_kanban_iteration,
    recover_cli_background_children,
)
from hermes_state import SessionDB


def _replacement_admit_then_interrupt(db_path: str, result_queue) -> None:
    """A separate interpreter process accepts a quiet event then exits."""
    state = SessionDB(db_path=Path(db_path))
    state.create_session("quiet-process", "cli")
    cli = HermesCLI.__new__(HermesCLI)
    cli._session_db = state
    cli.session_id = "quiet-process"
    prompt = "same quiet query"
    source_identity = _resolve_cli_query_source_identity(state, "quiet-process", prompt)
    claim = cli._admit_cli_logical_turn(
        prompt, event_type="cli-query", source_identity=source_identity,
        payload={"request_digest": _cli_request_digest(prompt)},
    )
    state.append_message("quiet-process", "user", "transcript changed before crash")
    result_queue.put(claim["logical_turn_id"])


def _replacement_reclaim(db_path: str, result_queue) -> None:
    """A fresh interpreter must reclaim the persisted quiet event, not mint one."""
    state = SessionDB(db_path=Path(db_path))
    cli = HermesCLI.__new__(HermesCLI)
    cli._session_db = state
    cli.session_id = "quiet-process"
    prompt = "same quiet query"
    source_identity = _resolve_cli_query_source_identity(state, "quiet-process", prompt)
    claim = cli._admit_cli_logical_turn(
        prompt, event_type="cli-query", source_identity=source_identity,
        payload={"request_digest": _cli_request_digest(prompt)},
    )
    cli._finish_cli_logical_turn(claim, {"final_response": "recovered"})
    result_queue.put((claim["logical_turn_id"], claim["outcome"]))


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


def test_quiet_process_replacement_recovers_without_source_override(state, monkeypatch):
    """A dead same-host PID waits for lease expiry before replacement."""
    monkeypatch.delenv("HERMES_CLI_SOURCE_ID", raising=False)
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    first = context.Process(
        target=_replacement_admit_then_interrupt,
        args=(str(state.db_path), queue),
    )
    first.start()
    first.join(timeout=10)
    assert first.exitcode == 0
    turn_id = queue.get(timeout=2)

    # A local PID probe cannot prove ownership: preserve the attempt and its
    # unexpired lease even though the process that created it has exited.
    first_turn = state.get_logical_turn(turn_id)
    first_lease = state.get_session_turn_lease("quiet-process")
    source_identity = _resolve_cli_query_source_identity(
        state, "quiet-process", "same quiet query"
    )
    busy = _cli(state, "quiet-process")._admit_cli_logical_turn(
        "same quiet query",
        event_type="cli-query",
        source_identity=source_identity,
        payload={"request_digest": _cli_request_digest("same quiet query")},
    )
    assert busy["outcome"] == "busy"
    assert state.get_logical_turn(turn_id)["current_attempt_id"] == first_turn["current_attempt_id"]
    assert state.get_session_turn_lease("quiet-process") == first_lease

    # Canonical recovery begins only after the durable lease expires.
    assert state.renew_session_turn_lease(
        "quiet-process",
        first_turn["lease_holder"],
        first_turn["current_attempt_id"],
        ttl_seconds=-1,
    )
    replacement = context.Process(
        target=_replacement_reclaim,
        args=(str(state.db_path), queue),
    )
    replacement.start()
    replacement.join(timeout=10)
    assert replacement.exitcode == 0
    assert queue.get(timeout=2) == (turn_id, "claimed")

    replacement_state = SessionDB(db_path=state.db_path)
    assert replacement_state.get_logical_turn(turn_id)["state"] == "completed"
    prompt = "same quiet query"
    fresh_identity = _resolve_cli_query_source_identity(
        replacement_state, "quiet-process", prompt
    )
    assert fresh_identity != replacement_state.get_logical_turn(turn_id)["source_identity"]
    fresh_cli = _cli(replacement_state, "quiet-process")
    second = fresh_cli._admit_cli_logical_turn(
        prompt, event_type="cli-query", source_identity=fresh_identity,
        payload={"request_digest": _cli_request_digest(prompt)},
    )
    assert second["logical_turn_id"] != turn_id


def test_quiet_recovery_refuses_ambiguous_unfinished_matches(state, monkeypatch):
    monkeypatch.delenv("HERMES_CLI_SOURCE_ID", raising=False)
    state.create_session("quiet-ambiguous", "cli")
    prompt = "same unfinished request"
    digest = _cli_request_digest(prompt)
    for source in ("quiet-event-a", "quiet-event-b"):
        state.admit_session_event(
            session_id="quiet-ambiguous", session_key="cli:quiet-ambiguous",
            source_identity=source, event_type="cli-query",
            payload={"text": prompt, "request_digest": digest},
        )
    with pytest.raises(DurableRecoveryAmbiguous):
        _resolve_cli_query_source_identity(state, "quiet-ambiguous", prompt)


def test_default_cli_admission_does_not_dedupe_identical_prompt_text(state):
    state.create_session("same-text", "cli")
    cli = _cli(state, "same-text")
    first = cli._admit_cli_logical_turn("identical")
    cli._finish_cli_logical_turn(first, {"final_response": "one"})
    second = cli._admit_cli_logical_turn("identical")
    assert second["logical_turn_id"] != first["logical_turn_id"]


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


def test_background_recovery_after_child_admission_reuses_the_original_child(state):
    state.create_session("parent-after-child", "cli")
    parent = state.admit_session_event(
        session_id="parent-after-child", session_key="cli:parent-after-child",
        source_identity="background-parent-event", event_type="cli-background-command",
        payload={"text": "recover admitted child", "background_task_id": "bg-after", "child_session_id": "child-after"},
    )
    parent_claim = state.claim_logical_turn(parent["logical_turn_id"], owner="dead")
    state.create_session("child-after", "cli-background")
    child = state.admit_session_event(
        session_id="child-after", session_key="cli:child-after",
        source_identity=_cli_background_source_identity("parent-after-child", "child-after", "bg-after"),
        event_type="cli-background", task_id="bg-after",
        payload={"text": "recover admitted child", "parent_session_id": "parent-after-child", "child_session_id": "child-after", "background_task_id": "bg-after"},
    )
    child_claim = state.claim_logical_turn(child["logical_turn_id"], owner="dead")
    state.fail_logical_turn(parent["logical_turn_id"], parent_claim["attempt_id"], "crashed", retryable=True)
    state.fail_logical_turn(child["logical_turn_id"], child_claim["attempt_id"], "crashed", retryable=True)

    executed = []
    recovered = recover_cli_background_children(
        state, lambda turn: executed.append(turn["logical_turn_id"]) or {"final_response": "ok"}, limit=4
    )
    assert recovered == [child["logical_turn_id"]]
    assert executed == [child["logical_turn_id"]]
    assert len(state.list_session_logical_turns("child-after")) == 1


def test_cli_startup_repairs_parent_before_child_without_manual_recovery(state, monkeypatch):
    monkeypatch.setattr("hermes_state.DEFAULT_DB_PATH", state.db_path)
    state.create_session("startup-parent", "cli")
    parent = state.admit_session_event(
        session_id="startup-parent", session_key="cli:startup-parent",
        source_identity="startup-parent-event", event_type="cli-background-command",
        payload={"text": "resume on startup", "background_task_id": "startup-child",
                 "child_session_id": "startup-child"},
    )
    executed = []
    monkeypatch.setattr(
        HermesCLI, "_execute_recovered_background_turn",
        lambda self, turn: executed.append(turn["logical_turn_id"]) or {"final_response": "ok"},
    )

    replacement = HermesCLI()
    replacement._background_recovery_thread.join(timeout=5)

    children = state.list_session_logical_turns("startup-child")
    assert len(children) == 1
    assert executed == [children[0]["logical_turn_id"]]
    assert children[0]["state"] == "completed"
    assert state.get_logical_turn(parent["logical_turn_id"])["state"] == "completed"


def test_cli_startup_recovers_same_previously_admitted_child(state, monkeypatch):
    monkeypatch.setattr("hermes_state.DEFAULT_DB_PATH", state.db_path)
    state.create_session("startup-parent-existing", "cli")
    state.create_session("startup-child-existing", "cli-background")
    child = state.admit_session_event(
        session_id="startup-child-existing", session_key="cli:startup-child-existing",
        source_identity="startup-existing-child-event", event_type="cli-background",
        task_id="startup-job-existing",
        payload={"text": "execute accepted child", "parent_session_id": "startup-parent-existing",
                 "child_session_id": "startup-child-existing",
                 "background_task_id": "startup-job-existing"},
    )
    dead_claim = state.claim_logical_turn(
        child["logical_turn_id"], owner="cli:2147483647", pid=2147483647
    )
    state.mark_logical_turn_started(child["logical_turn_id"], dead_claim["attempt_id"])
    assert state.renew_session_turn_lease(
        "startup-child-existing",
        dead_claim["turn"]["lease_holder"],
        dead_claim["attempt_id"],
        ttl_seconds=-1,
    )
    executed = []
    monkeypatch.setattr(
        HermesCLI, "_execute_recovered_background_turn",
        lambda self, turn: executed.append(turn["logical_turn_id"]) or {"final_response": "ok"},
    )

    replacement = HermesCLI()
    replacement._background_recovery_thread.join(timeout=5)

    assert executed == [child["logical_turn_id"]]
    assert len(state.list_session_logical_turns("startup-child-existing")) == 1
    assert state.get_logical_turn(child["logical_turn_id"])["state"] == "completed"


def test_cross_host_missing_pid_preserves_unexpired_lease_then_recovers_after_expiry(
    state, monkeypatch
):
    """Host B cannot steal Host A's lease using Host B's local PID table."""
    state.create_session("cross-host-child", "cli-background")
    child = state.admit_session_event(
        session_id="cross-host-child",
        session_key="cli:cross-host-child",
        source_identity="cross-host-child-event",
        event_type="cli-background",
        task_id="cross-host-task",
        payload={
            "text": "owned on host A",
            "parent_session_id": "host-a-parent",
            "child_session_id": "cross-host-child",
            "background_task_id": "cross-host-task",
        },
    )
    host_a = state.claim_logical_turn(
        child["logical_turn_id"], owner="cli:424242", pid=424242
    )
    state.mark_logical_turn_started(child["logical_turn_id"], host_a["attempt_id"])
    original_turn = state.get_logical_turn(child["logical_turn_id"])
    original_lease = state.get_session_turn_lease("cross-host-child")
    monkeypatch.setattr(cli_module, "_pid_is_alive", lambda _pid: False)

    executed = []
    assert recover_cli_background_children(
        state,
        lambda turn: executed.append(turn["logical_turn_id"]),
        limit=8,
    ) == []
    preserved = state.get_logical_turn(child["logical_turn_id"])
    assert executed == []
    assert preserved["state"] == "executing"
    assert preserved["current_attempt_id"] == original_turn["current_attempt_id"]
    assert preserved["attempt_count"] == 1
    assert state.get_session_turn_lease("cross-host-child") == original_lease

    assert state.renew_session_turn_lease(
        "cross-host-child",
        original_turn["lease_holder"],
        original_turn["current_attempt_id"],
        ttl_seconds=-1,
    )
    recovered = recover_cli_background_children(
        state,
        lambda turn: executed.append(turn["logical_turn_id"])
        or {"final_response": "safely recovered"},
        limit=8,
    )
    final = state.get_logical_turn(child["logical_turn_id"])
    assert recovered == [child["logical_turn_id"]]
    assert executed == [child["logical_turn_id"]]
    assert final["state"] == "completed"
    assert final["attempt_count"] == 2
    assert final["current_attempt_id"] != original_turn["current_attempt_id"]
    assert state.get_session_turn_lease("cross-host-child") is None


def test_background_recovery_terminal_turns_are_never_reopened(state, monkeypatch):
    monkeypatch.setattr(cli_module, "_pid_is_alive", lambda _pid: False)
    terminal_ids = []
    for suffix, retryable in (("completed", None), ("unrecoverable", False)):
        session_id = f"terminal-{suffix}"
        state.create_session(session_id, "cli-background")
        turn = state.admit_session_event(
            session_id=session_id,
            session_key=f"cli:{session_id}",
            source_identity=f"terminal-{suffix}-event",
            event_type="cli-background",
            task_id=f"terminal-{suffix}-task",
            payload={
                "text": suffix,
                "parent_session_id": "terminal-parent",
                "child_session_id": session_id,
                "background_task_id": f"terminal-{suffix}-task",
            },
        )
        claim = state.claim_logical_turn(
            turn["logical_turn_id"], owner="cli:999999", pid=999999
        )
        if retryable is None:
            state.complete_logical_turn(
                turn["logical_turn_id"], claim["attempt_id"], {"response": "done"}
            )
        else:
            state.fail_logical_turn(
                turn["logical_turn_id"], claim["attempt_id"], "terminal failure",
                retryable=retryable,
            )
        terminal_ids.append(turn["logical_turn_id"])

    executed = []
    recover_cli_background_children(
        state, lambda turn: executed.append(turn["logical_turn_id"]), limit=8
    )
    assert executed == []
    assert [state.get_logical_turn(turn_id)["state"] for turn_id in terminal_ids] == [
        "completed", "unrecoverable"
    ]


def test_unrelated_ready_rows_do_not_consume_background_recovery_budget(state):
    for index in range(8):
        session_id = f"unrelated-{index}"
        state.create_session(session_id, "gateway")
        state.admit_session_event(
            session_id=session_id,
            session_key=f"gateway:{session_id}",
            source_identity=f"unrelated-event-{index}",
            event_type="gateway-message",
            payload={"text": "unrelated"},
        )

    background_ids = []
    for index in range(3):
        session_id = f"eligible-child-{index}"
        state.create_session(session_id, "cli-background")
        child = state.admit_session_event(
            session_id=session_id,
            session_key=f"cli:{session_id}",
            source_identity=f"eligible-child-event-{index}",
            event_type="cli-background",
            task_id=f"eligible-task-{index}",
            payload={
                "text": f"background {index}",
                "parent_session_id": "eligible-parent",
                "child_session_id": session_id,
                "background_task_id": f"eligible-task-{index}",
            },
        )
        background_ids.append(child["logical_turn_id"])

    executed = []
    recover_cli_background_children(
        state,
        lambda turn: executed.append(turn["logical_turn_id"])
        or {"final_response": "done"},
        limit=8,
    )
    assert executed == background_ids
    assert all(state.get_logical_turn(turn_id)["state"] == "completed" for turn_id in background_ids)
    assert sum(
        turn["state"] == "queued"
        for index in range(8)
        for turn in state.list_session_logical_turns(f"unrelated-{index}")
    ) == 8


def test_background_recovery_uses_one_exact_budget_across_parents_and_children(state):
    parent_ids = []
    for index in range(5):
        session_id = f"budget-parent-{index}"
        state.create_session(session_id, "cli")
        parent = state.admit_session_event(
            session_id=session_id,
            session_key=f"cli:{session_id}",
            source_identity=f"budget-parent-event-{index}",
            event_type="cli-background-command",
            payload={
                "text": f"repair parent {index}",
                "background_task_id": f"budget-created-child-{index}",
                "child_session_id": f"budget-created-child-{index}",
            },
        )
        parent_ids.append(parent["logical_turn_id"])

    existing_child_ids = []
    for index in range(5):
        session_id = f"budget-existing-child-{index}"
        state.create_session(session_id, "cli-background")
        child = state.admit_session_event(
            session_id=session_id,
            session_key=f"cli:{session_id}",
            source_identity=f"budget-existing-child-event-{index}",
            event_type="cli-background",
            task_id=f"budget-existing-task-{index}",
            payload={
                "text": f"execute child {index}",
                "parent_session_id": "budget-old-parent",
                "child_session_id": session_id,
                "background_task_id": f"budget-existing-task-{index}",
            },
        )
        existing_child_ids.append(child["logical_turn_id"])

    executed = []
    recover_cli_background_children(
        state,
        lambda turn: executed.append(turn["logical_turn_id"])
        or {"final_response": "done"},
        limit=8,
    )

    all_turns = [
        turn
        for session_id in (
            [f"budget-parent-{index}" for index in range(5)]
            + [f"budget-existing-child-{index}" for index in range(5)]
            + [f"budget-created-child-{index}" for index in range(5)]
        )
        for turn in state.list_session_logical_turns(session_id)
    ]
    assert all(state.get_logical_turn(turn_id)["state"] == "completed" for turn_id in parent_ids)
    assert len(executed) == 3
    assert sum(turn["attempt_count"] for turn in all_turns) == 8
    remaining = [turn for turn in all_turns if turn["state"] in {"queued", "retry"}]
    assert len(remaining) == 7

    first_pass_executed = set(executed)
    recover_cli_background_children(
        state,
        lambda turn: executed.append(turn["logical_turn_id"])
        or {"final_response": "done"},
        limit=8,
    )
    assert len(executed) == 10
    assert first_pass_executed <= set(executed)
    final_turns = [
        turn
        for session_id in (
            [f"budget-parent-{index}" for index in range(5)]
            + [f"budget-existing-child-{index}" for index in range(5)]
            + [f"budget-created-child-{index}" for index in range(5)]
        )
        for turn in state.list_session_logical_turns(session_id)
    ]
    assert all(turn["state"] == "completed" for turn in final_turns)


def test_cli_startup_background_failure_does_not_block_next_child(state, monkeypatch):
    monkeypatch.setattr("hermes_state.DEFAULT_DB_PATH", state.db_path)
    child_ids = []
    for suffix in ("bad", "good"):
        session_id = f"startup-child-{suffix}"
        state.create_session(session_id, "cli-background")
        child = state.admit_session_event(
            session_id=session_id, session_key=f"cli:{session_id}",
            source_identity=f"startup-{suffix}-event", event_type="cli-background",
            task_id=f"startup-{suffix}",
            payload={"text": suffix, "parent_session_id": "old-parent",
                     "child_session_id": session_id, "background_task_id": f"startup-{suffix}"},
        )
        child_ids.append(child["logical_turn_id"])

    def _execute(self, turn):
        if (turn.get("payload") or {}).get("text") == "bad":
            raise RuntimeError("isolated failure")
        return {"final_response": "good"}

    monkeypatch.setattr(HermesCLI, "_execute_recovered_background_turn", _execute)
    replacement = HermesCLI()
    replacement._background_recovery_thread.join(timeout=5)

    assert state.get_logical_turn(child_ids[0])["state"] == "queued"
    assert state.get_logical_turn(child_ids[1])["state"] == "completed"


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
    cli._handle_background_command("/background durable child")
    for thread in list(cli._background_tasks.values()):
        thread.join(timeout=2)

    parent_turns = state.list_session_logical_turns("background-parent")
    assert len(parent_turns) == 2 and all(turn["state"] == "completed" for turn in parent_turns)
    task_ids = {turn["payload"]["background_task_id"] for turn in parent_turns}
    assert len(task_ids) == 2
    child_turns = [turn for task_id in task_ids for turn in state.list_session_logical_turns(task_id)]
    assert len(child_turns) == 2
    assert all(turn["state"] == "completed" for turn in child_turns)
    assert all(turn["payload"]["parent_session_id"] == "background-parent" for turn in child_turns)


def test_background_parent_replay_retains_its_persisted_child_identity(state):
    state.create_session("parent-replay", "cli")
    cli = _cli(state, "parent-replay")
    first = cli._admit_cli_logical_turn(
        "same background command", event_type="cli-background-command", source_identity="parent-event-1",
        payload={"background_task_id": "child-id-1", "child_session_id": "child-id-1"},
    )
    state.fail_logical_turn(first["logical_turn_id"], first["attempt_id"], "crashed", retryable=True)
    replay = cli._admit_cli_logical_turn(
        "same background command", event_type="cli-background-command", source_identity="parent-event-1",
        payload={"background_task_id": "new-child-must-not-win", "child_session_id": "new-child-must-not-win"},
    )
    assert replay["logical_turn_id"] == first["logical_turn_id"]
    assert replay["turn"]["payload"]["background_task_id"] == "child-id-1"


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


def test_explicit_completed_kanban_iteration_two_replay_resolves_two_not_four(state, monkeypatch):
    state.create_session("worker-exact-replay", "cli")
    cli = _cli(state, "worker-exact-replay")
    for iteration in (1, 2, 3):
        claim = cli._admit_cli_logical_turn(
            "identical continuation", event_type="kanban-goal-turn",
            source_identity=_cli_kanban_turn_source_identity("task-exact", None, iteration),
            task_id="task-exact", payload={"iteration": iteration},
        )
        cli._finish_cli_logical_turn(claim, {"final_response": f"turn {iteration}"})
    iteration_two = _cli_kanban_turn_source_identity("task-exact", None, 2)
    monkeypatch.setenv("HERMES_CLI_SOURCE_ID", iteration_two)
    assert _resolve_kanban_iteration(state, "worker-exact-replay", "task-exact") == 2


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
