"""CLI task creation preserves an explicit inherited chat origin, never guesses one."""
import json

import pytest

from gateway.session_context import reset_session_vars, set_session_vars
from hermes_cli import kanban as kc
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_notify as kbn


@pytest.fixture(autouse=True)
def isolated_origin(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "board.db"))
    reset_session_vars()
    for key in ("HERMES_SESSION_PLATFORM", "HERMES_SESSION_CHAT_ID", "HERMES_SESSION_KEY", "HERMES_KANBAN_TASK"):
        monkeypatch.delenv(key, raising=False)
    yield
    reset_session_vars()


def test_cli_create_subscribes_explicit_chat_origin_idempotently():
    set_session_vars(platform="telegram", chat_id="origin", chat_type="dm", thread_id="42",
                     message_id="88", profile="default", session_id="origin-session")
    first = json.loads(kc.run_slash('create "background work" --assignee worker --idempotency-key origin-test --json'))
    second = json.loads(kc.run_slash('create "background work" --assignee worker --idempotency-key origin-test --json'))
    assert first["id"] == second["id"]
    with kbc.connect_closing() as conn:
        subs = kbn.list_notify_subs(conn, first["id"])
    assert len(subs) == 1
    assert subs[0]["chat_id"] == "origin"
    assert subs[0]["thread_id"] == "42"
    assert subs[0]["notifier_profile"] == "default"
    assert subs[0]["delivery_metadata"]["telegram_reply_to_message_id"] == "88"


def test_cli_without_origin_does_not_invent_recipient(monkeypatch):
    monkeypatch.setenv("HERMES_SESSION_ID", "local-only")
    task = json.loads(kc.run_slash('create "local work" --assignee worker --json'))
    with kbc.connect_closing() as conn:
        assert kbn.list_notify_subs(conn, task["id"]) == []
