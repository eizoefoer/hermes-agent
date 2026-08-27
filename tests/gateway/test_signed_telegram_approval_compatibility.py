"""Compatibility coverage for deployed signed repository approval plugins."""

import sqlite3

from gateway import telegram_approval


def _request(store, *, ttl_seconds=3600):
    return store.create_request(
        task_id="task-1",
        permission_request_id="permission-1",
        project="project-1",
        repository="owner/repository",
        action="write",
        scope="repository",
        requesting_user_id="user-1",
        requesting_chat_id="chat-1",
        resume_kind="always_on_dynamic_permission",
        resume_payload={"permission_request_id": "permission-1", "actor": "worker"},
        details="write access",
        ttl_seconds=ttl_seconds,
    )


def test_signed_approval_api_remains_available_for_control_plane(monkeypatch, tmp_path):
    from gateway import signed_telegram_approval

    monkeypatch.setattr(
        signed_telegram_approval,
        "_RESUME_REGISTRY",
        dict(signed_telegram_approval._RESUME_REGISTRY),
    )
    assert telegram_approval.ResumeKind.ALWAYS_ON_DYNAMIC_PERMISSION.value == (
        "always_on_dynamic_permission"
    )
    assert callable(telegram_approval.register_resume_handler)
    assert callable(telegram_approval.validate_resume_registry)
    assert callable(telegram_approval.handle_callback)

    def resume_dynamic(request):
        return telegram_approval.ResumeResult(
            request["task_id"], True, "always_on_owned", "resumed"
        )

    telegram_approval.register_resume_handler(
        telegram_approval.ResumeKind.ALWAYS_ON_DYNAMIC_PERMISSION,
        resume_dynamic,
        transition="approved_pending_resume -> always_on_owned_or_blocked",
    )

    store = telegram_approval.ApprovalStore(tmp_path)
    request = _request(store)

    callback = store.callback(request["request_id"], "a")
    parsed = store.parse_callback(callback)
    assert parsed is not None
    assert parsed[1] == request["request_id"]
    assert store.get(request["request_id"])["requesting_user_id"] == "user-1"


def test_signed_callback_fails_closed_for_malformed_tampered_and_wrong_user(
    monkeypatch, tmp_path
):
    from gateway import signed_telegram_approval

    monkeypatch.setattr(
        signed_telegram_approval,
        "_RESUME_REGISTRY",
        dict(signed_telegram_approval._RESUME_REGISTRY),
    )
    signed_telegram_approval.register_resume_handler(
        signed_telegram_approval.ResumeKind.ALWAYS_ON_DYNAMIC_PERMISSION,
        lambda request: signed_telegram_approval.ResumeResult(
            request["task_id"], True, "always_on_owned", "resumed"
        ),
        transition="approved_pending_resume -> always_on_owned_or_blocked",
    )
    store = signed_telegram_approval.ApprovalStore(tmp_path)
    request = _request(store)
    callback = store.callback(request["request_id"], "r")

    assert store.resolve(
        callback_data="pa:malformed",
        caller_user_id="user-1",
        caller_chat_id="chat-1",
        message_id="",
    ).outcome == "invalid"
    assert store.resolve(
        callback_data=f"{callback[:-1]}{'A' if callback[-1] != 'A' else 'B'}",
        caller_user_id="user-1",
        caller_chat_id="chat-1",
        message_id="",
    ).outcome == "invalid"
    assert store.resolve(
        callback_data=callback,
        caller_user_id="user-2",
        caller_chat_id="chat-1",
        message_id="",
    ).outcome == "unauthorized"

    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE requests SET repository='attacker/expanded-scope' WHERE request_id=?",
            (request["request_id"],),
        )
    assert store.resolve(
        callback_data=callback,
        caller_user_id="user-1",
        caller_chat_id="chat-1",
        message_id="",
    ).outcome == "invalid"
    assert not store.has_grant(
        project="project-1",
        repository="attacker/expanded-scope",
        task_id="task-1",
        action="write",
        scope="repository",
        requesting_user_id="user-1",
    )


def test_signed_callback_expiry_fails_closed(monkeypatch, tmp_path):
    from gateway import signed_telegram_approval

    clock = [1000.0]
    monkeypatch.setattr(
        signed_telegram_approval,
        "_RESUME_REGISTRY",
        dict(signed_telegram_approval._RESUME_REGISTRY),
    )
    signed_telegram_approval.register_resume_handler(
        signed_telegram_approval.ResumeKind.ALWAYS_ON_DYNAMIC_PERMISSION,
        lambda request: signed_telegram_approval.ResumeResult(
            request["task_id"], True, "always_on_owned", "resumed"
        ),
        transition="approved_pending_resume -> always_on_owned_or_blocked",
    )
    store = signed_telegram_approval.ApprovalStore(tmp_path, now=lambda: clock[0])
    request = _request(store, ttl_seconds=60)
    callback = store.callback(request["request_id"], "a")
    clock[0] = 1061.0

    resolution = store.resolve(
        callback_data=callback,
        caller_user_id="user-1",
        caller_chat_id="chat-1",
        message_id="",
    )

    assert resolution.outcome == "expired"
    assert store.get(request["request_id"])["status"] == "expired"


def test_phase1_continuation_store_remains_separate(tmp_path):
    from gateway.approval_store import ApprovalStore as ContinuationApprovalStore

    continuation_store = ContinuationApprovalStore(tmp_path / "continuations.db")
    service = telegram_approval.TelegramApprovalService(
        continuation_store,
        process_local_resolver=lambda _session, _choice: 0,
    )

    request = service.create(
        request_id="phase1-request",
        session_key="session-1",
        continuation_kind="hermes_session",
        payload={"session_id": "session-1"},
        idempotency_key="phase1-occurrence-1",
    )

    assert request.request_id == "phase1-request"
    assert continuation_store.get_request(request.request_id) == request

