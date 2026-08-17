"""TUI durable logical-turn admission regressions."""

import threading
from types import SimpleNamespace

from hermes_state import SessionDB
from tui_gateway import server


def _session(key: str = "tui-session", **extra) -> dict:
    value = {
        "session_key": key,
        "history": [],
        "history_lock": threading.RLock(),
        "history_version": 0,
        "running": False,
        "attached_images": [],
        "cols": 80,
        "cwd": "/tmp/tui-worktree",
    }
    value.update(extra)
    return value


def _db(tmp_path, key: str = "tui-session") -> SessionDB:
    db = SessionDB(tmp_path / "state.db")
    db.create_session(key, "tui")
    return db


def test_tui_prompt_admission_uses_request_identity_and_real_sessiondb(monkeypatch, tmp_path):
    db = _db(tmp_path)
    session = _session()
    monkeypatch.setattr(server, "_get_db", lambda: db)

    first = server._admit_tui_turn(
        "transient-sid", session, "hello", event_type="tui.prompt",
        source_identity=server._tui_prompt_source_identity(session, {"message_id": "client-7"}, "rpc-1"),
    )
    replay = server._admit_tui_turn(
        "new-transient-sid", session, "hello", event_type="tui.prompt",
        source_identity=server._tui_prompt_source_identity(session, {"message_id": "client-7"}, "rpc-2"),
    )

    assert first["outcome"] == "claimed"
    assert replay["outcome"] == "busy"
    turns = db.list_session_logical_turns("tui-session")
    assert len(turns) == 1
    assert turns[0]["source_identity"] == "tui:prompt:tui-session:client-7"
    assert turns[0]["task_id"] is None


def test_tui_busy_prompt_is_durably_queued_not_rejected(monkeypatch, tmp_path):
    db = _db(tmp_path)
    session = _session()
    monkeypatch.setattr(server, "_get_db", lambda: db)
    active = server._admit_tui_turn(
        "sid", session, "first", event_type="tui.prompt", source_identity="tui:prompt:tui-session:first",
    )

    queued = server._admit_tui_turn(
        "sid", session, "second", event_type="tui.prompt", source_identity="tui:prompt:tui-session:second",
    )

    assert active["outcome"] == "claimed"
    assert queued["outcome"] == "busy"
    assert db.next_queued_logical_turn("tui-session")["payload"]["text"] == "second"


def test_tui_goal_identity_uses_goal_and_durable_continuation_identity():
    assert server._tui_goal_source_identity("goal-9", "continuation-2") == "tui:goal:goal-9:continuation-2"


def test_tui_completion_identity_is_deterministic():
    event = {"process_id": "proc-1", "session_id": "tui-session", "exit_code": 0, "command": "pytest"}
    assert server._tui_completion_source_identity(event) == server._tui_completion_source_identity(dict(event))


def test_tui_restart_rehydrates_queued_turn_without_new_logical_turn(monkeypatch, tmp_path):
    db = _db(tmp_path)
    queued = db.admit_session_event(
        session_id="tui-session", session_key="tui:tui-session",
        source_identity="tui:prompt:tui-session:recover-1", event_type="tui.prompt",
        payload={"text": "recover me", "tui_session_id": "old-sid"},
    )
    session = _session(agent=SimpleNamespace())
    monkeypatch.setattr(server, "_get_db", lambda: db)
    dispatched = []
    monkeypatch.setattr(server, "_dispatch_admitted_tui_turn", lambda sid, sess, claim: dispatched.append((sid, claim)))

    assert server._rehydrate_tui_session_turns("new-sid", session) == 1
    assert dispatched[0][0] == "new-sid"
    assert dispatched[0][1]["turn"]["logical_turn_id"] == queued["logical_turn_id"]
    assert db.count_logical_turns("tui-session") == 1


def test_tui_completion_persists_execution_before_delivery_ack(monkeypatch, tmp_path):
    db = _db(tmp_path)
    session = _session()
    monkeypatch.setattr(server, "_get_db", lambda: db)
    claim = server._admit_tui_turn(
        "sid", session, "run", event_type="tui.prompt", source_identity="tui:prompt:tui-session:delivery",
    )

    completed = server._finish_tui_turn(
        claim, result={"final_response": "done", "messages": []}, delivery_succeeded=False,
    )

    assert completed["state"] == "completed"
    assert completed["result"]["response"] == "done"
    assert completed["delivery_state"] == "pending"
    assert db.claim_logical_turn(completed["logical_turn_id"], owner="other", pid=2)["outcome"] == "terminal"


def test_tui_requires_store_unless_explicit_test_ephemeral_mode(monkeypatch):
    monkeypatch.setattr(server, "_get_db", lambda: None)
    monkeypatch.setattr(server, "_explicit_tui_ephemeral_mode", lambda: False)

    unavailable = server._admit_tui_turn(
        "sid", _session(), "no store", event_type="tui.prompt", source_identity="tui:prompt:tui-session:no-store",
    )

    assert unavailable["outcome"] == "unavailable"
