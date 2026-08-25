import asyncio
import threading
from unittest.mock import MagicMock, patch

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from hermes_state import SessionDB


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    value = APIServerAdapter(PlatformConfig(enabled=True, extra={"key": "secret"}))
    value._session_db = SessionDB(db_path=tmp_path / "state.db")
    return value


def _agent(result=None):
    agent = MagicMock()
    agent.run_conversation.return_value = result or {"final_response": "ok"}
    agent.session_prompt_tokens = 1
    agent.session_completion_tokens = 1
    agent.session_total_tokens = 2
    return agent


@pytest.mark.asyncio
async def test_authoritative_request_replay_is_one_logical_turn(adapter):
    fake = _agent()
    with patch.object(adapter, "_create_agent", return_value=fake):
        first = await adapter._run_agent(
            "same", [], session_id="api-session", source_identity="api:test:req-1"
        )
        replay = await adapter._run_agent(
            "same", [], session_id="api-session", source_identity="api:test:req-1"
        )

    assert first == replay
    assert fake.run_conversation.call_count == 1
    assert adapter._session_db.count_logical_turns("api-session") == 1


@pytest.mark.asyncio
async def test_identical_separate_requests_remain_distinct(adapter):
    fake = _agent()
    with patch.object(adapter, "_create_agent", return_value=fake):
        await adapter._run_agent("same", [], session_id="api-session", source_identity="api:test:req-1")
        await adapter._run_agent("same", [], session_id="api-session", source_identity="api:test:req-2")

    assert fake.run_conversation.call_count == 2
    assert adapter._session_db.count_logical_turns("api-session") == 2


@pytest.mark.asyncio
async def test_authoritative_task_id_is_persisted_and_forwarded(adapter):
    fake = _agent()
    with patch.object(adapter, "_create_agent", return_value=fake):
        await adapter._run_agent(
            "task-backed",
            [],
            session_id="api-task-session",
            source_identity="api:test:task-request",
            task_id="T1",
        )

    turn = adapter._session_db.list_session_logical_turns("api-task-session")[-1]
    assert turn["task_id"] == "T1"
    assert turn["goal_id"] is None
    assert fake.run_conversation.call_args.kwargs["task_id"] == "T1"


@pytest.mark.asyncio
async def test_same_session_requests_serialize_on_durable_lease(adapter):
    entered = threading.Event()
    release = threading.Event()
    fake = _agent()

    def run(**_kwargs):
        entered.set()
        release.wait(5)
        return {"final_response": "ok"}

    fake.run_conversation.side_effect = run
    with patch.object(adapter, "_create_agent", return_value=fake):
        first = asyncio.create_task(
            adapter._run_agent("one", [], session_id="api-session", source_identity="api:test:one")
        )
        await asyncio.to_thread(entered.wait, 2)
        second = asyncio.create_task(
            adapter._run_agent("two", [], session_id="api-session", source_identity="api:test:two")
        )
        await asyncio.sleep(0.15)
        assert fake.run_conversation.call_count == 1
        release.set()
        await asyncio.gather(first, second)

    assert fake.run_conversation.call_count == 2


@pytest.mark.asyncio
async def test_startup_recovery_executes_same_queued_turn(adapter):
    db = adapter._session_db
    db.create_session("api-restart", "api_server")
    turn = db.admit_session_event(
        session_id="api-restart",
        session_key="api:api-restart",
        source_identity="api:test:restart",
        event_type="api-session-chat",
        payload={"user_message": "recover me", "conversation_history": []},
    )
    fake = _agent()
    with patch.object(adapter, "_create_agent", return_value=fake):
        assert await adapter._recover_api_logical_turns() == 1
        await asyncio.gather(*list(adapter._background_tasks))

    recovered = db.get_logical_turn(turn["logical_turn_id"])
    assert recovered["state"] == "completed"
    assert recovered["logical_turn_id"] == turn["logical_turn_id"]
    fake.run_conversation.assert_called_once()


@pytest.mark.asyncio
async def test_startup_does_not_steal_unexpired_lease(adapter):
    db = adapter._session_db
    db.create_session("api-owned", "api_server")
    turn = db.admit_session_event(
        session_id="api-owned",
        session_key="api:api-owned",
        source_identity="api:test:owned",
        event_type="api-session-chat",
        payload={"user_message": "owned", "conversation_history": []},
    )
    claim = db.claim_logical_turn(turn["logical_turn_id"], owner="other", pid=999999)
    db.mark_logical_turn_started(turn["logical_turn_id"], claim["attempt_id"])

    with patch.object(adapter, "_create_agent") as create:
        assert await adapter._recover_api_logical_turns() == 0
    create.assert_not_called()
    assert db.get_logical_turn(turn["logical_turn_id"])["current_attempt_id"] == claim["attempt_id"]
