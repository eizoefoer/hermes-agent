"""Signed callback persistence and background ownership integration."""

import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway import signed_telegram_approval as signed


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_notification", [False, True])
async def test_callback_claims_resume_once_and_retains_task(monkeypatch, tmp_path, fail_notification):
    monkeypatch.delenv("HERMES_APPROVAL_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(signed, "_RESUME_REGISTRY", dict(signed._RESUME_REGISTRY))
    calls = []

    def resume(request):
        calls.append(request["task_id"])
        return signed.ResumeResult(request["task_id"], True, "owned", "resumed")

    signed.register_resume_handler(
        signed.ResumeKind.ALWAYS_ON_DYNAMIC_PERMISSION, resume,
        transition="approved_pending_resume -> always_on_owned_or_blocked",
    )
    store = signed.ApprovalStore()
    request = store.create_request(
        task_id="task-1", permission_request_id="permission-1", project="project-1",
        repository="owner/repository", action="write", scope="repository",
        requesting_user_id="user-1", requesting_chat_id="chat-1",
        resume_kind="always_on_dynamic_permission", resume_payload={}, details="write access",
    )
    query = SimpleNamespace(
        data=store.callback(request["request_id"], "a"), id="callback-1",
        from_user=SimpleNamespace(id="user-1"),
        message=SimpleNamespace(chat_id="chat-1", message_id=""),
        answer=AsyncMock(), edit_message_text=AsyncMock(), edit_message_reply_markup=AsyncMock(),
    )
    adapter = SimpleNamespace(_bot=SimpleNamespace(send_message=AsyncMock()))
    if fail_notification:
        adapter._bot.send_message.side_effect = RuntimeError("notification unavailable")
    assert await signed.handle_callback(adapter, query)
    tasks = tuple(signed._RESUME_TASKS)
    assert len(tasks) == 1
    assert await signed.handle_callback(adapter, query)
    await asyncio.gather(*tasks)
    assert calls == ["task-1"]
    persisted = store.get(request["request_id"])
    assert persisted is not None and persisted["resumed_at"] is not None
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute("SELECT state FROM tasks WHERE task_id='task-1'").fetchone()[0] == "owned"
    assert not signed._RESUME_TASKS


def test_real_profile_home_changes_isolate_persisted_requests(monkeypatch, tmp_path):
    monkeypatch.delenv("HERMES_APPROVAL_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "one"))
    first = signed.ApprovalStore()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "two"))
    second = signed.ApprovalStore()
    assert first.root == tmp_path / "one" / "approvals"
    assert second.root == tmp_path / "two" / "approvals"
    assert first.secret != second.secret


@pytest.mark.parametrize("task_id,owned", [("wrong-task", True), ("task-1", False)])
def test_store_rejects_invalid_resume_result(monkeypatch, tmp_path, task_id, owned):
    store = signed.ApprovalStore(tmp_path)
    request = store.create_request(
        task_id="task-1", permission_request_id="permission-1", project="project-1",
        repository="owner/repository", action="write", scope="repository",
        requesting_user_id="user-1", requesting_chat_id="chat-1",
        resume_kind="hermes_session", resume_payload={}, details="write access",
    )
    with pytest.raises(ValueError, match="ownership|task"):
        store.mark_resumed(request["request_id"], signed.ResumeResult(task_id, owned, "owned", "resumed"))
    persisted = store.get(request["request_id"])
    assert persisted is not None and persisted["resumed_at"] is None
