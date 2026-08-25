"""Tests for Matrix adapter fail-closed approval reaction auth.

When MATRIX_ALLOWED_USERS is not configured, _on_reaction must deny
approval reactions by default unless GATEWAY_ALLOW_ALL_USERS=true.
Mirrors the Telegram _is_callback_user_authorized fix (commit 89d32052e,
PR #28494).
"""

import asyncio
import importlib
import sys
import types
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# Stub mautrix so gateway.platforms.matrix can be imported without the SDK.
# ---------------------------------------------------------------------------

def _fake_mautrix_modules():
    modules = {
        name: types.ModuleType(name)
        for name in (
            "mautrix",
            "mautrix.types",
            "mautrix.client",
            "mautrix.client.api",
            "mautrix.errors",
            "mautrix.crypto",
            "mautrix.util",
            "mautrix.util.config",
        )
    }
    m = modules["mautrix.types"]
    for attr in (
        "ContentURI", "EventID", "EventType", "PaginationDirection",
        "PresenceState", "RoomCreatePreset", "RoomID", "SyncToken",
        "TrustState", "UserID",
    ):
        setattr(m, attr, str)
    return modules


@pytest.fixture
def matrix_module(monkeypatch):
    """Import the adapter against isolated mautrix stubs and restore caches."""
    platforms = importlib.import_module("gateway.platforms")
    missing = object()
    previous_attribute = getattr(platforms, "matrix", missing)
    previous_module = sys.modules.get("gateway.platforms.matrix", missing)

    with monkeypatch.context() as isolated:
        for name, module in _fake_mautrix_modules().items():
            isolated.setitem(sys.modules, name, module)
        sys.modules.pop("gateway.platforms.matrix", None)
        try:
            imported = importlib.import_module("gateway.platforms.matrix")
            yield imported
        finally:
            if previous_module is missing:
                sys.modules.pop("gateway.platforms.matrix", None)
            else:
                sys.modules["gateway.platforms.matrix"] = previous_module
            if previous_attribute is missing:
                if hasattr(platforms, "matrix"):
                    delattr(platforms, "matrix")
            else:
                setattr(platforms, "matrix", previous_attribute)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_adapter(matrix_module, allowed_user_ids=None):
    """Construct a MatrixAdapter with only the state needed by _on_reaction."""
    adapter = object.__new__(matrix_module.MatrixAdapter)
    adapter._user_id = "@bot:matrix.org"
    adapter._allowed_user_ids = set(allowed_user_ids) if allowed_user_ids else set()
    adapter._approval_reaction_map = {"✅": "once", "❎": "deny"}
    adapter._approval_prompts_by_event = {}
    adapter._approval_prompt_by_session = {}
    adapter._processed_events = deque(maxlen=512)
    adapter._processed_events_set = set()
    return adapter


def _make_event(sender, reacts_to, key="✅"):
    """Minimal Matrix reaction event."""
    return SimpleNamespace(
        sender=sender,
        event_id=f"$reaction-{sender.split(':')[0]}",
        room_id="!testroom:matrix.org",
        content={"m.relates_to": {"event_id": reacts_to, "key": key}},
    )


def _make_prompt(matrix_module, chat_id="!testroom:matrix.org"):
    return matrix_module._MatrixApprovalPrompt(
        session_key="session-abc",
        chat_id=chat_id,
        message_id="$prompt-event-1",
    )


def _run(matrix_module, adapter, event):
    """Run _on_reaction and return whether the prompt was resolved."""
    prompt_event_id = "$prompt-event-1"
    prompt = _make_prompt(matrix_module)
    adapter._approval_prompts_by_event[prompt_event_id] = prompt
    adapter._redact_bot_approval_reactions = AsyncMock()

    fake_approval = types.ModuleType("tools.approval")
    fake_approval.resolve_gateway_approval = lambda session_key, choice: 1
    with patch.dict(sys.modules, {"tools.approval": fake_approval}):
        asyncio.run(adapter._on_reaction(event))

    return prompt.resolved


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestApprovalReactionFailClosed:
    """_on_reaction approval auth must be fail-closed (parity with Telegram)."""

    def test_no_allowlist_no_allow_all_denies(self, monkeypatch, matrix_module):
        """No MATRIX_ALLOWED_USERS + no GATEWAY_ALLOW_ALL_USERS → deny."""
        monkeypatch.delenv("MATRIX_ALLOWED_USERS", raising=False)
        monkeypatch.delenv("GATEWAY_ALLOW_ALL_USERS", raising=False)
        adapter = _make_adapter(matrix_module, allowed_user_ids=None)
        event = _make_event("@stranger:matrix.org", "$prompt-event-1")
        assert _run(matrix_module, adapter, event) is False

    def test_no_allowlist_allow_all_permits(self, monkeypatch, matrix_module):
        """No MATRIX_ALLOWED_USERS + GATEWAY_ALLOW_ALL_USERS=true → allow."""
        monkeypatch.delenv("MATRIX_ALLOWED_USERS", raising=False)
        monkeypatch.setenv("GATEWAY_ALLOW_ALL_USERS", "true")
        adapter = _make_adapter(matrix_module, allowed_user_ids=None)
        event = _make_event("@anyone:matrix.org", "$prompt-event-1")
        assert _run(matrix_module, adapter, event) is True

    def test_listed_sender_permits(self, monkeypatch, matrix_module):
        """Sender in MATRIX_ALLOWED_USERS → allow."""
        monkeypatch.delenv("GATEWAY_ALLOW_ALL_USERS", raising=False)
        adapter = _make_adapter(matrix_module, allowed_user_ids=["@alice:matrix.org"])
        event = _make_event("@alice:matrix.org", "$prompt-event-1")
        assert _run(matrix_module, adapter, event) is True

    def test_unlisted_sender_denies(self, monkeypatch, matrix_module):
        """Sender not in MATRIX_ALLOWED_USERS → deny."""
        monkeypatch.delenv("GATEWAY_ALLOW_ALL_USERS", raising=False)
        adapter = _make_adapter(matrix_module, allowed_user_ids=["@alice:matrix.org"])
        event = _make_event("@mallory:matrix.org", "$prompt-event-1")
        assert _run(matrix_module, adapter, event) is False
