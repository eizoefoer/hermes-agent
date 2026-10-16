"""Selection receipts accept diagnostics, never arbitrary pytest configuration."""
import pytest

from tests.ci import workflow_steps


@pytest.mark.parametrize("options", [
    ["-o"],
    ["-o", "addopts=--ignore=tests/ci"],
    ["-o", "python_files=missing_*.py"],
    ["-o", "faulthandler_timeout=90", "--deselect=tests/ci/test_workflow_selection_options.py"],
])
def test_selection_rejects_unqualified_options(tmp_path, monkeypatch, options):
    path = tmp_path / "tests/ci/test_selected.py"
    path.parent.mkdir(parents=True)
    path.write_text("def test_selected(): pass\n", encoding="utf-8")
    monkeypatch.setattr(workflow_steps, "_run", lambda *a, **kw: (
        {}, [["pytest", "tests/ci/test_selected.py", *options]]))
    with pytest.raises(AssertionError):
        workflow_steps.selected_files({}, {}, tmp_path)


def test_diagnostic_option_preserves_selected_paths(tmp_path, monkeypatch):
    path = tmp_path / "tests/ci/test_selected.py"
    path.parent.mkdir(parents=True)
    path.write_text("def test_selected(): pass\n", encoding="utf-8")
    monkeypatch.setattr(workflow_steps, "_run", lambda *a, **kw: (
        {}, [["pytest", "tests/ci/test_selected.py", "-o", "faulthandler_timeout=90"]]))
    assert workflow_steps.selected_files({}, {}, tmp_path) == {"tests/ci/test_selected.py"}
