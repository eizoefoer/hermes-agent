"""Durable approval identity comes from the turn context, including group chats."""
from types import SimpleNamespace
import pytest
from gateway.run import _exec_approval_metadata
from gateway.approval_store import ApprovalStore

@pytest.mark.parametrize("user,chat,expected", [("user-7", "group-9", "user-7"), (None, "7", "7")])
def test_persisted_approval_uses_turn_source(tmp_path, user, chat, expected):
    ctx = SimpleNamespace(source=SimpleNamespace(user_id=user), _status_chat_id=chat,
        _status_thread_metadata={"thread_id": "topic-1"}, session_id="session-1",
        task_id="task-1", goal_id=None, branch="branch-1", worktree="/workspace",
        parent_logical_turn_id="turn-1")
    data = {"pattern_key": "pattern", "pattern_keys": ["pattern"]}
    result = _exec_approval_metadata(ctx, data, "echo reviewed", "test")
    continuation = result["approval_continuation"]
    assert continuation["payload"]["approval_user_id"] == expected
    assert result["thread_id"] == "topic-1"
    assert "approval_continuation" not in ctx._status_thread_metadata
    with ApprovalStore(tmp_path / "approvals.db") as store:
        request = store.create_request(request_id="approval-1", session_key="session-key",
            continuation_kind=continuation["kind"], payload=continuation["payload"],
            idempotency_key=continuation["idempotency_key"])
        assert request.payload["approval_user_id"] == expected
    second = _exec_approval_metadata(ctx, data, "echo reviewed", "test")
    assert second["approval_continuation"]["idempotency_key"] != continuation["idempotency_key"]
