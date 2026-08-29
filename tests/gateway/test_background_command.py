"""Tests for /background gateway slash command.

Tests the _handle_background_command handler (run a prompt in a separate
background session) across gateway messenger platforms.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent, SendResult
from gateway.session import SessionSource
from hermes_state import AsyncSessionDB, SessionDB


def _make_event(text="/background", platform=Platform.TELEGRAM,
                user_id="12345", chat_id="67890"):
    """Build a MessageEvent for testing."""
    source = SessionSource(
        platform=platform,
        user_id=user_id,
        chat_id=chat_id,
        user_name="testuser",
    )
    return MessageEvent(text=text, source=source)


def _make_runner():
    """Create a bare GatewayRunner with minimal mocks."""
    from gateway.run import GatewayRunner
    runner = object.__new__(GatewayRunner)
    runner.adapters = {}
    runner._voice_mode = {}
    runner._session_db = None
    runner._reasoning_config = None
    runner._provider_routing = {}
    runner._fallback_model = None
    runner._running_agents = {}
    runner._background_tasks = set()

    mock_store = MagicMock()
    # A real SessionStore returns None when no persisted /model override exists.
    # MagicMock's default truthy return would otherwise rehydrate a fake model
    # and make the session-scoped reasoning resolver receive a MagicMock.
    mock_store.get_model_override.return_value = None
    runner.session_store = mock_store

    from gateway.hooks import HookRegistry
    runner.hooks = HookRegistry()

    return runner


def _install_state(runner, tmp_path):
    db = SessionDB(tmp_path / "state.db")
    runner._session_db = AsyncSessionDB(db)
    runner._gateway_background_turn_dispatching = {}
    return db


def _seed_background_turn(db, source, task_id="bg_test"):
    db.create_session(task_id, "telegram", user_id=source.user_id)
    return db.admit_session_event(
        session_id=task_id,
        session_key=f"agent:background:{task_id}",
        source_identity=f"test:{task_id}",
        event_type="gateway-background-child",
        payload={
            "event_type": "gateway-background-child",
            "recovery_policy": "manual_reconcile",
        },
        task_id=task_id,
    )


# ---------------------------------------------------------------------------
# _handle_background_command
# ---------------------------------------------------------------------------


class TestHandleBackgroundCommand:
    """Tests for GatewayRunner._handle_background_command."""

    @pytest.mark.asyncio
    async def test_no_prompt_shows_usage(self):
        """Running /background with no prompt shows usage."""
        runner = _make_runner()
        event = _make_event(text="/background")
        result = await runner._handle_background_command(event)
        assert "Usage:" in result
        assert "/background" in result

    @pytest.mark.asyncio
    async def test_bg_alias_no_prompt_shows_usage(self):
        """Running /bg with no prompt shows usage."""
        runner = _make_runner()
        event = _make_event(text="/bg")
        result = await runner._handle_background_command(event)
        assert "Usage:" in result

    @pytest.mark.asyncio
    async def test_empty_prompt_shows_usage(self):
        """Running /background with only whitespace shows usage."""
        runner = _make_runner()
        event = _make_event(text="/background   ")
        result = await runner._handle_background_command(event)
        assert "Usage:" in result

    @pytest.mark.asyncio
    async def test_parent_persists_child_before_launch_and_replay_is_idempotent(
        self, tmp_path
    ):
        runner = _make_runner()
        db = _install_state(runner, tmp_path)
        source = _make_event("/background same work").source
        event = MessageEvent(
            text="/background same work",
            source=source,
            session_event_id="parent-command-1",
        )
        launched = []
        runner._launch_gateway_background_turn = lambda turn: launched.append(turn) or True

        first = await runner._handle_background_command(event)
        replay = await runner._handle_background_command(event)

        task_id = runner._gateway_background_task_id(
            event._durable_source_identity
        )
        turns = db.list_session_logical_turns(task_id)
        assert first == replay
        assert len(turns) == 1
        assert turns[0]["task_id"] == task_id
        assert turns[0]["payload"]["parent_logical_turn_id"] is None
        assert db.get_session(task_id) is not None
        assert [turn["logical_turn_id"] for turn in launched] == [
            turns[0]["logical_turn_id"],
            turns[0]["logical_turn_id"],
        ]

    @pytest.mark.asyncio
    async def test_two_identical_background_commands_create_distinct_children(
        self, tmp_path
    ):
        runner = _make_runner()
        db = _install_state(runner, tmp_path)
        source = _make_event("/background identical").source
        events = [
            MessageEvent(
                text="/background identical",
                source=source,
                session_event_id=f"parent-command-{index}",
            )
            for index in (1, 2)
        ]
        runner._launch_gateway_background_turn = lambda _turn: True
        for event in events:
            await runner._handle_background_command(event)

        task_ids = [
            runner._gateway_background_task_id(event._durable_source_identity)
            for event in events
        ]
        assert task_ids[0] != task_ids[1]
        assert all(db.count_logical_turns(task_id) == 1 for task_id in task_ids)

    @pytest.mark.asyncio
    async def test_startup_drain_launches_existing_child_without_parent_replay(
        self, tmp_path
    ):
        runner = _make_runner()
        db = _install_state(runner, tmp_path)
        source = _make_event("/background survive restart").source
        parent = MessageEvent(
            text="/background survive restart",
            source=source,
        )
        parent._logical_turn_id = "durable-parent-turn"
        child = await runner._admit_gateway_background_task(
            parent, "survive restart"
        )

        replacement = _make_runner()
        replacement._session_db = AsyncSessionDB(db)
        recovered = []
        replacement._launch_gateway_background_turn = (
            lambda turn: recovered.append(turn) or True
        )

        assert await replacement._drain_gateway_background_turns() == 1
        assert [turn["logical_turn_id"] for turn in recovered] == [
            child["turn"]["logical_turn_id"]
        ]

    @pytest.mark.asyncio
    async def test_delivery_recovery_replays_result_without_model_execution(
        self, tmp_path
    ):
        runner = _make_runner()
        db = _install_state(runner, tmp_path)
        source = _make_event("/background delivery").source
        parent = MessageEvent(text="/background delivery", source=source)
        parent._logical_turn_id = "delivery-parent"
        child = await runner._admit_gateway_background_task(parent, "delivery")
        turn_id = child["turn"]["logical_turn_id"]
        claim = db.claim_logical_turn(turn_id, owner="old-gateway")
        assert db.mark_logical_turn_started(turn_id, claim["attempt_id"])
        db.complete_logical_turn(
            turn_id,
            claim["attempt_id"],
            {
                "completed": True,
                "response": "persisted result",
                "response_present": True,
                "task_id": child["task_id"],
            },
            delivery_required=True,
        )

        replacement = _make_runner()
        replacement._session_db = AsyncSessionDB(db)
        replacement._gateway_background_turn_dispatching = {}
        adapter = MagicMock()
        adapter.send = AsyncMock(
            return_value=SendResult(success=True, message_id="replayed")
        )
        adapter.extract_media.return_value = ([], "persisted result")
        adapter.extract_images.return_value = ([], "persisted result")
        replacement.adapters[Platform.TELEGRAM] = adapter

        assert await replacement._drain_gateway_background_turns() == 1
        await asyncio.gather(*list(replacement._background_tasks))

        adapter.send.assert_awaited_once()
        assert "persisted result" in adapter.send.await_args.kwargs["content"]
        completed = db.get_logical_turn(turn_id)
        assert completed["state"] == "completed"
        assert completed["delivery_state"] == "delivered"

    @pytest.mark.asyncio
    async def test_transport_accepted_reconciles_without_duplicate_send(
        self, tmp_path
    ):
        runner = _make_runner()
        db = _install_state(runner, tmp_path)
        source = _make_event("/background accepted").source
        parent = MessageEvent(text="/background accepted", source=source)
        parent._logical_turn_id = "accepted-parent"
        child = await runner._admit_gateway_background_task(parent, "accepted")
        turn_id = child["turn"]["logical_turn_id"]
        claim = db.claim_logical_turn(turn_id, owner="old-gateway")
        assert db.mark_logical_turn_started(turn_id, claim["attempt_id"])
        db.complete_logical_turn(
            turn_id,
            claim["attempt_id"],
            {"completed": True, "response": "already accepted"},
            delivery_required=True,
        )
        db.record_logical_turn_transport_accepted(turn_id, claim["attempt_id"])

        replacement = _make_runner()
        replacement._session_db = AsyncSessionDB(db)
        replacement._gateway_background_turn_dispatching = {}
        adapter = MagicMock()
        adapter.send = AsyncMock()
        replacement.adapters[Platform.TELEGRAM] = adapter

        assert await replacement._drain_gateway_background_turns() == 1
        adapter.send.assert_not_awaited()
        assert db.get_logical_turn(turn_id)["delivery_state"] == "delivered"


# ---------------------------------------------------------------------------
# _run_background_task
# ---------------------------------------------------------------------------


class TestRunBackgroundTask:
    """Tests for GatewayRunner._run_background_task (the actual execution)."""


    @pytest.mark.asyncio
    async def test_no_credentials_sends_error(self, tmp_path):
        """When provider credentials are missing, an error is sent."""
        runner = _make_runner()
        db = _install_state(runner, tmp_path)
        mock_adapter = AsyncMock()
        mock_adapter.send = AsyncMock(
            return_value=SendResult(success=True, message_id="error-delivery")
        )
        mock_adapter.toolsets_for_source = MagicMock(return_value=None)
        runner.adapters[Platform.TELEGRAM] = mock_adapter

        source = SessionSource(
            platform=Platform.TELEGRAM,
            user_id="12345",
            chat_id="67890",
            user_name="testuser",
        )

        turn = _seed_background_turn(db, source)
        with patch("gateway.run._resolve_runtime_agent_kwargs", return_value={"api_key": None}):
            await runner._run_background_task(
                "test prompt",
                source,
                "bg_test",
                logical_turn_id=turn["logical_turn_id"],
            )

        # Should have sent an error message
        mock_adapter.send.assert_called_once()
        call_args = mock_adapter.send.call_args
        assert "failed" in call_args[1].get("content", call_args[0][1] if len(call_args[0]) > 1 else "").lower()
        assert db.get_logical_turn(turn["logical_turn_id"])["state"] == "unrecoverable"

    @pytest.mark.asyncio
    async def test_successful_task_sends_result(self, tmp_path):
        """When the agent completes successfully, the result is sent."""
        runner = _make_runner()
        db = _install_state(runner, tmp_path)
        mock_adapter = AsyncMock()
        mock_adapter.send = AsyncMock(
            return_value=SendResult(success=True, message_id="result-delivery")
        )
        mock_adapter.toolsets_for_source = MagicMock(return_value=None)
        mock_adapter.extract_media = MagicMock(return_value=([], "Hello from background!"))
        mock_adapter.extract_images = MagicMock(return_value=([], "Hello from background!"))
        runner.adapters[Platform.TELEGRAM] = mock_adapter

        source = SessionSource(
            platform=Platform.TELEGRAM,
            user_id="12345",
            chat_id="67890",
            user_name="testuser",
        )

        mock_result = {"final_response": "Hello from background!", "messages": []}
        turn = _seed_background_turn(db, source)

        checkpoint_config = {
            "checkpoints": {
                "enabled": True,
                "max_snapshots": 8,
                "max_total_size_mb": 222,
                "max_file_size_mb": 3,
            }
        }
        with patch("gateway.run._resolve_runtime_agent_kwargs", return_value={"api_key": "test-key"}), \
             patch("gateway.run._load_gateway_config", return_value=checkpoint_config), \
             patch("run_agent.AIAgent") as MockAgent:
            mock_agent_instance = MagicMock()
            mock_agent_instance.shutdown_memory_provider = MagicMock()
            mock_agent_instance.close = MagicMock()
            mock_agent_instance.run_conversation.return_value = mock_result
            MockAgent.return_value = mock_agent_instance

            await runner._run_background_task(
                "say hello",
                source,
                "bg_test",
                logical_turn_id=turn["logical_turn_id"],
            )

        # Should have sent the result
        mock_adapter.send.assert_called_once()
        call_args = mock_adapter.send.call_args
        content = call_args[1].get("content", call_args[0][1] if len(call_args[0]) > 1 else "")
        assert "Background task complete" in content
        assert "Hello from background!" in content
        agent_kwargs = MockAgent.call_args.kwargs
        assert agent_kwargs["checkpoints_enabled"] is True
        assert agent_kwargs["checkpoint_max_snapshots"] == 8
        assert agent_kwargs["checkpoint_max_total_size_mb"] == 222
        assert agent_kwargs["checkpoint_max_file_size_mb"] == 3
        mock_agent_instance.shutdown_memory_provider.assert_called_once()
        mock_agent_instance.close.assert_called_once()
        completed = db.get_logical_turn(turn["logical_turn_id"])
        assert completed["state"] == "completed"
        assert completed["delivery_state"] == "delivered"

    @pytest.mark.asyncio
    async def test_unmanaged_persistent_background_execution_fails_closed(self):
        runner = _make_runner()
        source = SessionSource(
            platform=Platform.TELEGRAM,
            user_id="12345",
            chat_id="67890",
        )

        with pytest.raises(Exception, match="refusing unmanaged"):
            await runner._run_background_task("unsafe", source, "bg_test")


# ---------------------------------------------------------------------------
# /background in help and known_commands
# ---------------------------------------------------------------------------


class TestBackgroundInHelp:
    """Verify /background appears in help text and known commands."""

    @pytest.mark.asyncio
    async def test_background_in_help_output(self):
        """The /help output includes /background."""
        runner = _make_runner()
        event = _make_event(text="/help")
        result = await runner._handle_help_command(event)
        assert "/background" in result


# ---------------------------------------------------------------------------
# CLI /background command definition
# ---------------------------------------------------------------------------


class TestBackgroundInCLICommands:
    """Verify /background is registered in the CLI command system."""


    def test_background_autocompletes(self):
        """The /background command appears in autocomplete results."""
        pytest.importorskip("prompt_toolkit")
        from hermes_cli.commands import SlashCommandCompleter
        from prompt_toolkit.document import Document

        completer = SlashCommandCompleter()
        doc = Document("backgro")  # Partial match
        completions = list(completer.get_completions(doc, None))
        # Text doesn't start with / so no completions
        assert len(completions) == 0

        doc = Document("/backgro")  # With slash prefix
        completions = list(completer.get_completions(doc, None))
        cmd_displays = [str(c.display) for c in completions]
        assert any("/background" in d for d in cmd_displays)
