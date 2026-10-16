"""Durable signed approvals must reach the modern Telegram callback ingress."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway import signed_telegram_approval as signed
from gateway.config import PlatformConfig
from plugins.platforms.telegram.adapter import TelegramAdapter


def make_request(store, kind="always_on_dynamic_permission"):
    return store.create_request(
        task_id="task-1", permission_request_id="permission-1", project="project-1",
        repository="owner/repository", action="write", scope="repository",
        requesting_user_id="12345", requesting_chat_id="12345",
        resume_kind=kind, resume_payload={}, details="write access",
    )


def make_query(store, request, user_id="12345"):
    return SimpleNamespace(
        data=store.callback(request["request_id"], "a"), id="callback-1",
        from_user=SimpleNamespace(id=user_id, first_name="Owner"),
        message=SimpleNamespace(chat_id="12345", message_id="", chat=SimpleNamespace(type="private")),
        answer=AsyncMock(), edit_message_text=AsyncMock(), edit_message_reply_markup=AsyncMock(),
    )


def make_adapter():
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="test-token"))
    adapter._bot = SimpleNamespace(send_message=AsyncMock())
    adapter._accept_update = MagicMock()
    return adapter


@pytest.mark.asyncio
async def test_modern_ingress_resumes_signed_request_once(monkeypatch, tmp_path):
    monkeypatch.delenv("HERMES_APPROVAL_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "12345")
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
    request = make_request(store)
    adapter = make_adapter()
    query = make_query(store, request)
    update = SimpleNamespace(callback_query=query)
    await adapter._handle_callback_query(update, SimpleNamespace())
    tasks = tuple(signed._RESUME_TASKS)
    assert tasks, "signed callback was not admitted through modern Telegram ingress"
    await asyncio.gather(*tasks)
    await adapter._handle_callback_query(update, SimpleNamespace())
    assert calls == ["task-1"]
    assert store.get(request["request_id"])["resumed_at"] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("rejection", ["allowlist", "request-owner", "profile"])
async def test_modern_ingress_rejects_without_consuming_signed_decision(monkeypatch, tmp_path, rejection):
    monkeypatch.delenv("HERMES_APPROVAL_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "owner"))
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "12345" if rejection == "allowlist" else "*")
    store = signed.ApprovalStore()
    request = make_request(store, "hermes_session")
    query = make_query(store, request, "99999" if rejection != "profile" else "12345")
    if rejection == "profile":
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "other"))
    adapter = make_adapter()
    await adapter._handle_callback_query(SimpleNamespace(callback_query=query), SimpleNamespace())
    query.answer.assert_awaited()
    assert store.get(request["request_id"])["status"] == "pending"
    assert not signed._RESUME_TASKS
