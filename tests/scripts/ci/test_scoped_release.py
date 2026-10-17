"""Fail-closed contracts for release result parsing and full-tree provenance."""
import importlib.util
import subprocess

import pytest

SPEC = importlib.util.spec_from_file_location("scoped_release", "scripts/ci/scoped_release.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


@pytest.mark.parametrize("skips", ["", ", 456 skipped"])
def test_actual_runner_summary(skips):
    text = f"=== Summary: 3121 files, 28017 tests passed, 0 failed{skips} (100% complete) in 3007.8s (4 workers) ==="
    assert module.parse_summary(text) == {
        "files": 3121, "passed": 28017, "failed": 0, "skipped": 456 if skips else 0}


@pytest.mark.parametrize("files,passed,failed", [(0, 1, 0), (1, 0, 0), (1, 1, 1)])
def test_rejects_empty_or_failed_results(files, passed, failed):
    line = f"=== Summary: {files} files, {passed} tests passed, {failed} failed (100% complete) in 1s (4 workers) ==="
    with pytest.raises(ValueError):
        module.parse_summary(line)


@pytest.mark.parametrize("text", ["1 file timed out", "", "=== Summary: 1 files, 1 tests passed, 0 failed (50% complete) in 1s ==="])
def test_rejects_incomplete_results(text):
    with pytest.raises(ValueError):
        module.parse_summary(text)


def test_rejects_multiple_summaries():
    summary = "=== Summary: 1 files, 1 tests passed, 0 failed (100% complete) in 1s ===\n"
    with pytest.raises(ValueError):
        module.parse_summary(summary * 2)


def test_valid_test_names_are_not_errors():
    summary = "=== Summary: 1 files, 1 tests passed, 0 failed (100% complete) in 1s ==="
    assert module.parse_summary("test_error_timeout.py passed\n" + summary)["passed"] == 1


def test_provenance_rejects_wrong_source_and_dirty_tree(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], check=True)
    subprocess.run(["git", "config", "user.name", "Acceptance test"], check=True)
    subprocess.run(["git", "config", "user.email", "acceptance@example.invalid"], check=True)
    path = tmp_path / "contract.txt"
    path.write_text("approved\n")
    subprocess.run(["git", "add", "contract.txt"], check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], check=True)
    sha = module.git("rev-parse", "HEAD")
    manifest = {"compatibility_baseline": sha, "source_feature_head": sha,
                "source_stack": [{"head": sha}], "default_main_snapshot": sha,
                "required_paths": ["contract.txt"]}
    receipt = module.prepare(manifest, sha)
    assert receipt["source_sha"] == sha
    assert receipt["tree_sha"] == module.git("rev-parse", "HEAD^{tree}")
    with pytest.raises(ValueError, match="expected SHA"):
        module.prepare(manifest, "0" * 40)
    injected = tmp_path / "untracked_import.py"
    injected.write_text("value = 'not in accepted tree'\n")
    with pytest.raises(ValueError, match="untracked"):
        module.prepare(manifest, sha)
    injected.unlink()
    path.write_text("unapproved\n")
    with pytest.raises(subprocess.CalledProcessError):
        module.prepare(manifest, sha)


@pytest.mark.parametrize("field,value", [
    ("tree_sha", "forged-tree"), ("run_id", "another-run"),
    ("manifest_sha256", "forged-manifest"), ("source_sha", "another-sha"),
])
def test_receipt_binding_rejects_consistent_but_wrong_evidence(field, value):
    import hashlib
    manifest = b'{"source": "approved"}'
    receipt = {"tree_sha": "tree", "run_id": "run", "source_sha": "sha",
               "manifest_sha256": hashlib.sha256(manifest).hexdigest()}
    module.verify_source_binding(receipt, "sha", "tree", "run", manifest)
    receipt[field] = value
    with pytest.raises(ValueError, match="binding"):
        module.verify_source_binding(receipt, "sha", "tree", "run", manifest)


def test_shared_selection_is_exact_explicit_list(tmp_path):
    manifest = {"required_paths": ["tests/a.py", "tests/b.py"]}
    output = tmp_path / "selection.txt"
    assert module.selection(manifest, "shared", output) == ["--files-from", str(output)]
    assert output.read_text().splitlines() == manifest["required_paths"]
