"""TUI durable logical-turn admission regressions."""

import sys
import threading
import time
import types
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


def test_tui_ordinary_prompt_ignores_invented_client_correlations(monkeypatch, tmp_path):
    db = _db(tmp_path)
    task_ids = []

    class _Agent:
        def run_conversation(
            self,
            prompt,
            conversation_history=None,
            stream_callback=None,
            task_id=None,
        ):
            task_ids.append(task_id)
            return {"final_response": "ok", "messages": []}

    class _ImmediateThread:
        def __init__(self, target=None, daemon=None, **_kwargs):
            self._target = target

        def start(self):
            self._target()

    session = _session(agent=_Agent())
    server._sessions["sid"] = session
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(server, "_emit", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(server, "make_stream_renderer", lambda _cols: None)
    monkeypatch.setattr(server, "render_message", lambda *_args: None)
    monkeypatch.setattr(server, "_session_info", lambda *_args: {})
    monkeypatch.setattr(server, "_sync_session_key_after_compress", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "_rehydrate_tui_session_turns", lambda *_args: 0)
    monkeypatch.setattr("agent.title_generator.maybe_auto_title", lambda *_args: None)

    try:
        response = server.handle_request(
            {
                "id": "prompt-rpc",
                "method": "prompt.submit",
                "params": {
                    "session_id": "sid",
                    "message_id": "message-1",
                    "text": "hello",
                    "task_id": "tui-session",
                    "goal_id": "tui-session",
                },
            }
        )

        assert response["result"]["status"] == "streaming"
        assert task_ids == [None]
        turn = db.list_session_logical_turns("tui-session")[0]
        assert turn["task_id"] is None
        assert turn["goal_id"] is None
    finally:
        server._sessions.pop("sid", None)


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


def test_tui_identical_goal_prompts_use_durable_evaluation_sequence(monkeypatch, tmp_path):
    db = _db(tmp_path)
    session = _session()
    monkeypatch.setattr(server, "_get_db", lambda: db)
    state = SimpleNamespace(
        created_at=100.0,
        goal_id=None,
        last_turn_at=0.0,
        turns_used=0,
    )
    manager = SimpleNamespace(session_id="tui-session", state=state)
    prompt = "[Continuing toward your standing goal]\nSame prompt"
    identities = []

    for iteration in range(1, 4):
        state.turns_used = iteration
        state.last_turn_at = 200.0 + iteration
        source_identity, goal_id = server._tui_goal_evaluation_metadata(manager)
        identities.append(source_identity)
        claim = server._admit_tui_turn(
            "sid",
            session,
            prompt,
            event_type="tui.goal.continuation",
            source_identity=source_identity,
            goal_id=goal_id,
        )
        assert claim["outcome"] == "claimed"
        server._finish_tui_turn(
            claim,
            result={"final_response": "progress", "messages": []},
            execution_outcome="completed",
        )

    replay = server._admit_tui_turn(
        "sid",
        session,
        prompt,
        event_type="tui.goal.continuation",
        source_identity=identities[-1],
        goal_id=None,
    )

    assert len(set(identities)) == 3
    assert replay["outcome"] == "terminal"
    turns = db.list_session_logical_turns("tui-session")
    assert len(turns) == 3
    assert [turn["payload"]["text"] for turn in turns] == [prompt, prompt, prompt]
    assert all(turn["goal_id"] is None for turn in turns)


def test_tui_goal_metadata_uses_real_goal_id_when_exposed():
    manager = SimpleNamespace(
        session_id="session-1",
        state=SimpleNamespace(
            created_at=100.0,
            goal_id="goal-42",
            last_turn_at=101.0,
            turns_used=1,
        ),
    )

    source_identity, goal_id = server._tui_goal_evaluation_metadata(manager)

    assert goal_id == "goal-42"
    assert source_identity.startswith("tui:goal:goal-42:evaluated:")


def test_tui_completion_is_durable_before_busy_dispatch(monkeypatch, tmp_path):
    db = _db(tmp_path)
    session = _session(running=True)
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "_emit", lambda *_args, **_kwargs: True)
    active = server._admit_tui_turn(
        "sid",
        session,
        "active",
        event_type="tui.prompt",
        source_identity="tui:prompt:tui-session:active",
    )
    assert active["outcome"] == "claimed"
    event = {
        "type": "completion",
        "session_id": "process-7",
        "command": "pytest",
        "exit_code": 0,
        "output": "ok",
    }

    outcome = server._admit_and_dispatch_tui_completion(
        "sid", session, event, "process finished", rid="completion-1"
    )
    replay = server._admit_and_dispatch_tui_completion(
        "sid", session, dict(event), "process finished", rid="completion-2"
    )

    assert outcome == "busy"
    assert replay == "busy"
    turns = db.list_session_logical_turns("tui-session")
    assert len(turns) == 2
    queued = [turn for turn in turns if turn["state"] == "queued"]
    assert len(queued) == 1
    assert queued[0]["source_identity"] == "tui:process:completion:process-7"


def test_tui_busy_retry_flows_into_durable_prompt_queue(monkeypatch, tmp_path):
    db = _db(tmp_path)
    history = [
        {"role": "user", "content": "previous"},
        {"role": "assistant", "content": "previous answer"},
    ]
    session = _session(
        agent=SimpleNamespace(),
        history=list(history),
        inflight_turn={
            "assistant": "partial",
            "streaming": True,
            "user": "retry this active request",
        },
        running=True,
    )
    server._sessions["sid"] = session
    monkeypatch.setattr(server, "_get_db", lambda: db)
    active = server._admit_tui_turn(
        "sid",
        session,
        "retry this active request",
        event_type="tui.prompt",
        source_identity="tui:prompt:tui-session:active-request",
    )
    assert active["outcome"] == "claimed"

    try:
        retry = server.handle_request(
            {
                "id": "retry-command",
                "method": "command.dispatch",
                "params": {"name": "retry", "session_id": "sid"},
            }
        )["result"]
        queued = server.handle_request(
            {
                "id": "retry-submit",
                "method": "prompt.submit",
                "params": {
                    "session_id": "sid",
                    "message_id": "retry-message-1",
                    "text": retry["message"],
                },
            }
        )["result"]

        assert retry["type"] == "send"
        assert queued["status"] == "queued"
        assert session["history"] == history
        turns = db.list_session_logical_turns("tui-session")
        assert len(turns) == 2
        assert turns[1]["payload"]["text"] == "retry this active request"
        assert turns[1]["state"] == "queued"
    finally:
        server._sessions.pop("sid", None)


def test_tui_session_attach_rehydrates_after_process_restart(monkeypatch, tmp_path):
    path = tmp_path / "state.db"
    original = SessionDB(path)
    original.create_session("tui-session", "tui")
    admitted = original.admit_session_event(
        session_id="tui-session",
        session_key="tui:tui-session",
        source_identity="tui:prompt:tui-session:restart",
        event_type="tui.prompt",
        payload={"text": "execute after restart", "event_type": "tui.prompt"},
    )
    original.close()
    recovered = SessionDB(path)
    calls = []

    class _Agent:
        model = "test-model"
        session_id = "tui-session"

        def run_conversation(self, prompt, conversation_history=None, stream_callback=None):
            calls.append(prompt)
            return {
                "final_response": "recovered",
                "messages": [
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": "recovered"},
                ],
            }

    monkeypatch.setattr(server, "_get_db", lambda: recovered)
    monkeypatch.setattr(server, "_SlashWorker", lambda *_args: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(server, "_start_notification_poller", lambda *_args: threading.Event())
    monkeypatch.setattr(server, "_notify_session_boundary", lambda *_args: None)
    monkeypatch.setattr(server, "_wire_callbacks", lambda *_args: None)
    monkeypatch.setattr(server, "_emit", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(server, "_session_info", lambda *_args: {"model": "test-model"})
    monkeypatch.setattr(server, "make_stream_renderer", lambda _cols: None)
    monkeypatch.setattr(server, "render_message", lambda *_args: None)
    monkeypatch.setattr(server, "_sync_session_key_after_compress", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("agent.title_generator.maybe_auto_title", lambda *_args: None)

    try:
        server._init_session("new-process-sid", "tui-session", _Agent(), [])

        deadline = time.time() + 2
        turn = recovered.get_logical_turn(admitted["logical_turn_id"])
        while turn["state"] != "completed" and time.time() < deadline:
            time.sleep(0.01)
            turn = recovered.get_logical_turn(admitted["logical_turn_id"])
        assert calls == ["execute after restart"]
        assert turn["logical_turn_id"] == admitted["logical_turn_id"]
        assert turn["attempt_count"] == 1
        assert turn["state"] == "completed"
    finally:
        server._sessions.pop("new-process-sid", None)


def test_tui_validation_blocked_is_not_recorded_as_success(monkeypatch, tmp_path):
    db = _db(tmp_path)
    called = []

    class _Agent:
        model = "test-model"
        base_url = ""
        api_key = ""
        provider = "test"

        def run_conversation(self, *_args, **_kwargs):
            called.append(True)
            raise AssertionError("model execution must not start")

    class _ImmediateThread:
        def __init__(self, target=None, daemon=None, **_kwargs):
            self._target = target

        def start(self):
            self._target()

    session = _session(agent=_Agent())
    claim = None
    monkeypatch.setattr(server, "_get_db", lambda: db)
    claim = server._admit_tui_turn(
        "sid",
        session,
        "@outside",
        event_type="tui.prompt",
        source_identity="tui:prompt:tui-session:blocked",
    )
    context_module = types.ModuleType("agent.context_references")
    context_module.preprocess_context_references = lambda *_args, **_kwargs: SimpleNamespace(
        blocked=True,
        message="",
        warnings=["outside allowed root"],
    )
    metadata_module = types.ModuleType("agent.model_metadata")
    metadata_module.get_model_context_length = lambda *_args, **_kwargs: 1000
    monkeypatch.setitem(sys.modules, "agent.context_references", context_module)
    monkeypatch.setitem(sys.modules, "agent.model_metadata", metadata_module)
    monkeypatch.setattr(server.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(server, "_emit", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(server, "make_stream_renderer", lambda _cols: None)
    monkeypatch.setattr(server, "_session_info", lambda *_args: {})
    monkeypatch.setattr(server, "_rehydrate_tui_session_turns", lambda *_args: 0)

    server._dispatch_admitted_tui_turn("sid", session, claim)

    turn = db.get_logical_turn(claim["logical_turn_id"])
    assert called == []
    assert turn["state"] == "unrecoverable"
    assert turn["result"] is None
    assert "outside allowed root" in turn["error"]


def test_tui_transport_acceptance_is_not_client_delivery(monkeypatch, tmp_path):
    db = _db(tmp_path)
    session = _session()
    monkeypatch.setattr(server, "_get_db", lambda: db)
    claim = server._admit_tui_turn(
        "sid",
        session,
        "run",
        event_type="tui.prompt",
        source_identity="tui:prompt:tui-session:transport",
    )

    completed = server._finish_tui_turn(
        claim,
        result={"final_response": "done", "messages": []},
        delivery_succeeded=True,
        execution_outcome="completed",
    )

    assert completed["delivery_state"] == "transport_accepted"
    pending = db.list_pending_logical_turn_deliveries(
        include_transport_accepted=True,
        session_id="tui-session",
    )
    assert pending[0]["logical_turn_id"] == claim["logical_turn_id"]
    assert db.list_pending_logical_turn_deliveries() == []
    replayed = []
    monkeypatch.setattr(
        server,
        "_emit",
        lambda event, _sid, payload=None: replayed.append((event, payload)) or True,
    )

    assert server._rehydrate_tui_session_deliveries("new-sid", session) == 1
    assert replayed == [
        (
            "message.complete",
            {"text": "done", "status": "complete", "replayed_delivery": True},
        )
    ]
    assert db.get_logical_turn(claim["logical_turn_id"])["state"] == "completed"
    replayed.clear()
    hydrated_session = _session(
        history=[{"role": "assistant", "content": "done"}]
    )
    assert server._rehydrate_tui_session_deliveries(
        "hydrated-sid", hydrated_session
    ) == 0
    assert replayed == []


def test_tui_background_is_persistent_and_preview_restart_is_ephemeral(monkeypatch, tmp_path):
    db = _db(tmp_path, key="parent-session")
    calls = []
    init_kwargs = []

    class _BackgroundAgent:
        def __init__(self, **kwargs):
            init_kwargs.append(kwargs)

        def run_conversation(self, user_message, task_id=None, **_kwargs):
            calls.append((user_message, task_id))
            return {"final_response": "background done", "messages": []}

    class _ImmediateThread:
        def __init__(self, target=None, daemon=None, **_kwargs):
            self._target = target

        def start(self):
            self._target()

    run_agent_module = types.ModuleType("run_agent")
    run_agent_module.AIAgent = _BackgroundAgent
    parent_agent = SimpleNamespace(model="test-model")
    parent_session = _session("parent-session", agent=parent_agent)
    server._sessions["parent-sid"] = parent_session
    monkeypatch.setitem(sys.modules, "run_agent", run_agent_module)
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(server, "_emit", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(server, "_load_cfg", lambda: {})
    monkeypatch.setattr(server, "_resolve_model", lambda: "test-model")

    try:
        request = {
            "id": "background-rpc-1",
            "method": "prompt.background",
            "params": {"session_id": "parent-sid", "text": "work independently"},
        }
        first = server.handle_request(request)["result"]
        replay = server.handle_request(request)["result"]

        assert first["status"] == "running"
        assert replay["status"] == "duplicate"
        assert calls == [("work independently", first["task_id"])]
        assert init_kwargs[0]["session_id"] == first["task_id"]
        assert init_kwargs[0]["session_db"] is db
        turn = db.list_session_logical_turns(first["task_id"])[0]
        assert turn["task_id"] == first["task_id"]
        assert turn["goal_id"] is None
        assert turn["state"] == "completed"
        preview_kwargs = server._ephemeral_preview_agent_kwargs(
            parent_agent, "preview-helper"
        )
        assert preview_kwargs["session_db"] is None
        assert preview_kwargs["skip_memory"] is True
        assert server.TUI_REASONING_PRODUCERS["prompt.background"] == "ADMIT_NEW_LOGICAL_TURN"
        assert server.TUI_REASONING_PRODUCERS["preview.restart"] == "EPHEMERAL_HELPER"
    finally:
        server._sessions.pop("parent-sid", None)
