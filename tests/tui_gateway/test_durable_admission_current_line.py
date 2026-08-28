"""Current-line Phase 1 durability regressions for Desktop/TUI turns."""

import threading
import time
from types import SimpleNamespace

from hermes_state import SessionDB
from tui_gateway import server


def _session(key: str) -> dict:
    return {
        "session_key": key,
        "history": [],
        "history_lock": threading.Lock(),
        "running": False,
        "transport": object(),
    }


def test_tui_admission_uses_real_ledger_and_lease(tmp_path, monkeypatch):
    db = SessionDB(tmp_path / "state.db")
    db.create_session("tui-session", "tui")
    monkeypatch.setattr(server, "_get_db", lambda: db)
    session = _session("tui-session")

    claim = server._admit_tui_turn(
        "ui-1", session, "hello", source_identity="tui-request-1"
    )
    assert claim["outcome"] == "claimed"
    turn = db.get_logical_turn(claim["logical_turn_id"])
    assert turn["state"] == "executing"
    assert turn["task_id"] is None
    assert turn["goal_id"] is None
    lease = db.get_session_turn_lease(turn["lease_conversation_id"])
    assert lease is not None
    assert lease["holder"] == turn["lease_holder"]

    server._finish_tui_turn(
        session, claim, {"final_response": "done"}, delivery_succeeded=True
    )
    completed = db.get_logical_turn(claim["logical_turn_id"])
    assert completed["state"] == "completed"
    assert completed["delivery_state"] == "transport_accepted"


def test_busy_tui_occurrence_is_durable_before_local_queue(tmp_path, monkeypatch):
    db = SessionDB(tmp_path / "state.db")
    db.create_session("busy", "tui")
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "_load_busy_input_mode", lambda: "queue")
    session = _session("busy")
    first = server._admit_tui_turn(
        "ui", session, "first", source_identity="tui-first"
    )
    session["running"] = True

    response = server._handle_busy_submit("rpc", "ui", session, "second", None)
    assert response["result"]["status"] == "queued"
    turns = db.list_session_logical_turns("busy")
    assert [turn["state"] for turn in turns] == ["executing", "queued"]
    assert session["queued_prompt"]["logical_turn_id"] == turns[1]["logical_turn_id"]
    server._finish_tui_turn(session, first, {"final_response": "first"})


def test_tui_restart_rehydrates_same_queued_logical_turn(tmp_path, monkeypatch):
    db = SessionDB(tmp_path / "state.db")
    db.create_session("restart", "tui")
    monkeypatch.setattr(server, "_get_db", lambda: db)
    admitted = db.admit_session_event(
        session_id="restart",
        session_key="tui:restart",
        source_identity="accepted-before-restart",
        event_type="tui-prompt",
        payload={"text": "survive restart"},
    )
    session = _session("restart")
    dispatched = []
    monkeypatch.setattr(
        server,
        "_drain_queued_prompt",
        lambda _rid, _sid, current: dispatched.append(
            current["queued_prompt"]["logical_turn_id"]
        ) or True,
    )

    assert server._rehydrate_tui_session_work("ui-restart", session) == 1
    assert dispatched == [admitted["logical_turn_id"]]
    assert session["queued_prompt"]["logical_turn_id"] == admitted["logical_turn_id"]


def test_notification_occurrence_policy_preserves_replay_and_distinct_matches():
    first = {"type": "watch_match", "session_id": "proc"}
    replay_identity = server._tui_notification_source_identity(first)
    assert server._tui_notification_source_identity(first) == replay_identity
    second = {"type": "watch_match", "session_id": "proc"}
    assert server._tui_notification_source_identity(second) != replay_identity
    authoritative = {"type": "watch_match", "event_id": "evt-1"}
    assert server._tui_notification_source_identity(authoritative) == "tui:watch_match:evt-1"


def test_prompt_background_persists_child_before_success_and_replay_is_stable(
    tmp_path, monkeypatch
):
    db = SessionDB(tmp_path / "state.db")
    db.create_session("parent", "tui")
    monkeypatch.setattr(server, "_get_db", lambda: db)
    emitted = []
    monkeypatch.setattr(server, "_emit", lambda *args, **_kwargs: emitted.append(args))

    class FakeAgent:
        def __init__(self, **_kwargs):
            pass

        def run_conversation(self, **_kwargs):
            return {"final_response": "child done"}

    monkeypatch.setattr("run_agent.AIAgent", FakeAgent)
    parent = _session("parent")
    parent["agent"] = SimpleNamespace(model="test-model")
    parent["cwd"] = str(tmp_path)
    server._sessions["ui-parent"] = parent
    try:
        first = server.handle_request(
            {
                "id": "rpc-occurrence-1",
                "method": "prompt.background",
                "params": {"session_id": "ui-parent", "text": "work"},
            }
        )
        task_id = first["result"]["task_id"]
        deadline = time.time() + 5
        while time.time() < deadline:
            turns = db.list_session_logical_turns(task_id)
            if turns and turns[0]["state"] == "completed":
                break
            time.sleep(0.02)
        assert len(db.list_session_logical_turns(task_id)) == 1
        replay = server.handle_request(
            {
                "id": "rpc-occurrence-1",
                "method": "prompt.background",
                "params": {"session_id": "ui-parent", "text": "work"},
            }
        )
        assert replay["result"]["task_id"] == task_id
        second = server.handle_request(
            {
                "id": "rpc-occurrence-2",
                "method": "prompt.background",
                "params": {"session_id": "ui-parent", "text": "work"},
            }
        )
        assert second["result"]["task_id"] != task_id
    finally:
        server._sessions.pop("ui-parent", None)


def test_background_child_startup_recovery_executes_same_turn(tmp_path, monkeypatch):
    db = SessionDB(tmp_path / "state.db")
    db.create_session("parent-recovery", "tui")
    db.create_session("child-recovery", "tui-background-child")
    monkeypatch.setattr(server, "_get_db", lambda: db)
    child = db.admit_session_event(
        session_id="child-recovery",
        session_key="tui:child-recovery",
        source_identity="child-recovery-event",
        event_type="tui-background-child",
        payload={
            "text": "recover me",
            "parent_session_id": "parent-recovery",
            "background_task_id": "child-recovery",
        },
        task_id="child-recovery",
    )

    class FakeAgent:
        def __init__(self, **_kwargs):
            pass

        def run_conversation(self, **_kwargs):
            return {"final_response": "recovered"}

    monkeypatch.setattr("run_agent.AIAgent", FakeAgent)
    parent = _session("parent-recovery")
    parent["agent"] = SimpleNamespace(model="test-model")
    parent["cwd"] = str(tmp_path)
    assert server._recover_tui_background_children("ui", parent) == 1
    deadline = time.time() + 5
    while time.time() < deadline:
        if db.get_logical_turn(child["logical_turn_id"])["state"] == "completed":
            break
        time.sleep(0.02)
    assert db.get_logical_turn(child["logical_turn_id"])["state"] == "completed"


def test_background_recovery_filters_parent_before_bounded_limit(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    for index in range(4):
        session_id = f"foreign-{index}"
        db.create_session(session_id, "tui-background-child")
        db.admit_session_event(
            session_id=session_id,
            session_key=f"tui:{session_id}",
            source_identity=f"foreign-event-{index}",
            event_type="tui-background-child",
            payload={"text": "foreign", "parent_session_id": "other"},
        )
    db.create_session("target-child", "tui-background-child")
    target = db.admit_session_event(
        session_id="target-child",
        session_key="tui:target-child",
        source_identity="target-event",
        event_type="tui-background-child",
        payload={"text": "target", "parent_session_id": "target-parent"},
    )
    ready = db.list_ready_logical_turns(
        limit=1,
        event_types=("tui-background-child",),
        payload_equals={"parent_session_id": "target-parent"},
    )
    assert [turn["logical_turn_id"] for turn in ready] == [target["logical_turn_id"]]
