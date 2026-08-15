"""Telegram approval persistence and compatibility dispatch.

The service is transport-independent so Telegram callbacks can persist a
decision before choosing how its continuation is consumed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from gateway.approval_store import ApprovalRequest, ApprovalStore, Continuation


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
        store: ApprovalStore,
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
        continuation = self.store.decide(request_id, choice, decided_by=decided_by)
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
