"""Real CLI module fallback startup, without network update-service coupling."""
import os
import shutil
import subprocess
from unittest import mock

from hermes_cli import kanban_db_dispatch as kbd
from hermes_cli.config import atomic_config_write


def test_resolve_hermes_argv_module_actually_runs(tmp_path, monkeypatch):
    """The dispatch fallback must import and execute the actual CLI entry point."""
    home = tmp_path / "hermes-home"
    home.mkdir()
    atomic_config_write(home / "config.yaml", {"updates": {"check": False}})
    monkeypatch.setenv("HERMES_HOME", str(home))
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("HERMES_BIN", None)
        with mock.patch.object(shutil, "which", return_value=None):
            argv = kbd._resolve_hermes_argv()
    result = subprocess.run(argv + ["--version"], capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, (
        f"`{' '.join(argv)} --version` failed (rc={result.returncode}); "
        f"stderr={result.stderr[:200]!r}"
    )
