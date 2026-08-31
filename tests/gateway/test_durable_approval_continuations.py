"""Phase 1.2 durable approval continuation contracts."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from gateway.approval_store import ApprovalStore
from gateway.approval_continuation import (
    AmbiguousExternalResult,
    ContinuationWorker,
    GatewayContinuationRegistry,
    GitHubWorkflowConsumer,
    UnrecoverableContinuation,
)
from gateway.telegram_approval import TelegramApprovalService


class Clock:
    def __init__(self, value: float = 1_000.0):
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _decided(
    store: ApprovalStore, *, key: str = "turn-1", kind: str = "hermes_session"
):
    store.create_request(
        request_id=f"approval-{key}",
        session_key="telegram:chat:7",
        continuation_kind=kind,
        payload={"session_id": "session-7", "prompt": "continue"},
        idempotency_key=key,
    )
    return store.decide(f"approval-{key}", "once", decided_by="user-1")


def test_decision_and_continuation_survive_reopen(tmp_path):
    path = tmp_path / "approvals.db"
    first = ApprovalStore(path)
    continuation = _decided(first)
    first.close()

    reopened = ApprovalStore(path)
    approval = reopened.get_request("approval-turn-1")
    persisted = reopened.get_continuation(continuation.id)

    assert approval.decision == "once"
    assert persisted.state == "pending"
    assert persisted.payload["session_id"] == "session-7"


def test_concurrent_claim_has_one_winner(tmp_path):
    path = tmp_path / "approvals.db"
    seed = ApprovalStore(path)
    continuation = _decided(seed)
    seed.close()

    def claim(worker: str):
        store = ApprovalStore(path)
        try:
            item = store.claim_next(worker, lease_seconds=30)
            return item.id if item else None
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        claimed = list(pool.map(claim, [f"worker-{n}" for n in range(8)]))

    assert claimed.count(continuation.id) == 1
    assert claimed.count(None) == 7


def test_completed_continuation_replay_is_idempotent(tmp_path):
    store = ApprovalStore(tmp_path / "approvals.db")
    continuation = _decided(store)
    claimed = store.claim_next("worker-1")

    first = store.complete(claimed.id, "worker-1", {"turn_id": "turn-9"})
    replay = store.complete(claimed.id, "worker-1", {"turn_id": "turn-other"})

    assert first.state == "completed"
    assert replay.state == "completed"
    assert replay.result == {"turn_id": "turn-9"}
    assert store.claim_next("worker-2") is None


def test_retry_backoff_is_bounded_and_attempts_are_finite(tmp_path):
    clock = Clock()
    store = ApprovalStore(tmp_path / "approvals.db", clock=clock)
    continuation = _decided(store)

    expected_delays = [5, 10, 12]
    for attempt, delay in enumerate(expected_delays, start=1):
        claimed = store.claim_next("worker")
        assert claimed.id == continuation.id
        failed = store.fail(
            claimed.id,
            "worker",
            "temporarily unavailable",
            retryable=True,
            max_attempts=4,
            base_backoff=5,
            max_backoff=12,
        )
        assert failed.state == "retry"
        assert failed.attempts == attempt
        assert failed.next_attempt_at == clock.value + delay
        assert store.claim_next("early") is None
        clock.advance(delay)

    claimed = store.claim_next("worker")
    terminal = store.fail(
        claimed.id,
        "worker",
        "still unavailable",
        retryable=True,
        max_attempts=4,
        base_backoff=5,
        max_backoff=12,
    )
    assert terminal.state == "terminal"
    clock.advance(60)
    assert store.claim_next("worker") is None


def test_unrecoverable_failure_becomes_terminal_immediately(tmp_path):
    store = ApprovalStore(tmp_path / "approvals.db")
    _decided(store)
    worker = ContinuationWorker(store, worker_id="worker")
    worker.register(
        "hermes_session",
        lambda item: (_ for _ in ()).throw(
            UnrecoverableContinuation("session was deleted")
        ),
    )

    result = worker.run_once()

    assert result.state == "terminal"
    assert result.last_error == "session was deleted"


def test_unavailable_gateway_retries_then_succeeds_once(tmp_path):
    clock = Clock()
    store = ApprovalStore(tmp_path / "approvals.db", clock=clock)
    continuation = _decided(store)
    registry = GatewayContinuationRegistry()
    worker = ContinuationWorker(
        store,
        worker_id="worker",
        base_backoff=3,
        registry=registry,
    )

    unavailable = worker.run_once()
    assert unavailable.state == "retry"
    assert unavailable.last_error == "gateway continuation consumer unavailable"

    consumed = []
    registry.register(
        "hermes_session", lambda item: consumed.append(item.id) or {"turn_id": "t-1"}
    )
    clock.advance(3)
    completed = worker.run_once()

    assert completed.state == "completed"
    assert consumed == [continuation.id]


def test_duplicate_decision_creates_one_logical_continuation(tmp_path):
    store = ApprovalStore(tmp_path / "approvals.db")
    first = _decided(store, key="stable-key")
    replay = store.decide("approval-stable-key", "once", decided_by="user-1")
    duplicate = store.create_request(
        request_id="approval-replayed-delivery",
        session_key="telegram:chat:7",
        continuation_kind="hermes_session",
        payload={"session_id": "session-7", "prompt": "continue"},
        idempotency_key="stable-key",
    )
    duplicate_decision = store.decide(duplicate.request_id, "once", decided_by="user-1")

    assert replay.id == first.id
    assert duplicate_decision.id == first.id
    assert store.count_continuations() == 1


def test_ambiguous_github_result_reconciles_without_redispatch(tmp_path):
    clock = Clock()
    store = ApprovalStore(tmp_path / "approvals.db", clock=clock)
    continuation = _decided(store, key="github-run-22", kind="github_workflow")
    dispatches = []
    reconciles = []

    def dispatch(intent):
        dispatches.append(intent.idempotency_key)
        raise AmbiguousExternalResult(
            "connection dropped after dispatch", external_id="run-22"
        )

    def reconcile(intent):
        reconciles.append(intent.external_id)
        return {"status": "completed", "run_id": intent.external_id}

    github = GitHubWorkflowConsumer(store, dispatch=dispatch, reconcile=reconcile)
    worker = ContinuationWorker(store, worker_id="worker")
    worker.register("github_workflow", github.consume)

    ambiguous = worker.run_once()
    assert ambiguous.state == "reconciling"
    assert dispatches == ["github-run-22"]

    completed = github.reconcile_once("reconciler")
    assert completed.state == "completed"
    assert completed.id == continuation.id
    assert dispatches == ["github-run-22"]
    assert reconciles == ["run-22"]


def test_github_dispatching_intent_after_worker_loss_requires_reconciliation(tmp_path):
    clock = Clock()
    store = ApprovalStore(tmp_path / "approvals.db", clock=clock)
    continuation = _decided(store, key="github-crash", kind="github_workflow")
    abandoned = store.claim_next("dead-worker", lease_seconds=10)
    intent = store.get_or_create_external_intent(abandoned)
    assert store.begin_external_dispatch(intent.continuation_id) is True

    clock.advance(11)
    dispatches = []
    github = GitHubWorkflowConsumer(
        store,
        dispatch=lambda intent: dispatches.append(intent.idempotency_key) or {},
        reconcile=lambda intent: {"status": "completed", "run_id": "recovered"},
    )
    worker = ContinuationWorker(store, worker_id="replacement")
    worker.register("github_workflow", github.consume)

    ambiguous = worker.run_once()

    assert ambiguous.id == continuation.id
    assert ambiguous.state == "reconciling"
    assert dispatches == []
    assert github.reconcile_once("reconciler").state == "completed"


def test_telegram_hermes_session_decision_never_resolves_process_local_queue(tmp_path):
    store = ApprovalStore(tmp_path / "approvals.db")
    local_resolutions = []
    service = TelegramApprovalService(
        store,
        process_local_resolver=lambda *args: local_resolutions.append(args) or 1,
    )
    service.create(
        request_id="telegram-callback-1",
        session_key="telegram:chat:7",
        continuation_kind="hermes_session",
        payload={"session_id": "session-7", "prompt": "continue"},
        idempotency_key="turn-7",
    )

    outcome = service.decide("telegram-callback-1", "once", decided_by="user-1")

    assert outcome.durable is True
    assert outcome.local_resolution_count == 0
    assert outcome.continuation.state == "pending"
    assert local_resolutions == []


def test_telegram_approval_is_bound_to_initiating_user(tmp_path):
    store = ApprovalStore(tmp_path / "approvals.db")
    service = TelegramApprovalService(
        store,
        process_local_resolver=lambda *_args: 0,
    )
    service.create(
        request_id="telegram-identity-bound",
        session_key="telegram:chat:7",
        continuation_kind="hermes_session",
        payload={"session_id": "session-7", "approval_user_id": "user-1"},
        idempotency_key="identity-turn-7",
    )

    with pytest.raises(PermissionError, match="another Telegram user"):
        service.decide("telegram-identity-bound", "once", decided_by="user-2")

    assert store.get_request("telegram-identity-bound").decision is None


def test_telegram_compatibility_fast_path_is_logically_once(tmp_path):
    store = ApprovalStore(tmp_path / "approvals.db")
    local_resolutions = []
    service = TelegramApprovalService(
        store,
        process_local_resolver=lambda *args: local_resolutions.append(args) or 1,
    )
    service.create(
        request_id="telegram-callback-legacy",
        session_key="telegram:chat:legacy",
        continuation_kind="process_local_gateway",
        payload={},
        idempotency_key="legacy-turn",
    )

    first = service.decide("telegram-callback-legacy", "once", decided_by="user-1")
    replay = service.decide("telegram-callback-legacy", "once", decided_by="user-1")

    assert first.local_resolution_count == 1
    assert replay.local_resolution_count == 0
    assert local_resolutions == [("telegram:chat:legacy", "once")]
    assert replay.continuation.state == "completed"
