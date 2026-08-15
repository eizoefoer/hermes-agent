"""Durable approval continuation workers and consumer contracts."""

from __future__ import annotations

import threading
from typing import Any, Callable, Optional

from gateway.approval_store import ApprovalStore, Continuation, ExternalIntent


class ContinuationUnavailable(RuntimeError):
    """The continuation consumer is temporarily unavailable."""


class UnrecoverableContinuation(RuntimeError):
    """The continuation can never be safely completed."""


class AmbiguousExternalResult(RuntimeError):
    """An external dispatch may have succeeded and must be reconciled."""

    def __init__(self, message: str, *, external_id: Optional[str] = None) -> None:
        super().__init__(message)
        self.external_id = external_id


class GatewayContinuationRegistry:
    """Thread-safe hook surface used by a live gateway to consume durable work."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._consumers: dict[str, Callable[[Continuation], dict[str, Any]]] = {}

    def register(
        self, kind: str, consumer: Callable[[Continuation], dict[str, Any]]
    ) -> None:
        with self._lock:
            self._consumers[kind] = consumer

    def unregister(self, kind: str, consumer: Optional[Callable] = None) -> None:
        with self._lock:
            if consumer is None or self._consumers.get(kind) is consumer:
                self._consumers.pop(kind, None)

    def consume(self, item: Continuation) -> dict[str, Any]:
        with self._lock:
            consumer = self._consumers.get(item.kind)
        if consumer is None:
            raise ContinuationUnavailable("gateway continuation consumer unavailable")
        return consumer(item)


gateway_continuation_registry = GatewayContinuationRegistry()


class ContinuationWorker:
    """Lease and execute at most one continuation per ``run_once`` call."""

    def __init__(
        self,
        store: ApprovalStore,
        *,
        worker_id: str,
        registry: GatewayContinuationRegistry = gateway_continuation_registry,
        lease_seconds: float = 30,
        max_attempts: int = 5,
        base_backoff: float = 5,
        max_backoff: float = 300,
    ) -> None:
        self.store = store
        self.worker_id = worker_id
        self.registry = registry
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.base_backoff = base_backoff
        self.max_backoff = max_backoff
        self._consumers: dict[str, Callable[[Continuation], dict[str, Any]]] = {}

    def register(
        self, kind: str, consumer: Callable[[Continuation], dict[str, Any]]
    ) -> None:
        self._consumers[kind] = consumer

    def run_once(self) -> Optional[Continuation]:
        item = self.store.claim_next(self.worker_id, lease_seconds=self.lease_seconds)
        if item is None:
            return None
        consumer = self._consumers.get(item.kind)
        try:
            # hermes_session deliberately goes through the gateway hook.  It
            # must never call tools.approval.resolve_gateway_approval here:
            # that queue belongs to whichever process happened to request it.
            result = consumer(item) if consumer else self.registry.consume(item)
        except AmbiguousExternalResult as exc:
            return self.store.mark_reconciling(
                item.id, self.worker_id, error=str(exc), external_id=exc.external_id
            )
        except ContinuationUnavailable as exc:
            return self.store.fail(
                item.id,
                self.worker_id,
                str(exc),
                retryable=True,
                max_attempts=self.max_attempts,
                base_backoff=self.base_backoff,
                max_backoff=self.max_backoff,
            )
        except UnrecoverableContinuation as exc:
            return self.store.fail(
                item.id,
                self.worker_id,
                str(exc),
                retryable=False,
                max_attempts=self.max_attempts,
            )
        except Exception as exc:
            return self.store.fail(
                item.id,
                self.worker_id,
                str(exc),
                retryable=True,
                max_attempts=self.max_attempts,
                base_backoff=self.base_backoff,
                max_backoff=self.max_backoff,
            )
        return self.store.complete(item.id, self.worker_id, result)


class GitHubWorkflowConsumer:
    """Dispatch GitHub work only behind a persisted intent, then reconcile ambiguity."""

    def __init__(
        self,
        store: ApprovalStore,
        *,
        dispatch: Callable[[ExternalIntent], dict[str, Any]],
        reconcile: Callable[[ExternalIntent], dict[str, Any]],
    ) -> None:
        self.store = store
        self.dispatch = dispatch
        self.reconcile = reconcile

    def consume(self, continuation: Continuation) -> dict[str, Any]:
        intent = self.store.get_or_create_external_intent(continuation)
        if intent.state in {"dispatching", "ambiguous"}:
            raise AmbiguousExternalResult(
                intent.last_error or "external result remains ambiguous",
                external_id=intent.external_id,
            )
        if intent.state == "succeeded":
            return intent.result or {}
        if intent.state != "prepared":
            raise UnrecoverableContinuation(
                f"invalid GitHub intent state: {intent.state}"
            )
        # The idempotency key is part of the durable intent before this call.
        # An ambiguous exception transitions to reconciliation; it is never
        # interpreted as permission to blindly dispatch again.
        if not self.store.begin_external_dispatch(intent.continuation_id):
            current = self.store.get_external_intent(intent.continuation_id)
            raise AmbiguousExternalResult(
                (current.last_error if current else None)
                or "GitHub dispatch ownership became ambiguous",
                external_id=current.external_id if current else None,
            )
        result = self.dispatch(intent)
        self.store.record_external_success(intent.continuation_id, result)
        return result

    def reconcile_once(self, _worker_id: str) -> Optional[Continuation]:
        intent = self.store.next_ambiguous_intent()
        if intent is None:
            return None
        result = self.reconcile(intent)
        status = result.get("status")
        if status == "completed":
            return self.store.complete_reconciliation(intent.continuation_id, result)
        if status in {"failed", "not_found"}:
            raise UnrecoverableContinuation(
                f"GitHub reconciliation returned terminal status: {status}"
            )
        raise ContinuationUnavailable("GitHub reconciliation is still pending")
