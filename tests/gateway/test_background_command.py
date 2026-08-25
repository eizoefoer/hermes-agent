"""Tests for /background gateway slash command.

Tests the _handle_background_command handler (run a prompt in a separate
background session) across gateway messenger platforms.
"""

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource
from hermes_state import SessionDB


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
    runner._session_db = SessionDB(
        db_path=Path(os.environ["HERMES_HOME"]) / "state.db"
    )
    runner._reasoning_config = None
    runner._provider_routing = {}
    runner._fallback_model = None
    runner._running_agents = {}
    runner._background_tasks = set()

    mock_store = MagicMock()
    mock_store.get_or_create_session.return_value = SimpleNamespace(
        session_id="gateway-background-parent",
        session_key="telegram:parent",
    )
    runner.session_store = mock_store

    if not runner._session_db.get_session("gateway-background-parent"):
        runner._session_db.create_session("gateway-background-parent", "telegram")

    from gateway.hooks import HookRegistry
    runner.hooks = HookRegistry()

    return runner


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)


async def _run_admitted_background(runner, prompt, source, task_id, **kwargs):
    event = MessageEvent(
        text=f"/background {prompt}",
        source=source,
        message_id=f"event-{task_id}",
    )
    admitted = runner._admit_gateway_background_turn(event, prompt)
    claim = admitted["claim"]
    assert claim["outcome"] == "claimed"
    await runner._run_background_task(
        prompt,
        source,
        admitted["task_id"],
        logical_turn_id=admitted["logical_turn_id"],
        attempt_id=claim["attempt_id"],
        **kwargs,
    )
    return admitted


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
    async def test_valid_prompt_starts_task(self):
        """Running /background with a prompt returns confirmation and starts task."""
        runner = _make_runner()

        # Patch asyncio.create_task to capture the coroutine
        created_tasks = []
        original_create_task = asyncio.create_task

        def capture_task(coro, *args, **kwargs):
            # Close the coroutine to avoid warnings
            coro.close()
            mock_task = MagicMock()
            created_tasks.append(mock_task)
            return mock_task

        with patch("gateway.run.asyncio.create_task", side_effect=capture_task):
            event = _make_event(text="/background Summarize the top HN stories")
            result = await runner._handle_background_command(event)

        assert "🔄" in result
        assert "Background task started" in result
        assert "bg_" in result  # task ID starts with bg_
        assert "Summarize the top HN stories" in result
        assert len(created_tasks) == 1  # background task was created

    @pytest.mark.asyncio
    async def test_telegram_dm_topic_passes_trigger_anchor_to_task(self):
        """Telegram private-topic completion sends need the original command message id."""
        runner = _make_runner()
        runner._run_background_task = AsyncMock()

        def capture_task(coro, *args, **kwargs):
            coro.close()
            mock_task = MagicMock()
            return mock_task

        source = SessionSource(
            platform=Platform.TELEGRAM,
            user_id="12345",
            chat_id="67890",
            chat_type="dm",
            thread_id="20197",
        )
        event = MessageEvent(
            text="/background summarize",
            source=source,
            message_id="463",
            reply_to_message_id="462",
        )

        with patch("gateway.run.asyncio.create_task", side_effect=capture_task):
            result = await runner._handle_background_command(event)

        assert "Background task started" in result
        runner._run_background_task.assert_called_once()
        assert runner._run_background_task.call_args.kwargs["event_message_id"] == "463"

    @pytest.mark.asyncio
    async def test_prompt_truncated_in_preview(self):
        """Long prompts are truncated to 60 chars in the confirmation message."""
        runner = _make_runner()
        long_prompt = "A" * 100

        with patch("gateway.run.asyncio.create_task", side_effect=lambda c, **kw: (c.close(), MagicMock())[1]):
            event = _make_event(text=f"/background {long_prompt}")
            result = await runner._handle_background_command(event)

        assert "..." in result
        # Should not contain the full prompt
        assert long_prompt not in result

    @pytest.mark.asyncio
    async def test_task_id_is_unique(self):
        """Each background task gets a unique task ID."""
        runner = _make_runner()
        task_ids = set()

        with patch("gateway.run.asyncio.create_task", side_effect=lambda c, **kw: (c.close(), MagicMock())[1]):
            for i in range(5):
                event = _make_event(text=f"/background task {i}")
                result = await runner._handle_background_command(event)
                # Extract task ID from result (format: "Task ID: bg_HHMMSS_hex")
                for line in result.split("\n"):
                    if "Task ID:" in line:
                        tid = line.split("Task ID:")[1].strip()
                        task_ids.add(tid)

        assert len(task_ids) == 5  # all unique

    @pytest.mark.asyncio
    async def test_works_across_platforms(self):
        """The /background command works for all platforms."""
        for platform in [Platform.TELEGRAM, Platform.DISCORD, Platform.SLACK]:
            runner = _make_runner()
            with patch("gateway.run.asyncio.create_task", side_effect=lambda c, **kw: (c.close(), MagicMock())[1]):
                event = _make_event(
                    text="/background test task",
                    platform=platform,
                )
                result = await runner._handle_background_command(event)
                assert "Background task started" in result

    @pytest.mark.asyncio
    async def test_child_is_durable_before_started_ack_and_parent_replay_is_idempotent(self):
        runner = _make_runner()
        created = []

        def capture_task(coro, *args, **kwargs):
            coro.close()
            task = MagicMock()
            created.append(task)
            return task

        event = _make_event(text="/background identical work")
        event.message_id = "authoritative-message-1"
        with patch("gateway.run.asyncio.create_task", side_effect=capture_task):
            first = await runner._handle_background_command(event)
            replay = await runner._handle_background_command(event)

        turns = [
            turn
            for session in runner._session_db.list_sessions_rich(
                source="gateway-background", limit=20, include_children=True
            )
            for turn in runner._session_db.list_session_logical_turns(session["id"])
        ]
        assert first == replay
        assert len(created) == 1
        assert len(turns) == 1
        assert turns[0]["state"] == "executing"
        assert turns[0]["task_id"] == turns[0]["session_id"]
        assert runner._session_db.get_session_turn_lease(turns[0]["session_id"])

    @pytest.mark.asyncio
    async def test_identical_commands_with_distinct_message_ids_create_distinct_children(self):
        runner = _make_runner()

        def capture_task(coro, *args, **kwargs):
            coro.close()
            return MagicMock()

        first_event = _make_event(text="/background same text")
        first_event.message_id = "message-a"
        second_event = _make_event(text="/background same text")
        second_event.message_id = "message-b"
        with patch("gateway.run.asyncio.create_task", side_effect=capture_task):
            first = await runner._handle_background_command(first_event)
            second = await runner._handle_background_command(second_event)

        sessions = runner._session_db.list_sessions_rich(
            source="gateway-background", limit=20, include_children=True
        )
        assert first != second
        assert len(sessions) == 2
        assert len({session["id"] for session in sessions}) == 2

    @pytest.mark.asyncio
    async def test_unexpired_background_lease_is_not_stolen_then_expiry_recovers(self):
        runner = _make_runner()
        event = _make_event(text="/background survive restart")
        event.message_id = "restart-message"
        admitted = runner._admit_gateway_background_turn(event, "survive restart")
        original = runner._session_db.get_logical_turn(admitted["logical_turn_id"])
        original_lease = runner._session_db.get_session_turn_lease(admitted["task_id"])
        runner._run_background_task = AsyncMock()

        # A valid durable lease remains authoritative even though no local
        # asyncio task owns it in this replacement runner.
        assert await runner._drain_startup_logical_turns(limit=10) == 0
        assert runner._run_background_task.await_count == 0
        assert runner._session_db.get_session_turn_lease(admitted["task_id"]) == original_lease
        assert runner._session_db.get_logical_turn(admitted["logical_turn_id"])["attempt_count"] == 1

        assert runner._session_db.renew_session_turn_lease(
            admitted["task_id"],
            original["lease_holder"],
            original["current_attempt_id"],
            ttl_seconds=-1,
        )
        assert runner._session_db.reconcile_logical_turns() == 1
        assert await runner._drain_startup_logical_turns(limit=10) == 1
        if runner._background_tasks:
            await asyncio.gather(*list(runner._background_tasks))

        recovered = runner._session_db.get_logical_turn(admitted["logical_turn_id"])
        assert runner._run_background_task.await_count == 1
        assert runner._run_background_task.call_args.kwargs["logical_turn_id"] == admitted["logical_turn_id"]
        assert recovered["attempt_count"] == 2
        assert recovered["current_attempt_id"] != original["current_attempt_id"]


# ---------------------------------------------------------------------------
# _run_background_task
# ---------------------------------------------------------------------------


class TestRunBackgroundTask:
    """Tests for GatewayRunner._run_background_task (the actual execution)."""

    @pytest.mark.asyncio
    async def test_no_adapter_returns_silently(self):
        """When no adapter is available, the task returns without error."""
        runner = _make_runner()
        source = SessionSource(
            platform=Platform.TELEGRAM,
            user_id="12345",
            chat_id="67890",
            user_name="testuser",
        )
        # No adapters set — should not raise
        await _run_admitted_background(runner, "test prompt", source, "bg_test")

    @pytest.mark.asyncio
    async def test_no_credentials_sends_error(self):
        """When provider credentials are missing, an error is sent."""
        runner = _make_runner()
        mock_adapter = AsyncMock()
        mock_adapter.send = AsyncMock()
        runner.adapters[Platform.TELEGRAM] = mock_adapter

        source = SessionSource(
            platform=Platform.TELEGRAM,
            user_id="12345",
            chat_id="67890",
            user_name="testuser",
        )

        with patch("gateway.run._resolve_runtime_agent_kwargs", return_value={"api_key": None}):
            await _run_admitted_background(runner, "test prompt", source, "bg_test")

        # Should have sent an error message
        mock_adapter.send.assert_called_once()
        call_args = mock_adapter.send.call_args
        assert "failed" in call_args[1].get("content", call_args[0][1] if len(call_args[0]) > 1 else "").lower()

    @pytest.mark.asyncio
    async def test_successful_task_sends_result(self):
        """When the agent completes successfully, the result is sent."""
        runner = _make_runner()
        mock_adapter = AsyncMock()
        mock_adapter.send = AsyncMock()
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

        with patch("gateway.run._resolve_runtime_agent_kwargs", return_value={"api_key": "test-key"}), \
             patch("run_agent.AIAgent") as MockAgent:
            mock_agent_instance = MagicMock()
            mock_agent_instance.shutdown_memory_provider = MagicMock()
            mock_agent_instance.close = MagicMock()
            mock_agent_instance.run_conversation.return_value = mock_result
            MockAgent.return_value = mock_agent_instance

            await _run_admitted_background(runner, "say hello", source, "bg_test")

        # Should have sent the result
        mock_adapter.send.assert_called_once()
        call_args = mock_adapter.send.call_args
        content = call_args[1].get("content", call_args[0][1] if len(call_args[0]) > 1 else "")
        assert "Background task complete" in content
        assert "Hello from background!" in content
        mock_agent_instance.shutdown_memory_provider.assert_called_once()
        mock_agent_instance.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_telegram_dm_topic_completion_preserves_reply_anchor_metadata(self, monkeypatch):
        """Background completion metadata must let Telegram send thread id plus reply id."""
        from gateway import run as gateway_run

        runner = _make_runner()
        runner._resolve_session_agent_runtime = MagicMock(
            return_value=("test-model", {"api_key": "test-key"})
        )
        runner._resolve_session_reasoning_config = MagicMock(return_value=None)
        runner._load_service_tier = MagicMock(return_value=None)
        runner._resolve_turn_agent_config = MagicMock(
            return_value={
                "model": "test-model",
                "runtime": {"api_key": "test-key"},
                "request_overrides": None,
            }
        )
        runner._run_in_executor_with_context = AsyncMock(
            return_value={"final_response": "done", "messages": []}
        )
        monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: {})

        mock_adapter = AsyncMock()
        mock_adapter.send = AsyncMock()
        mock_adapter.extract_media = MagicMock(return_value=([], "done"))
        mock_adapter.extract_images = MagicMock(return_value=([], "done"))
        runner.adapters[Platform.TELEGRAM] = mock_adapter

        source = SessionSource(
            platform=Platform.TELEGRAM,
            user_id="12345",
            chat_id="67890",
            chat_type="dm",
            thread_id="20197",
        )

        await _run_admitted_background(
            runner,
            "say hello",
            source,
            "bg_test",
            event_message_id="463",
        )

        mock_adapter.send.assert_called_once()
        assert mock_adapter.send.call_args.kwargs["metadata"] == {
            "thread_id": "20197",
            "telegram_dm_topic_reply_fallback": True,
            "direct_messages_topic_id": "20197",
            "telegram_reply_to_message_id": "463",
        }

    @pytest.mark.asyncio
    async def test_agent_cleanup_runs_when_background_agent_raises(self):
        """Temporary background agents must be cleaned up on error paths too."""
        runner = _make_runner()
        mock_adapter = AsyncMock()
        mock_adapter.send = AsyncMock()
        runner.adapters[Platform.TELEGRAM] = mock_adapter

        source = SessionSource(
            platform=Platform.TELEGRAM,
            user_id="12345",
            chat_id="67890",
            user_name="testuser",
        )

        with patch("gateway.run._resolve_runtime_agent_kwargs", return_value={"api_key": "test-key"}), \
             patch("run_agent.AIAgent") as MockAgent:
            mock_agent_instance = MagicMock()
            mock_agent_instance.shutdown_memory_provider = MagicMock()
            mock_agent_instance.close = MagicMock()
            mock_agent_instance.run_conversation.side_effect = RuntimeError("boom")
            MockAgent.return_value = mock_agent_instance

            await _run_admitted_background(runner, "say hello", source, "bg_test")

        mock_adapter.send.assert_called_once()
        mock_agent_instance.shutdown_memory_provider.assert_called_once()
        mock_agent_instance.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_exception_sends_error_message(self):
        """When the agent raises an exception, an error message is sent."""
        runner = _make_runner()
        mock_adapter = AsyncMock()
        mock_adapter.send = AsyncMock()
        runner.adapters[Platform.TELEGRAM] = mock_adapter

        source = SessionSource(
            platform=Platform.TELEGRAM,
            user_id="12345",
            chat_id="67890",
            user_name="testuser",
        )

        with patch("gateway.run._resolve_runtime_agent_kwargs", side_effect=RuntimeError("boom")):
            await _run_admitted_background(runner, "test prompt", source, "bg_test")

        mock_adapter.send.assert_called_once()
        call_args = mock_adapter.send.call_args
        content = call_args[1].get("content", call_args[0][1] if len(call_args[0]) > 1 else "")
        assert "failed" in content.lower()


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

    def test_background_is_known_command(self):
        """The /background command is in GATEWAY_KNOWN_COMMANDS."""
        from hermes_cli.commands import GATEWAY_KNOWN_COMMANDS
        assert "background" in GATEWAY_KNOWN_COMMANDS

    def test_bg_alias_is_known_command(self):
        """The /bg alias is in GATEWAY_KNOWN_COMMANDS."""
        from hermes_cli.commands import GATEWAY_KNOWN_COMMANDS
        assert "bg" in GATEWAY_KNOWN_COMMANDS


# ---------------------------------------------------------------------------
# CLI /background command definition
# ---------------------------------------------------------------------------


class TestBackgroundInCLICommands:
    """Verify /background is registered in the CLI command system."""

    def test_background_in_commands_dict(self):
        """The /background command is in the COMMANDS dict."""
        from hermes_cli.commands import COMMANDS
        assert "/background" in COMMANDS

    def test_bg_alias_in_commands_dict(self):
        """The /bg alias is in the COMMANDS dict."""
        from hermes_cli.commands import COMMANDS
        assert "/bg" in COMMANDS

    def test_background_in_session_category(self):
        """The /background command is in the Session category."""
        from hermes_cli.commands import COMMANDS_BY_CATEGORY
        assert "/background" in COMMANDS_BY_CATEGORY["Session"]

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
