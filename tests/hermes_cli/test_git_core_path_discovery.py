"""Real-git lookup must handle PM and custom Git-for-Windows install roots."""
from pathlib import Path

import pytest

from hermes_cli import update_cmd_git


@pytest.mark.parametrize("arch", ["mingw64", "clangarm64", "mingw32"])
def test_locator_probes_git_core_beside_path_launcher(tmp_path, monkeypatch, arch):
    root = tmp_path / "setup-pm" / "tools" / "git-custom"
    launcher = root / "cmd" / "git.EXE"
    core = root / arch / "libexec" / "git-core" / "git.exe"
    launcher.parent.mkdir(parents=True)
    launcher.touch()
    core.parent.mkdir(parents=True)
    core.touch()
    monkeypatch.setattr("shutil.which", lambda name: str(launcher))
    monkeypatch.setattr(update_cmd_git, "_portable_git_candidates", list)
    probes = []

    def probe(argv):
        probes.append(Path(argv[0]))
        return False if Path(argv[0]) == core else None

    monkeypatch.setattr(update_cmd_git, "_probe_fork_bomb", probe)
    assert update_cmd_git._locate_real_git() == core
    assert core in probes
    assert launcher not in probes, "the PATH trampoline itself is not git-core"


def test_locator_rejects_unusable_path_git_core(tmp_path, monkeypatch):
    launcher = tmp_path / "Git" / "bin" / "git.exe"
    core = launcher.parent.parent / "mingw64" / "libexec" / "git-core" / "git.exe"
    core.parent.mkdir(parents=True)
    core.touch()
    monkeypatch.setattr("shutil.which", lambda name: str(launcher))
    monkeypatch.setattr(update_cmd_git, "_portable_git_candidates", list)
    monkeypatch.setattr(update_cmd_git, "_probe_fork_bomb", lambda argv: None)
    assert update_cmd_git._locate_real_git() is None
