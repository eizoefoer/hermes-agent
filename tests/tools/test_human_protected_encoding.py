import json
import subprocess

import pytest

from tools.file_tools import patch_tool, write_file_tool


@pytest.mark.parametrize(("route", "encoding"), [
    (route, encoding)
    for route in ("write", "replace", "patch")
    for encoding in ("bom", "crlf")
])
def test_human_once_approval_preserves_bom_and_crlf(tmp_path, monkeypatch, route, encoding):
    import hermes_cli.config as hc
    from tools import file_tools
    from tools.environments.local import LocalEnvironment
    from tools.file_operations import ShellFileOperations
    from hermes_cli.config import atomic_config_write
    from tools.terminal_tool import set_approval_callback

    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    home = tmp_path / "test-home"
    home.mkdir()
    atomic_config_write(home / "config.yaml", {
        "security": {"protected_instruction_exact_transforms": []}})
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("TERMINAL_CWD", str(root))
    monkeypatch.delenv("HERMES_SINGLE_QUERY_SESSION", raising=False)
    hc._LOAD_CONFIG_CACHE.clear()

    target = root / "AGENTS.md"
    before = "old\r\n" if encoding == "crlf" else "\ufeffold\n"
    on_disk_candidate = "new\r\n" if encoding == "crlf" else "\ufeffnew\n"
    target.write_bytes(before.encode("utf-8"))
    ops = ShellFileOperations(LocalEnvironment(cwd=str(root)))
    monkeypatch.setattr(file_tools, "_get_file_ops", lambda task_id="default": ops)
    # Establish a real whole-file baseline before exercising the tool entrypoint.
    read = json.loads(file_tools.read_file_tool(str(target), task_id="human-encoding"))
    assert not read.get("error"), read

    asked = []
    set_approval_callback(lambda *args, **kwargs: asked.append(kwargs) or "once")
    try:
        if route == "write":
            result = json.loads(write_file_tool(str(target), "new\n", task_id="human-encoding"))
        elif route == "replace":
            result = json.loads(patch_tool(
                mode="replace", path=str(target), old_string="old", new_string="new",
                task_id="human-encoding"))
        else:
            patch = (f"*** Begin Patch\n*** Update File: {target}\n@@\n-old\n+new\n"
                     "*** End Patch")
            result = json.loads(patch_tool(mode="patch", patch=patch, task_id="human-encoding"))
    finally:
        set_approval_callback(None)

    assert len(asked) == 1
    assert not result.get("error"), result
    assert target.read_bytes() == on_disk_candidate.encode("utf-8")
