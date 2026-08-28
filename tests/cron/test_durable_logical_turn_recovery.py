from __future__ import annotations

import time

import cron.scheduler as scheduler
from hermes_state import SessionDB as RealSessionDB


def _admit_cron_turn(db_path, *, execution_id="exec-1"):
    db = RealSessionDB(db_path)
    session_id = f"cron_job-1_{execution_id}"
    db.ensure_session(session_id, source="cron")
    turn = db.admit_session_event(
        session_id=session_id,
        session_key=session_id,
        source_identity=f"cron-execution:{execution_id}",
        event_type="cron-execution",
        payload={
            "job_id": "job-1",
            "execution_id": execution_id,
            "prompt": "do work",
            "recovery_policy": "manual_reconcile",
            "job_snapshot": {
                "id": "job-1",
                "name": "Job One",
                "prompt": "do work",
                "deliver": "local",
            },
        },
        task_id="job-1",
    )
    db.close()
    return turn["logical_turn_id"]


def test_recovery_drains_queued_cron_turn_through_same_logical_identity(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "state.db"
    logical_turn_id = _admit_cron_turn(db_path)
    monkeypatch.setattr("hermes_state.SessionDB", lambda: RealSessionDB(db_path))

    executions = []

    def fake_run_job(job):
        db = RealSessionDB(db_path)
        turn = db.admit_session_event(
            session_id="cron_job-1_exec-1",
            session_key="cron_job-1_exec-1",
            source_identity="cron-execution:exec-1",
            event_type="cron-execution",
            payload={},
            task_id="job-1",
        )
        assert turn["logical_turn_id"] == logical_turn_id
        claim = db.claim_logical_turn(
            logical_turn_id, owner="cron:test", pid=123, ttl_seconds=30
        )
        assert claim["outcome"] == "claimed"
        assert db.mark_logical_turn_started(logical_turn_id, claim["attempt_id"])
        db.complete_logical_turn(
            logical_turn_id,
            claim["attempt_id"],
            {"success": True, "output": "output", "final_response": ""},
            delivery_required=False,
        )
        db.close()
        executions.append(job["execution_id"])
        return True, "output", "", None

    monkeypatch.setattr(scheduler, "run_job", fake_run_job)
    monkeypatch.setattr(scheduler, "save_job_output", lambda *_args: "/tmp/out")

    assert scheduler.recover_cron_logical_turns(limit=2) == 1
    assert executions == ["exec-1"]

    db = RealSessionDB(db_path)
    recovered = db.get_logical_turn(logical_turn_id)
    assert recovered["state"] == "completed"
    assert recovered["task_id"] == "job-1"
    assert recovered["attempt_count"] == 1
    assert db.get_session_turn_lease("cron_job-1_exec-1") is None
    db.close()

    # A later drain cannot reopen terminal model execution.
    assert scheduler.recover_cron_logical_turns(limit=2) == 0
    assert executions == ["exec-1"]


def test_recovery_reclaims_claimed_turn_only_after_lease_expiry(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    logical_turn_id = _admit_cron_turn(db_path)
    db = RealSessionDB(db_path)
    first = db.claim_logical_turn(
        logical_turn_id, owner="cron:dead", pid=999999, ttl_seconds=0.1
    )
    assert first["outcome"] == "claimed"
    db.close()
    monkeypatch.setattr("hermes_state.SessionDB", lambda: RealSessionDB(db_path))

    calls = []

    def fake_run_job(job):
        db2 = RealSessionDB(db_path)
        claim = db2.claim_logical_turn(
            logical_turn_id, owner="cron:replacement", pid=321, ttl_seconds=30
        )
        assert claim["outcome"] == "claimed"
        assert claim["attempt_id"] != first["attempt_id"]
        db2.mark_logical_turn_started(logical_turn_id, claim["attempt_id"])
        db2.complete_logical_turn(
            logical_turn_id,
            claim["attempt_id"],
            {"success": True, "output": "ok", "final_response": ""},
        )
        db2.close()
        calls.append(claim["attempt_id"])
        return True, "ok", "", None

    monkeypatch.setattr(scheduler, "run_job", fake_run_job)
    monkeypatch.setattr(scheduler, "save_job_output", lambda *_args: "/tmp/out")

    assert scheduler.recover_cron_logical_turns(limit=2) == 0
    time.sleep(0.12)
    assert scheduler.recover_cron_logical_turns(limit=2) == 1
    assert len(calls) == 1

    db = RealSessionDB(db_path)
    turn = db.get_logical_turn(logical_turn_id)
    assert turn["attempt_count"] == 2
    assert turn["state"] == "completed"
    db.close()


def test_delivery_recovery_never_reruns_completed_cron_execution(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "state.db"
    logical_turn_id = _admit_cron_turn(db_path)
    db = RealSessionDB(db_path)
    claim = db.claim_logical_turn(logical_turn_id, owner="cron:test", pid=123)
    db.mark_logical_turn_started(logical_turn_id, claim["attempt_id"])
    db.complete_logical_turn(
        logical_turn_id,
        claim["attempt_id"],
        {"success": True, "output": "doc", "final_response": "reply"},
        delivery_required=True,
    )
    db.close()

    monkeypatch.setattr("hermes_state.SessionDB", lambda: RealSessionDB(db_path))
    monkeypatch.setattr(
        scheduler,
        "run_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("completed reasoning must not rerun")
        ),
    )
    delivered = []
    monkeypatch.setattr(
        scheduler,
        "_deliver_result",
        lambda job, content, **_kwargs: delivered.append((job["id"], content)),
    )

    assert scheduler.recover_cron_logical_turns(limit=2) == 1
    assert delivered == [("job-1", "reply")]
    db = RealSessionDB(db_path)
    turn = db.get_logical_turn(logical_turn_id)
    assert turn["state"] == "completed"
    assert turn["delivery_state"] == "transport_accepted"
    db.close()

