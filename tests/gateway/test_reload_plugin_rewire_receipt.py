"""Public reload receipts must reflect executed profile-scoped rewiring."""
import asyncio
import os
from pathlib import Path

import pytest

from gateway.run_plugin_rewire import GatewayPluginRewireMixin, reload_plugins_verb
from hermes_cli.plugins import discover_plugins, get_plugin_command_handler, get_plugin_manager


@pytest.mark.parametrize('secondary', [False, True])
def test_same_key_reload_executes_rewire_and_reports_success(secondary, tmp_path):
    home = Path(os.environ['HERMES_HOME'])
    target = tmp_path / 'secondary' if secondary else home
    plugin = target / 'plugins' / 'receipt_probe'
    plugin.mkdir(parents=True)
    (plugin / 'plugin.yaml').write_text("name: receipt_probe\nversion: '0.1'\ndescription: test\n")
    (plugin / '__init__.py').write_text(
        'def register(ctx):\n    ctx.register_command("receiptprobe", lambda raw: "ok", description="test")\n')
    (target / 'config.yaml').write_text('plugins:\n  enabled: [receipt_probe]\n')

    class Adapter:
        fail = False
        def __init__(self): self.seen = []
        def rewire_plugin_handlers(self):
            if self.fail:
                raise RuntimeError('test callback failure')
            self.seen.append(get_plugin_command_handler('receiptprobe'))

    class Runner(GatewayPluginRewireMixin): pass

    async def scenario():
        from gateway.run import _profile_runtime_scope
        runner = Runner()
        selected, other = Adapter(), Adapter()
        runner.adapters = {'primary': other if secondary else selected}
        runner._profile_adapters = {'secondary': {'adapter': selected}}
        runner._served_profile_homes = {'secondary': target}
        scope = _profile_runtime_scope(target, hydrate_secrets=False)
        with scope:
            discover_plugins(force=True)  # already loaded before control request
            runner._subscribe_plugin_rewire(get_plugin_manager(),
                'secondary' if secondary else None, target if secondary else None)
        verb = reload_plugins_verb(runner, asyncio.get_running_loop())
        for _ in range(2):
            result = await asyncio.to_thread(verb, {'home': str(target)})
            assert result['reloaded'] is True
            assert result['adapters_rewired'] == 1
            assert selected.seen and selected.seen[-1] is not None
        assert selected.seen[0] is not selected.seen[-1]  # fresh registration actually reached adapter
        assert not other.seen
        selected.fail = True
        result = await asyncio.to_thread(verb, {'home': str(target)})
        assert result['adapters_rewired'] == 0
        before = len(selected.seen)
        refused = await asyncio.to_thread(verb, {'home': str(tmp_path / 'unserved')})
        assert refused['reloaded'] is False
        assert len(selected.seen) == before

    asyncio.run(scenario())
