"""Quiet tasks must report on wall-clock ticks, not on terminal-event delivery."""
import asyncio
from types import SimpleNamespace

import pytest

from gateway.config import Platform
from gateway.run import GatewayRunner
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_notify as kbn


class Adapter:
    def __init__(self):
        self.sent = []
        self.fail = False

    async def send(self, chat_id, text, metadata=None):
        if self.fail:
            return SimpleNamespace(success=False, error="temporary outage")
        self.sent.append((chat_id, text, metadata))
        return SimpleNamespace(success=True)

    async def handle_message(self, event):
        pytest.fail("progress must not wake an agent")


def runner_for(adapter):
    runner = GatewayRunner.__new__(GatewayRunner)
    runner._running = True
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._kanban_dispatcher_lock_handle = object()
    runner._kanban_sub_fail_counts = {}
    return runner


async def tick(monkeypatch, runner):
    async def fake_sleep(delay):
        if delay != 5:
            runner._running = False
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    await runner._kanban_notifier_watcher(interval=1)


@pytest.fixture
def running_task(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "board.db"))
    monkeypatch.setattr("time.time", lambda: 10000)
    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title="quiet work", assignee="worker")
        assert kb.claim_task(conn, tid, claimer="test")
        kbn.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="origin",
                           thread_id="topic", notifier_profile="default", delivery_mode="notify+wake")
    return tid


def test_quiet_tick_cadence_survives_new_runner_without_burst(running_task, monkeypatch):
    adapter = Adapter()
    # Expanding intervals: first after 10m, then +20m, then +40m (capped).
    for elapsed, count in [(599, 0), (600, 1), (1799, 1), (1800, 2), (4199, 2), (4200, 3),
                           (6599, 3), (6600, 4), (15000, 5), (15001, 5)]:
        monkeypatch.setattr("time.time", lambda: 10000 + elapsed)
        asyncio.run(tick(monkeypatch, runner_for(adapter)))
        assert len(adapter.sent) == count
    assert all(running_task in text and "quiet work" in text for _, text, _ in adapter.sent)
    assert all(meta["thread_id"] == "topic" for _, _, meta in adapter.sent)
    with kbc.connect_closing() as conn:
        progress = [ev for ev in kb.list_events(conn, running_task) if ev.kind == "progress"]
    assert len(progress) == 5


def test_failed_progress_send_retries_then_dedupes_after_restart(running_task, monkeypatch):
    monkeypatch.setattr("time.time", lambda: 10600)
    adapter = Adapter()
    adapter.fail = True
    asyncio.run(tick(monkeypatch, runner_for(adapter)))
    assert not adapter.sent
    adapter.fail = False
    asyncio.run(tick(monkeypatch, runner_for(adapter)))
    asyncio.run(tick(monkeypatch, runner_for(adapter)))
    assert len(adapter.sent) == 1


def test_expired_collected_batch_cannot_send(running_task, monkeypatch):
    from gateway.kanban_watchers_notifier import _KanbanNotification, _notifier_collect
    monkeypatch.setattr("time.time", lambda: 10600)
    adapter = Adapter()
    runner = runner_for(adapter)
    deliveries = _notifier_collect(runner, kb, notifier_profile="default", gc_due=False, gc_retention_days=30)
    assert len(deliveries) == 1
    monkeypatch.setattr("time.time", lambda: 11000)
    asyncio.run(_KanbanNotification(runner, deliveries[0], platform_cls=Platform, sub_fail_counts={}).deliver())
    assert not adapter.sent
    asyncio.run(tick(monkeypatch, runner_for(adapter)))
    assert len(adapter.sent) == 1


def test_outage_does_not_permanently_remove_subscription(running_task, monkeypatch):
    adapter = Adapter()
    adapter.fail = True
    for attempt in range(20):
        monkeypatch.setattr("time.time", lambda: 10600 + attempt * 600)
        runner = runner_for(adapter)
        runner._kanban_sub_fail_counts = {(running_task, "telegram", "origin", "topic"): 12}
        asyncio.run(tick(monkeypatch, runner))
    with kbc.connect_closing() as conn:
        assert len(kbn.list_notify_subs(conn, running_task)) == 1
    adapter.fail = False
    monkeypatch.setattr("time.time", lambda: 30000)
    asyncio.run(tick(monkeypatch, runner_for(adapter)))
    assert len(adapter.sent) == 1  # downtime coalesces to current progress, never a burst


def test_completed_task_does_not_emit_elapsed_progress(running_task, monkeypatch):
    monkeypatch.setattr("time.time", lambda: 10600)
    with kbc.connect_closing() as conn:
        kb.complete_task(conn, running_task, summary="actually finished", force=True)
    adapter = Adapter()
    # Passive terminal subscription avoids intentionally waking on completion.
    with kbc.connect_closing() as conn:
        kbn.add_notify_sub(conn, task_id=running_task, platform="telegram", chat_id="origin",
                           thread_id="topic", delivery_mode="notify")
    asyncio.run(tick(monkeypatch, runner_for(adapter)))
    assert len(adapter.sent) == 1 and "actually finished" in adapter.sent[0][1]
    with kbc.connect_closing() as conn:
        assert not [ev for ev in kb.list_events(conn, running_task) if ev.kind == "progress"]


def test_foreign_owner_not_claimed_or_scheduled(running_task, monkeypatch):
    monkeypatch.setattr("time.time", lambda: 10600)
    adapter = Adapter()
    runner = runner_for(adapter)
    runner._active_profile_name = lambda: "another-profile"
    runner._kanban_dispatcher_lock_handle = None
    asyncio.run(tick(monkeypatch, runner))
    assert not adapter.sent
    with kbc.connect_closing() as conn:
        assert not [ev for ev in kb.list_events(conn, running_task) if ev.kind == "progress"]
