"""An external drain refresh must keep work descriptions consistent with counts."""
import asyncio
from types import SimpleNamespace

from gateway.run import GatewayRunner


def test_external_drain_refreshes_description_when_work_finishes(monkeypatch):
    import gateway.drain_control as drain_control
    import gateway.run as gateway_run

    writes = []
    runner = SimpleNamespace(
        _running=True, _draining=False, _external_drain_active=False,
        _restart_requested=False, pending=True,
    )
    for name in ('_drain_control_watcher', '_enter_external_drain',
                 '_update_runtime_status', '_persist_active_agents'):
        setattr(runner, name, getattr(GatewayRunner, name).__get__(runner))
    runner._active_work_count = lambda: int(runner.pending)
    runner._describe_active_work = lambda: [{'kind': 'fixture'}] if runner.pending else []
    monkeypatch.setattr(drain_control, 'drain_requested', lambda: True)

    def write_status(**values):
        writes.append(values)
        if values.get('active_agents') == 1:
            runner.pending = False
        elif values.get('active_agents') == 0:
            runner._running = False

    monkeypatch.setattr(gateway_run, '_write_runtime_status_quiet', write_status)
    asyncio.run(asyncio.wait_for(runner._drain_control_watcher(interval=0), timeout=3))
    assert writes[0]['active_agents'] == 1
    assert writes[0]['active_work']
    assert writes[-1]['active_agents'] == 0
    assert writes[-1].get('active_work', 'not refreshed') == []
