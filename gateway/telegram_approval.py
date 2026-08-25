"""Telegram approval persistence and compatibility dispatch.

The service is transport-independent so Telegram callbacks can persist a
decision before choosing how its continuation is consumed.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import threading
from typing import Any, Callable

from gateway.approval_store import (
    ApprovalRequest,
    ApprovalStore as ContinuationApprovalStore,
    Continuation,
)
from gateway.signed_telegram_approval import (
    ApprovalStore,
    ResumeKind,
    ResumeRegistration,
    ResumeResult,
    UnknownResumeKindError,
    _RESUME_REGISTRY,
    handle_callback,
    primary_keyboard,
    register_resume_handler,
    resume_request,
    scope_keyboard,
    validate_resume_registry,
)


HERMES_SESSION = "hermes_session"
PROCESS_LOCAL_GATEWAY = "process_local_gateway"


@dataclass(frozen=True)
class TelegramApprovalOutcome:
    continuation: Continuation
    durable: bool
    local_resolution_count: int


class TelegramApprovalService:
    """Atomically turn Telegram decisions into durable continuations."""

    def __init__(
        self,
        store: ContinuationApprovalStore,
        *,
        process_local_resolver: Callable[[str, str], int],
    ) -> None:
        self.store = store
        self._process_local_resolver = process_local_resolver

    def create(
        self,
        *,
        request_id: str,
        session_key: str,
        continuation_kind: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> ApprovalRequest:
        return self.store.create_request(
            request_id=request_id,
            session_key=session_key,
            continuation_kind=continuation_kind,
            payload=payload,
            idempotency_key=idempotency_key,
        )

    def decide(
        self, request_id: str, choice: str, *, decided_by: str
    ) -> TelegramApprovalOutcome:
        request = self.store.get_request(request_id)
        if request is None:
            raise KeyError(request_id)
        local_owner = None
        if (
            request.continuation_kind == HERMES_SESSION
            and request.payload.get("process_local_fast_path") is True
        ):
            local_owner = f"telegram-fast:{os.getpid()}:{threading.get_ident()}"
        continuation = self.store.decide(
            request_id,
            choice,
            decided_by=decided_by,
            lease_owner=local_owner,
        )
        if continuation.kind == HERMES_SESSION and local_owner:
            # The decision transaction leases the row to this callback before
            # a poller can see it.  Resume the still-blocked agent when it is
            # process-local; otherwise release the row for restart recovery.
            count = 0
            if continuation.lease_owner == local_owner:
                try:
                    count = self._process_local_resolver(
                        continuation.session_key, continuation.decision
                    )
                except Exception:
                    self.store.release_lease(continuation.id, local_owner)
                    raise
                if count:
                    continuation = self.store.complete(
                        continuation.id,
                        local_owner,
                        {"process_local_fast_path": True},
                    )
                    return TelegramApprovalOutcome(continuation, False, count)
                continuation = self.store.release_lease(
                    continuation.id, local_owner
                )
            return TelegramApprovalOutcome(continuation, True, 0)
        if continuation.kind != PROCESS_LOCAL_GATEWAY:
            # The worker will feed this to the registered gateway consumer.
            # Resolving tools.approval here would target a process-local queue
            # and is the Phase 1.2 split-process bug.
            return TelegramApprovalOutcome(continuation, True, 0)

        won, continuation = self.store.consume_for_process_local_compatibility(
            continuation.id
        )
        count = 0
        if won:
            count = self._process_local_resolver(
                continuation.session_key, continuation.decision
            )
        return TelegramApprovalOutcome(continuation, False, count)
