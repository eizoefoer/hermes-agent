"""Compatibility coverage for deployed signed repository approval plugins."""

import sqlite3

import pytest

from gateway import telegram_approval


_DYNAMIC_TRANSITION = "approved_pending_resume -> always_on_owned_or_blocked"


@pytest.fixture
def registry(monkeypatch):
    from gateway import signed_telegram_approval as signed

    monkeypatch.setattr(signed, "_RESUME_REGISTRY", dict(signed._RESUME_REGISTRY))

    def handler(request):
        return signed.ResumeResult(request["task_id"], True, "always_on_owned", "resumed")

    signed.register_resume_handler(
        signed.ResumeKind.ALWAYS_ON_DYNAMIC_PERMISSION,
        handler,
        transition=_DYNAMIC_TRANSITION,
    )
    return signed, handler


def _request(store, *, ttl_seconds=3600):
    return store.create_request(
        task_id="task-1", permission_request_id="permission-1", project="project-1",
        repository="owner/repository", action="write", scope="repository",
        requesting_user_id="user-1", requesting_chat_id="chat-1",
        resume_kind="always_on_dynamic_permission",
        resume_payload={"permission_request_id": "permission-1", "actor": "worker"},
        details="write access", ttl_seconds=ttl_seconds,
    )


def test_signed_approval_api_remains_available_for_control_plane(registry, tmp_path):
    signed, _ = registry
    assert telegram_approval.ResumeKind.ALWAYS_ON_DYNAMIC_PERMISSION.value == "always_on_dynamic_permission"
    assert callable(telegram_approval.register_resume_handler)
    assert callable(telegram_approval.validate_resume_registry)
    assert callable(telegram_approval.handle_callback)
    store = telegram_approval.ApprovalStore(tmp_path)
    request = _request(store)
    callback = store.callback(request["request_id"], "a")
    parsed = store.parse_callback(callback)
    assert parsed is not None and parsed[1] == request["request_id"]
    assert store.get(request["request_id"])["requesting_user_id"] == "user-1"
    with pytest.raises(ValueError, match="matching state transition"):
        signed.register_resume_handler(
            signed.ResumeKind.ALWAYS_ON_DYNAMIC_PERMISSION,
            lambda item: signed.ResumeResult(item["task_id"], True, "bad", "bad"),
            transition="wrong transition",
        )


def test_signed_callback_fails_closed_for_tampered_wrong_user_and_scope(registry, tmp_path):
    _, _ = registry
    from gateway import signed_telegram_approval as signed

    store = signed.ApprovalStore(tmp_path)
    request = _request(store); callback = store.callback(request["request_id"], "r")
    assert store.resolve(callback_data="pa:malformed", caller_user_id="user-1", caller_chat_id="chat-1", message_id="").outcome == "invalid"
    assert store.resolve(callback_data=callback[:-1] + ("A" if callback[-1] != "A" else "B"), caller_user_id="user-1", caller_chat_id="chat-1", message_id="").outcome == "invalid"
    assert store.resolve(callback_data=callback, caller_user_id="user-2", caller_chat_id="chat-1", message_id="").outcome == "unauthorized"
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("UPDATE requests SET repository='attacker/expanded-scope' WHERE request_id=?", (request["request_id"],))
    assert store.resolve(callback_data=callback, caller_user_id="user-1", caller_chat_id="chat-1", message_id="").outcome == "invalid"
    assert not store.has_grant(project="project-1", repository="attacker/expanded-scope", task_id="task-1", action="write", scope="repository", requesting_user_id="user-1")


def test_signed_callback_expiry_fails_closed(registry, tmp_path):
    _, _ = registry
    from gateway import signed_telegram_approval as signed

    clock = [1000.0]
    store = signed.ApprovalStore(tmp_path, now=lambda: clock[0])
    request = _request(store, ttl_seconds=60); callback = store.callback(request["request_id"], "a"); clock[0] = 1061.0
    assert store.resolve(callback_data=callback, caller_user_id="user-1", caller_chat_id="chat-1", message_id="").outcome == "expired"
    assert store.get(request["request_id"])["status"] == "expired"


def test_persisted_restart_duplicate_decision_and_resume_claims(registry, tmp_path):
    _, _ = registry
    from gateway import signed_telegram_approval as signed

    store = signed.ApprovalStore(tmp_path)
    request = _request(store)
    callback = store.callback(request["request_id"], "a")
    restarted = signed.ApprovalStore(tmp_path)
    first = restarted.resolve(callback_data=callback, caller_user_id="user-1", caller_chat_id="chat-1", message_id="")
    assert first.outcome == "approved" and first.execute and first.request is not None
    duplicate = store.resolve(callback_data=callback, caller_user_id="user-1", caller_chat_id="chat-1", message_id="")
    assert duplicate.outcome == "duplicate" and not duplicate.execute
    assert restarted.claim_resume(request["request_id"])
    assert not store.claim_resume(request["request_id"])
    result = signed.resume_request(first.request)
    restarted.mark_resumed(request["request_id"], result)
    assert not store.claim_resume(request["request_id"])
    persisted = store.get(request["request_id"])
    assert persisted is not None and persisted["resumed_at"] is not None


def test_default_approval_home_tracks_profile_home_at_construction(monkeypatch, tmp_path):
    from gateway import signed_telegram_approval as signed

    monkeypatch.delenv("HERMES_APPROVAL_HOME", raising=False)
    profile_one = tmp_path / "profile-one"
    profile_two = tmp_path / "profile-two"
    monkeypatch.setattr(signed, "get_hermes_home", lambda: profile_one)
    first = signed.ApprovalStore()
    monkeypatch.setattr(signed, "get_hermes_home", lambda: profile_two)
    second = signed.ApprovalStore()
    assert first.root == profile_one / "approvals"
    assert second.root == profile_two / "approvals"
    assert first.db_path != second.db_path


def test_profile_approval_stores_are_isolated(registry, tmp_path):
    _, _ = registry
    from gateway import signed_telegram_approval as signed

    first = signed.ApprovalStore(tmp_path / "one")
    second = signed.ApprovalStore(tmp_path / "two")
    request = _request(first)
    assert first.get(request["request_id"]) is not None
    assert second.get(request["request_id"]) is None
