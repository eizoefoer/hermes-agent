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
