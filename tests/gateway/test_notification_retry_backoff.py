"""Failed admission retains events without polling the same broken route every tick."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.run_notifications import GatewayNotificationsMixin
from gateway.config import Platform
from gateway.session import SessionSource
from hermes_state_runtime import RuntimeStoreError
from gateway.wake import WakeNotAccepted


@pytest.mark.asyncio
@pytest.mark.parametrize('durable', [False, True])
async def test_refused_notification_backs_off_then_recovers_without_blocking_other_routes(monkeypatch, durable):
    clock = [100.0]
    monkeypatch.setattr('gateway.run_notifications.time.monotonic', lambda: clock[0])
    once = AsyncMock(side_effect=RuntimeStoreError('admission_conflict'))
    runner = object.__new__(GatewayNotificationsMixin)
    runner._build_process_event_source = lambda evt: SessionSource(
        platform=Platform.TELEGRAM, chat_id='fixture', chat_type='dm')
    adapter = SimpleNamespace(supports_async_delivery=True)
    runner._resolve_injection_adapter = lambda *args: adapter
    monkeypatch.setattr('gateway.session_authorities.active_authority',
                        lambda _: SimpleNamespace(admit_automation=once))
    event = {'type': 'async_delegation', 'session_key': 'route-a', 'delegation_id': 'child-a'}

    async def offer(evt):
        if durable:
            with pytest.raises(WakeNotAccepted):
                await GatewayNotificationsMixin._inject_watch_notification(runner, 'result', evt,
                                                                           raise_not_accepted=True)
        else:
            assert await GatewayNotificationsMixin._inject_watch_notification(runner, 'result', evt) is False

    await offer(event)
    for _ in range(20):
        await offer(event)
    assert once.await_count == 1
    await offer(dict(event, session_key='route-b'))
    assert once.await_count == 2
    clock[0] += 2
    await offer(event)
    assert once.await_count == 3
    clock[0] += 2
    await offer(event)
    assert once.await_count == 3  # second failure waits four seconds
    clock[0] += 2
    once.side_effect = None
    once.return_value = True
    assert await GatewayNotificationsMixin._inject_watch_notification(runner, 'result', event) is True
    assert once.await_count == 4
    assert await GatewayNotificationsMixin._inject_watch_notification(runner, 'result', event) is True
    assert once.await_count == 5  # successful admission clears the transient refusal
