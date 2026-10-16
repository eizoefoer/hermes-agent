"""Regression tests for contributor attribution comparison bases."""

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github/workflows/contributor-check.yml"


def _check_script() -> str:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    marker = "        run: |\n"
    start = workflow.index(marker, workflow.index("id: check-emails")) + len(marker)
    lines = workflow[start:].splitlines()
    body = []
    for line in lines:
        if line and not line.startswith("          "):
            break
        body.append(line[10:] if line.startswith("          ") else "")
    return "\n".join(body)


def _commit(repo: Path, message: str, email: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": message, "GIT_AUTHOR_EMAIL": email,
           "GIT_COMMITTER_NAME": message, "GIT_COMMITTER_EMAIL": email}
    subprocess.run(["git", "-C", str(repo), "commit", "--allow-empty", "-m", message],
                   check=True, env=env, capture_output=True, text=True)
    return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()


@pytest.fixture
def divergent_history(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"], check=True)
    root = _commit(repo, "base", "base@example.invalid")
    main_only = _commit(repo, "fork main history", "fork@example.invalid")
    subprocess.run(["git", "-C", str(repo), "checkout", "-b", "feature", root], check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repo), "update-ref", "refs/remotes/origin/main", main_only], check=True)
    base = _commit(repo, "existing upstream history", "historical@example.invalid")
    mappings = repo / "contributors" / "emails"
    mappings.mkdir(parents=True)
    (mappings / "mapped@example.invalid").write_text("fixture", encoding="utf-8")
    mapped = _commit(repo, "mapped feature", "mapped@example.invalid")
    return repo, base, main_only, mapped


def _run_check(repo: Path, *, pr_base: str = "", comparison_base: str = "") -> subprocess.CompletedProcess:
    output = repo / "github-output"
    output.write_text("", encoding="utf-8")
    return subprocess.run(["bash", "-e", "-o", "pipefail", "-c", _check_script()], cwd=repo,
                          check=False, env={**os.environ, "PR_BASE_SHA": pr_base,
                               "PR_HEAD_SHA": "", "COMPARISON_BASE": comparison_base,
                               "GITHUB_OUTPUT": str(output)}, capture_output=True, text=True)


def test_explicit_comparison_base_excludes_divergent_main_and_still_fails_new_email(divergent_history):
    repo, base, _, _ = divergent_history
    assert _run_check(repo, comparison_base=base).returncode == 0
    _commit(repo, "new unmapped feature", "new-person@example.invalid")
    result = _run_check(repo, comparison_base=base)
    assert result.returncode == 1
    assert "new-person@example.invalid" in result.stdout
    assert "historical@example.invalid" not in result.stdout


def test_pr_event_base_precedes_explicit_comparison_base(divergent_history):
    repo, base, _, _ = divergent_history
    _commit(repo, "new unmapped feature", "new-person@example.invalid")
    result = _run_check(repo, pr_base=base, comparison_base="HEAD")
    assert result.returncode == 1
    assert "new-person@example.invalid" in result.stdout


def test_default_comparison_remains_origin_main(divergent_history):
    repo, _, _, _ = divergent_history
    result = _run_check(repo)
    assert result.returncode == 1
    assert "historical@example.invalid" in result.stdout
