"""Phase 1.3 durable logical-turn admission contracts."""

from concurrent.futures import ThreadPoolExecutor

from hermes_state import SessionDB


def _db(tmp_path):
    state = SessionDB(tmp_path / "state.db")
    state.create_session("session-1", "cli", user_id="user-1")
    return state


def test_historical_resumed_session_without_lease_claims_a_normal_logical_turn(tmp_path):
    """Loading history must not manufacture busy ownership in a fresh process."""
    state = _db(tmp_path)
    state.append_message("session-1", "user", "old request")
    state.append_message("session-1", "assistant", "old response")
    assert state.get_session_turn_lease("session-1") is None

    admitted = state.admit_logical_turn(
        session_id="session-1",
        session_key="cli:session-1",
        source_identity="cli:resume:message-1",
        payload={"text": "new request", "source": "cli"},
    )
    claim = state.claim_logical_turn(
        admitted["logical_turn_id"], owner="cli:123", pid=123, ttl_seconds=30
    )
    assert state.mark_logical_turn_started(admitted["logical_turn_id"], claim["attempt_id"])

    assert admitted["state"] == "queued"
    assert claim["outcome"] == "claimed"
    assert state.get_logical_turn(admitted["logical_turn_id"])["state"] == "executing"
    assert claim["turn"]["attempt_count"] == 1
    assert state.get_session_turn_lease("session-1")["turn_id"] == claim["attempt_id"]


def test_real_durable_contention_queues_second_turn_without_second_execution(tmp_path):
    state = _db(tmp_path)
    first = state.admit_logical_turn(
        session_id="session-1", session_key="telegram:7", source_identity="update:1", payload={}
    )
    first_claim = state.claim_logical_turn(first["logical_turn_id"], owner="gateway:a", pid=1)
    second = state.admit_logical_turn(
        session_id="session-1", session_key="telegram:7", source_identity="update:2", payload={}
    )

    second_claim = state.claim_logical_turn(second["logical_turn_id"], owner="gateway:b", pid=2)

    assert first_claim["outcome"] == "claimed"
    assert second_claim["outcome"] == "busy"
    assert second_claim["lease"]["holder"].startswith("gateway:a:")
    assert state.get_logical_turn(second["logical_turn_id"])["state"] == "queued"


def test_duplicate_source_delivery_is_one_logical_turn_and_one_attempt(tmp_path):
    state = _db(tmp_path)

    first = state.admit_logical_turn(
        session_id="session-1", session_key="telegram:7", source_identity="telegram:update:99", payload={"x": 1}
    )
    replay = state.admit_logical_turn(
        session_id="session-1", session_key="telegram:7", source_identity="telegram:update:99", payload={"x": 1}
    )

    assert replay["logical_turn_id"] == first["logical_turn_id"]
    assert replay["duplicate"] is True
    assert state.count_logical_turns("session-1") == 1


def test_release_promotes_next_queued_turn_and_crashed_attempt_is_reclaimable(tmp_path):
    state = _db(tmp_path)
    first = state.admit_logical_turn(
        session_id="session-1", session_key="telegram:7", source_identity="update:1", payload={}
    )
    second = state.admit_logical_turn(
        session_id="session-1", session_key="telegram:7", source_identity="update:2", payload={}
    )
    claimed = state.claim_logical_turn(first["logical_turn_id"], owner="gateway:a", pid=1, ttl_seconds=0.01)
    assert claimed["outcome"] == "claimed"

    state.fail_logical_turn(first["logical_turn_id"], claimed["attempt_id"], "crashed", retryable=True)
    promoted = state.claim_next_logical_turn("session-1", owner="gateway:b", pid=2)

    assert promoted["outcome"] == "claimed"
    assert promoted["turn"]["logical_turn_id"] == first["logical_turn_id"]
    assert promoted["turn"]["attempt_count"] == 2


def test_concurrent_claims_have_one_winner(tmp_path):
    path = tmp_path / "state.db"
    seed = SessionDB(path)
    seed.create_session("session-1", "cli", user_id="user-1")
    turn = seed.admit_logical_turn(
        session_id="session-1", session_key="cli:session-1", source_identity="cli:1", payload={}
    )

    def claim(worker):
        db = SessionDB(path)
        return db.claim_logical_turn(turn["logical_turn_id"], owner=worker, pid=1)["outcome"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(claim, ["a", "b", "c", "d"]))

    assert outcomes.count("claimed") == 1
    assert outcomes.count("busy") == 3


def test_queued_turn_survives_restart_and_keeps_its_logical_identity(tmp_path):
    state = _db(tmp_path)
    queued = state.admit_logical_turn(
        session_id="session-1", session_key="cli:session-1",
        source_identity="cli:resume:queued", payload={"text": "continue"},
    )

    assert state.reconcile_logical_turns() == 0
    recovered = state.claim_next_logical_turn("session-1", owner="cli:replacement", pid=2)

    assert recovered["outcome"] == "claimed"
    assert recovered["turn"]["logical_turn_id"] == queued["logical_turn_id"]
    assert recovered["turn"]["attempt_count"] == 1


def test_claimed_turn_with_live_owner_is_not_replayed_on_restart(tmp_path):
    state = _db(tmp_path)
    turn = state.admit_logical_turn(
        session_id="session-1", session_key="cli:session-1", source_identity="cli:claimed", payload={},
    )
    claim = state.claim_logical_turn(turn["logical_turn_id"], owner="cli:live", pid=1)

    assert state.reconcile_logical_turns() == 0
    assert state.get_logical_turn(turn["logical_turn_id"])["state"] == "claimed"
    assert state.claim_next_logical_turn("session-1", owner="cli:replacement", pid=2)["outcome"] == "empty"
    assert claim["attempt_id"] == state.get_logical_turn(turn["logical_turn_id"])["current_attempt_id"]


def test_executing_turn_without_lease_is_requeued_with_new_attempt(tmp_path):
    state = _db(tmp_path)
    turn = state.admit_logical_turn(
        session_id="session-1", session_key="cli:session-1", source_identity="cli:crash", payload={},
    )
    first = state.claim_logical_turn(turn["logical_turn_id"], owner="cli:crashed", pid=1)
    assert state.mark_logical_turn_started(turn["logical_turn_id"], first["attempt_id"])
    state.release_session_turn_lease("session-1", first["lease"]["holder"], first["attempt_id"])

    assert state.reconcile_logical_turns() == 1
    second = state.claim_next_logical_turn("session-1", owner="cli:replacement", pid=2)

    assert second["outcome"] == "claimed"
    assert second["turn"]["logical_turn_id"] == turn["logical_turn_id"]
    assert second["attempt_id"] != first["attempt_id"]
    assert second["turn"]["attempt_count"] == 2


def test_completed_execution_retains_a_pending_delivery_obligation(tmp_path):
    state = _db(tmp_path)
    turn = state.admit_logical_turn(
        session_id="session-1", session_key="telegram:7", source_identity="telegram:update:delivery", payload={},
    )
    claim = state.claim_logical_turn(turn["logical_turn_id"], owner="gateway:a", pid=1)
    assert state.mark_logical_turn_started(turn["logical_turn_id"], claim["attempt_id"])

    completed = state.complete_logical_turn(
        turn["logical_turn_id"], claim["attempt_id"], {"response": "done"}, delivery_required=True,
    )

    assert completed["state"] == "completed"
    assert completed["delivery_state"] == "pending"
    assert state.acknowledge_logical_turn_delivery(turn["logical_turn_id"], claim["attempt_id"])["delivery_state"] == "delivered"
    assert state.get_logical_turn(turn["logical_turn_id"])["state"] == "completed"


def test_terminal_unrecoverable_turn_never_replays(tmp_path):
    state = _db(tmp_path)
    turn = state.admit_logical_turn(
        session_id="session-1", session_key="cli:session-1", source_identity="cli:unsafe", payload={},
    )
    claim = state.claim_logical_turn(turn["logical_turn_id"], owner="cli:1", pid=1)
    state.fail_logical_turn(turn["logical_turn_id"], claim["attempt_id"], "external side effect ambiguous", retryable=False)

    assert state.reconcile_logical_turns() == 0
    assert state.claim_logical_turn(turn["logical_turn_id"], owner="cli:2", pid=2)["outcome"] == "terminal"


def test_task_correlation_is_retained_across_attempts(tmp_path):
    state = _db(tmp_path)
    turn = state.admit_logical_turn(
        session_id="session-1", session_key="cli:session-1", source_identity="cli:task",
        payload={}, task_id="task-1", goal_id="goal-1", branch="feature/durable", worktree="/tmp/worktree",
    )
    first = state.claim_logical_turn(turn["logical_turn_id"], owner="cli:1", pid=1)
    state.fail_logical_turn(turn["logical_turn_id"], first["attempt_id"], "retry", retryable=True)
    second = state.claim_logical_turn(turn["logical_turn_id"], owner="cli:2", pid=2)

    retained = state.get_logical_turn(turn["logical_turn_id"])
    assert retained["session_id"] == "session-1"
    assert retained["task_id"] == "task-1"
    assert retained["goal_id"] == "goal-1"
    assert retained["branch"] == "feature/durable"
    assert retained["worktree"] == "/tmp/worktree"
    assert retained["current_attempt_id"] == second["attempt_id"]
