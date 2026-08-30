import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from hermes_state import SessionDB
from plugins.platforms.feishu.feishu_comment import (
    _comment_source_identity,
    _session_key,
    handle_drive_comment_event,
    parse_drive_comment_event,
    recover_feishu_comment_turns,
)
from plugins.platforms.feishu.feishu_comment_rules import ResolvedCommentRule


def _event(event_id="evt-1", reply_id="reply-1"):
    return SimpleNamespace(
        event={
            "event_id": event_id,
            "comment_id": "comment-1",
            "reply_id": reply_id,
            "timestamp": "123",
            "notice_meta": {
                "file_token": "doc-1",
                "file_type": "docx",
                "notice_type": "add_reply",
                "from_user_id": {"open_id": "user-1"},
                "to_user_id": {"open_id": "bot-1"},
            },
        }
    )


def _patch_comment_runtime():
    return (
        patch(
            "plugins.platforms.feishu.feishu_comment_rules.resolve_rule",
            return_value=ResolvedCommentRule(True, "allow", frozenset(), "top"),
        ),
        patch(
            "plugins.platforms.feishu.feishu_comment_rules.is_user_allowed",
            return_value=True,
        ),
        patch(
            "plugins.platforms.feishu.feishu_comment_rules.has_wiki_keys",
            return_value=False,
        ),
        patch("plugins.platforms.feishu.feishu_comment_rules.load_config"),
        patch(
            "plugins.platforms.feishu.feishu_comment.query_document_meta",
            new=AsyncMock(return_value={"title": "Doc", "url": "https://doc"}),
        ),
        patch(
            "plugins.platforms.feishu.feishu_comment.batch_query_comment",
            new=AsyncMock(return_value={"is_whole": False, "quote": ""}),
        ),
        patch(
            "plugins.platforms.feishu.feishu_comment.list_comment_replies",
            new=AsyncMock(
                return_value=[
                    {
                        "reply_id": "reply-1",
                        "user_id": {"open_id": "user-1"},
                        "content": {"elements": [{"text_run": {"text": "help"}}]},
                    }
                ]
            ),
        ),
        patch(
            "plugins.platforms.feishu.feishu_comment._run_comment_agent",
            return_value="done",
        ),
        patch(
            "plugins.platforms.feishu.feishu_comment.deliver_comment_reply",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "plugins.platforms.feishu.feishu_comment.add_comment_reaction",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "plugins.platforms.feishu.feishu_comment.delete_comment_reaction",
            new=AsyncMock(return_value=True),
        ),
    )


async def _run_with_runtime(coro):
    patches = _patch_comment_runtime()
    for item in patches:
        item.start()
    try:
        return await coro
    finally:
        for item in reversed(patches):
            item.stop()


def test_comment_event_uses_authoritative_occurrence_identity():
    parsed = parse_drive_comment_event(_event())
    assert _comment_source_identity(parsed) == "feishu-comment:event:evt-1"
    parsed["event_id"] = ""
    assert _comment_source_identity(parsed) == "feishu-comment:reply:reply-1"


def test_persistent_comment_turn_executes_and_closes_delivery(tmp_path: Path):
    db = SessionDB(tmp_path / "state.db")
    asyncio.run(
        _run_with_runtime(
            handle_drive_comment_event(
                object(),
                _event(),
                self_open_id="bot-1",
                _session_db=db,
                _drain_recovery=False,
            )
        )
    )
    session_id = _session_key("docx", "doc-1")
    turns = db.list_session_logical_turns(session_id)
    assert len(turns) == 1
    assert turns[0]["state"] == "completed"
    assert turns[0]["delivery_state"] == "transport_accepted"
    assert turns[0]["task_id"] is None
    assert turns[0]["goal_id"] is None
    assert db.get_session_turn_lease(session_id) is None


def test_duplicate_comment_event_does_not_execute_twice(tmp_path: Path):
    db = SessionDB(tmp_path / "state.db")
    for _ in range(2):
        asyncio.run(
            _run_with_runtime(
                handle_drive_comment_event(
                    object(), _event(), self_open_id="bot-1",
                    _session_db=db, _drain_recovery=False,
                )
            )
        )
    turns = db.list_session_logical_turns(_session_key("docx", "doc-1"))
    assert len(turns) == 1
    assert turns[0]["attempt_count"] == 1


def test_recovery_dispatches_queued_comment_turn(tmp_path: Path):
    db = SessionDB(tmp_path / "state.db")
    parsed = parse_drive_comment_event(_event())
    session_id = _session_key("docx", "doc-1")
    db.ensure_session(session_id, source="feishu_comment")
    admitted = db.admit_session_event(
        session_id=session_id,
        session_key=session_id,
        source_identity=_comment_source_identity(parsed),
        event_type="feishu-comment",
        payload={"parsed_event": parsed, "self_open_id": "bot-1"},
    )
    asyncio.run(
        _run_with_runtime(
            recover_feishu_comment_turns(
                object(), self_open_id="bot-1", session_db=db
            )
        )
    )
    recovered = db.get_logical_turn(admitted["logical_turn_id"])
    assert recovered["state"] == "completed"
    assert recovered["attempt_count"] == 1


def test_busy_comment_session_keeps_second_turn_durably_queued(tmp_path: Path):
    db = SessionDB(tmp_path / "state.db")
    session_id = _session_key("docx", "doc-1")
    db.ensure_session(session_id, source="feishu_comment")
    first = db.admit_session_event(
        session_id=session_id,
        session_key=session_id,
        source_identity="first",
        event_type="feishu-comment",
        payload={"parsed_event": parse_drive_comment_event(_event("first"))},
    )
    claim = db.claim_logical_turn(first["logical_turn_id"], owner="other", pid=1)
    assert claim["outcome"] == "claimed"
    asyncio.run(
        _run_with_runtime(
            handle_drive_comment_event(
                object(), _event("second", "reply-2"), self_open_id="bot-1",
                _session_db=db, _drain_recovery=False,
            )
        )
    )
    turns = db.list_session_logical_turns(session_id)
    assert len(turns) == 2
    assert turns[1]["state"] == "queued"
    assert turns[1]["attempt_count"] == 0
