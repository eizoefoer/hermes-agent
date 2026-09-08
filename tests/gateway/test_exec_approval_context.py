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

@pytest.mark.asyncio
@pytest.mark.parametrize("command,decision", [("/approve", "once"), ("/deny", "deny")])
async def test_typed_decision_survives_loss_of_process_local_queue(tmp_path, command, decision):
    from gateway.run import GatewayRunner
    from gateway.config import Platform
    from gateway.platforms.base import MessageEvent
    from gateway.session import SessionSource
    from gateway.telegram_approval import TelegramApprovalService
    from unittest.mock import MagicMock
    with ApprovalStore(tmp_path / "approvals.db") as store:
        service = TelegramApprovalService(store, process_local_resolver=lambda *args: 0)
        service.create(request_id="request", session_key="session-key", continuation_kind="hermes_session",
            payload={"session_id": "session-1", "approval_user_id": "user-7", "process_local_fast_path": True},
            idempotency_key="request-key")
        class Adapter:
            def _get_telegram_approval_service(self):
                return service
        runner = object.__new__(GatewayRunner)
        runner.adapters = {Platform.TELEGRAM: Adapter()}
        runner._session_key_for_source = lambda source: "session-key"
        source = SessionSource(platform=Platform.TELEGRAM, chat_id="7", user_id="user-7", chat_type="dm")
        event = MessageEvent(text=command, source=source, message_id="message-1")
        handler = runner._handle_deny_command if decision == "deny" else runner._handle_approve_command
        result = await handler(event)
        assert "1 command(s)" in result
        assert store.get_request("request").decision == decision
        assert store.claim_next("worker").decision == decision
        assert store.pending_requests("session-key") == []

@pytest.mark.asyncio
async def test_typed_approval_is_scoped_to_owner_and_allowed_choice(tmp_path):
    from gateway.run import GatewayRunner
    from gateway.config import Platform
    from gateway.platforms.base import MessageEvent
    from gateway.session import SessionSource
    from gateway.telegram_approval import TelegramApprovalService
    with ApprovalStore(tmp_path / "approvals.db") as store:
        service = TelegramApprovalService(store, process_local_resolver=lambda *args: 0)
        service.create(request_id="request", session_key="session-key", continuation_kind="hermes_session",
            payload={"approval_user_id": "user-7"}, idempotency_key="key")
        class Adapter:
            def _get_telegram_approval_service(self): return service
        runner = object.__new__(GatewayRunner)
        runner.adapters = {Platform.TELEGRAM: Adapter()}
        source = SessionSource(platform=Platform.TELEGRAM, chat_id="7", user_id="other", chat_type="dm")
        event = MessageEvent(text="/approve", source=source, message_id="m")
        assert runner._resolve_durable_telegram_command(event, "session-key") is None
        source.user_id = "user-7"
        event.text = "/approve session"
        assert "one operation only" in runner._resolve_durable_telegram_command(event, "session-key")
        assert store.get_request("request").decision is None
        assert runner._resolve_durable_telegram_command(event, "different-session") is None
