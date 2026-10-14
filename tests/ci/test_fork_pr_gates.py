"""Run attribution against divergent fork history and validate runnable CI declarations."""

import os
from pathlib import Path
import subprocess

import pytest
from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[2]


def workflow(name):
    return YAML(typ="safe").load((ROOT / ".github/workflows" / name).read_text())


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def commit(repo, name, email):
    (repo / name).write_text(name)
    git(repo, "add", name)
    git(repo, "-c", "user.name=Contributor", "-c", f"user.email={email}", "commit", "-m", name)
    return git(repo, "rev-parse", "HEAD")


@pytest.mark.parametrize("mapped", [True, False])
def test_attribution_checks_only_actual_pr_history(tmp_path, mapped):
    repo = tmp_path / "fork"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    commit(repo, "common", "common@example.test")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "checkout", "-b", "compat/recovery")
    base = commit(repo, "upstream-import", "imported@example.test")
    head = commit(repo, "real-pr-change", "actual@example.test")
    emails = repo / "contributors/emails"
    emails.mkdir(parents=True)
    if mapped:
        (emails / "actual@example.test").write_text("actual-contributor\n")
    step = next(s for s in workflow("contributor-check.yml")["jobs"]["check-attribution"]["steps"]
                if s.get("id") == "check-emails")
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", step["run"]], cwd=repo,
        env={**os.environ, "PR_BASE_SHA": base, "PR_HEAD_SHA": head,
             "GITHUB_OUTPUT": str(tmp_path / "outputs")}, capture_output=True, text=True,
        check=False,
    )
    assert "imported@example.test" not in result.stdout
    assert "common@example.test" not in result.stdout
    assert result.returncode == (0 if mapped else 1), result.stdout + result.stderr
    if not mapped:
        assert "actual@example.test" in result.stdout


def test_required_pr_test_lanes_have_standard_runners_and_bounded_workers():
    linux = workflow("tests.yml")["jobs"]["test"]
    assert linux["runs-on"] == "ubuntu-latest"
    assert linux["strategy"]["matrix"]["slice"] == [1, 2]
    run = next(s for s in linux["steps"] if s.get("name") == "Run tests")
    assert int(run["env"]["HERMES_TEST_WORKERS"]) <= 4
    os_job = workflow("tests-os.yml")["jobs"]["os-tests"]
    rows = os_job["strategy"]["matrix"]["include"]
    assert {r["runner"] for r in rows if r["marker"] == "windows"} == {
        "windows-latest", "windows-11-arm",
    }
    assert all(r.get("workers", 3) <= 4 for r in rows)
    os_run = next(s for s in os_job["steps"] if s.get("name") == "Run ${{ matrix.marker }} tests")
    assert 900 <= int(os_run["env"]["HERMES_TEST_FILE_TIMEOUT"]) <= 1800
    for name, job in [("js-tests.yml", "check"), ("rust-tests.yml", "bootstrap-installer"),
                      ("nix.yml", "flake-check")]:
        assert workflow(name)["jobs"][job]["runs-on"] == "ubuntu-latest"
