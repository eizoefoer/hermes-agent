import sys
from types import ModuleType, SimpleNamespace

import pytest
from acp.schema import TextContentBlock

from acp_adapter.server import HermesACPAgent
from acp_adapter.session import SessionManager


class FakeAgent:
    def __init__(self):
        self.model = "fake-model"
        self.provider = "fake-provider"
        self.enabled_toolsets = ["hermes-acp"]
        self.disabled_toolsets = []
        self.tools = []
        self.valid_tool_names = set()
        self.steers = []
        self.runs = []

    def steer(self, text):
        self.steers.append(text)
        return True

    def run_conversation(self, *, user_message, conversation_history, task_id, **kwargs):
        self.runs.append(user_message)
        messages = list(conversation_history or [])
        messages.append({"role": "user", "content": user_message})
        final = f"ran: {user_message}"
        messages.append({"role": "assistant", "content": final})
        return {"final_response": final, "messages": messages}


class CaptureConn:
    def __init__(self):
        self.updates = []

    async def session_update(self, *args, **kwargs):
        if kwargs:
            self.updates.append((kwargs.get("session_id"), kwargs.get("update")))
        else:
            self.updates.append((args[0], args[1]))

    async def request_permission(self, *args, **kwargs):
        return SimpleNamespace(outcome="allow")


class NoopDb:
    def __init__(self):
        self.turns = []

    def get_session(self, *_args, **_kwargs):
        return None

    def create_session(self, *_args, **_kwargs):
        return None

    def update_session(self, *_args, **_kwargs):
        return None

    def replace_messages(self, *_args, **_kwargs):
        return None

    def admit_session_event(self, **kwargs):
        turn = {
            "logical_turn_id": f"turn-{len(self.turns) + 1}",
            "session_id": kwargs["session_id"],
            "state": "queued",
            "payload": {**kwargs.get("payload", {}), "event_type": kwargs["event_type"]},
        }
        self.turns.append(turn)
        return turn

    def claim_logical_turn(self, logical_turn_id, **_kwargs):
        turn = next(t for t in self.turns if t["logical_turn_id"] == logical_turn_id)
        if turn["state"] == "completed":
            return {"outcome": "terminal"}
        turn["state"] = "claimed"
        return {"outcome": "claimed", "attempt_id": f"attempt-{logical_turn_id}"}

    def mark_logical_turn_started(self, *_args, **_kwargs):
        return None

    def complete_logical_turn(self, logical_turn_id, _attempt_id, *_args, **_kwargs):
        next(t for t in self.turns if t["logical_turn_id"] == logical_turn_id)["state"] = "completed"

    def fail_logical_turn(self, logical_turn_id, _attempt_id, *_args, **_kwargs):
        next(t for t in self.turns if t["logical_turn_id"] == logical_turn_id)["state"] = "failed"

    def reconcile_logical_turns(self):
        return 0

    def list_ready_logical_turns(self, **_kwargs):
        return [t for t in self.turns if t["state"] == "queued"]

    def begin_logical_turn_delivery(self, *_args, **_kwargs):
        return None

    def acknowledge_logical_turn_delivery(self, *_args, **_kwargs):
        return None

    def fail_logical_turn_delivery(self, *_args, **_kwargs):
        return None


def make_agent_and_state():
    fake = FakeAgent()
    manager = SessionManager(agent_factory=lambda **kwargs: fake, db=NoopDb())
    acp_agent = HermesACPAgent(session_manager=manager)
    state = manager.create_session(cwd=".")
    conn = CaptureConn()
    acp_agent.on_connect(conn)
    return acp_agent, state, fake, conn


def test_acp_real_agent_gets_session_db_for_recall(monkeypatch):
    """ACP sessions persist to SessionDB; recall must receive the same DB handle."""
    captured = {}
    sentinel_db = NoopDb()

    class CapturingAgent(FakeAgent):
        def __init__(self, **kwargs):
            super().__init__()
            captured.update(kwargs)

    def mod(name, **attrs):
        module = ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        return module

    monkeypatch.setitem(sys.modules, "run_agent", mod("run_agent", AIAgent=CapturingAgent))
    monkeypatch.setitem(
        sys.modules,
        "hermes_cli.config",
        mod("hermes_cli.config", load_config=lambda: {"model": {"default": "m", "provider": "p"}}),
    )
    monkeypatch.setitem(
        sys.modules,
        "hermes_cli.runtime_provider",
        mod(
            "hermes_cli.runtime_provider",
            resolve_runtime_provider=lambda **_kwargs: {
                "provider": "p",
                "api_mode": "chat_completions",
                "base_url": "u",
                "api_key": "k",
                "command": None,
                "args": [],
            },
        ),
    )

    manager = SessionManager(db=sentinel_db)
    agent = manager._make_agent(session_id="acp-session", cwd=".")

    assert isinstance(agent, CapturingAgent)
    assert captured["session_db"] is sentinel_db
    assert captured["platform"] == "acp"
    assert captured["session_id"] == "acp-session"


@pytest.mark.asyncio
async def test_acp_steer_slash_command_injects_into_running_agent():
    acp_agent, state, fake, _conn = make_agent_and_state()
    state.is_running = True

    response = await acp_agent.prompt(
        session_id=state.session_id,
        prompt=[TextContentBlock(type="text", text="/steer prefer the simpler fix")],
    )

    assert response.stop_reason == "end_turn"
    assert fake.steers == ["prefer the simpler fix"]
    assert fake.runs == []


@pytest.mark.asyncio
async def test_acp_steer_after_zed_interrupt_replays_interrupted_prompt_with_guidance():
    acp_agent, state, fake, _conn = make_agent_and_state()
    state.interrupted_prompt_text = "write hi to a text file"

    response = await acp_agent.prompt(
        session_id=state.session_id,
        prompt=[TextContentBlock(type="text", text="/steer write HELLO instead")],
    )

    assert response.stop_reason == "end_turn"
    assert fake.steers == []
    assert fake.runs == [
        "write hi to a text file\n\nUser correction/guidance after interrupt: write HELLO instead"
    ]
    assert state.interrupted_prompt_text == ""


@pytest.mark.asyncio
async def test_acp_steer_on_idle_session_runs_as_regular_prompt():
    # /steer on an idle session (no running turn, nothing to salvage) should
    # run the steer payload as a normal user prompt — NOT silently append it
    # to state.queued_prompts. Without this, users on Zed / other ACP clients
    # see their /steer turn into "queued for the next turn" when they never
    # typed /queue. Matches gateway/run.py ~L4898 idle-/steer behavior.
    acp_agent, state, fake, _conn = make_agent_and_state()

    response = await acp_agent.prompt(
        session_id=state.session_id,
        prompt=[TextContentBlock(type="text", text="/steer summarize the README")],
    )

    assert response.stop_reason == "end_turn"
    assert fake.steers == []
    assert fake.runs == ["summarize the README"]
    assert state.queued_prompts == []


@pytest.mark.asyncio
async def test_acp_queue_slash_command_adds_next_turn_without_running_now():
    acp_agent, state, fake, _conn = make_agent_and_state()

    response = await acp_agent.prompt(
        session_id=state.session_id,
        prompt=[TextContentBlock(type="text", text="/queue run the tests after this")],
    )

    assert response.stop_reason == "end_turn"
    ready = acp_agent.session_manager._get_db().list_ready_logical_turns()
    assert [turn["payload"]["user_text"] for turn in ready] == ["run the tests after this"]
    assert fake.runs == []


@pytest.mark.asyncio
async def test_acp_prompt_drains_queued_turns_after_current_run():
    acp_agent, state, fake, conn = make_agent_and_state()
    await acp_agent.prompt(
        session_id=state.session_id,
        prompt=[TextContentBlock(type="text", text="/queue then run tests")],
    )

    response = await acp_agent.prompt(
        session_id=state.session_id,
        prompt=[TextContentBlock(type="text", text="make the change")],
    )

    assert response.stop_reason == "end_turn"
    assert fake.runs == ["make the change", "then run tests"]
    assert state.queued_prompts == []
    agent_messages = [u for _sid, u in conn.updates if getattr(u, "session_update", None) == "agent_message_chunk"]
    assert len(agent_messages) >= 2
