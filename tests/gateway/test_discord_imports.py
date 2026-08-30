"""Import-safety tests for the Discord gateway adapter."""

import subprocess
import sys


class TestDiscordImportSafety:
    def test_module_imports_even_when_discord_dependency_is_missing(self):
        # Run the import-failure simulation in a child interpreter. Replacing
        # this process's canonical adapter module creates split module/class
        # identities for later tests in a monolithic gateway run.
        code = r'''
import builtins
original_import = builtins.__import__
def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name == "discord" or name.startswith("discord."):
        raise ImportError("discord unavailable for test")
    return original_import(name, globals, locals, fromlist, level)
builtins.__import__ = fake_import
import plugins.platforms.discord.adapter as module
assert module.DISCORD_AVAILABLE is False
assert module.discord is None
'''
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(__import__("pathlib").Path(__file__).resolve().parents[2]),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert completed.returncode == 0, completed.stderr
