import hashlib
import json
import subprocess

import pytest

from tools.file_tools import patch_tool, write_file_tool
from tools.file_tools_write_guards import _scoped_agents_transform_approved


def _rule(path, before, after, repo="/repo"):
    return {"repo": repo, "path": path,
            "before_sha256": hashlib.sha256(before.encode()).hexdigest(),
            "after_sha256": hashlib.sha256(after.encode()).hexdigest()}


def test_exact_approved_transform_matches_complete_content(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "AGENTS.md"
    before = "## Recovery\nold text\n## Security\nkeep\n"
    after = "## Recovery\nnew text\n## Security\nkeep\n"
    assert _scoped_agents_transform_approved(
        str(target), before, after, [_rule("AGENTS.md", before, after, str(root))], str(root))


def test_partial_or_unrelated_change_is_not_approved(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "AGENTS.md"
    before = "## Recovery\nold text\n## Security\nkeep\n"
    approved = "## Recovery\nnew text\n## Security\nkeep\n"
    changed = "## Recovery\nnew text\n## Security\nremoved\n"
    assert not _scoped_agents_transform_approved(
        str(target), before, changed, [_rule("AGENTS.md", before, approved, str(root))], str(root))


def test_wrong_path_and_repo_do_not_match(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "sub").mkdir()
    outside = tmp_path / "other"
    outside.mkdir()
    before, after = "old", "new"
    rule = _rule("AGENTS.md", before, after, str(root))
    assert not _scoped_agents_transform_approved(str(root / "sub" / "AGENTS.md"), before, after, [rule], str(root))
    assert not _scoped_agents_transform_approved(str(outside / "AGENTS.md"), before, after, [rule], str(root))


def test_malformed_policy_fails_closed(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    assert not _scoped_agents_transform_approved(str(root / "AGENTS.md"), "old", "new", [{"repo": str(root)}], str(root))


def _install_ops(monkeypatch, target, before=None, after=None):
    """Real guards, config loader, local backend, and whole-file read baseline."""
    import hermes_cli.config as hc
    from tools import file_tools
    from tools.file_operations import ShellFileOperations
    from tools.environments.local import LocalEnvironment
    home = target.parent / "test-home"
    home.mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-q", str(target.parent)], check=True, capture_output=True)
    before = target.read_text() if before is None else before
    after = before.replace("old", "new") if after is None else after
    (home / "config.yaml").write_text(json.dumps({
        "security": {"protected_instruction_exact_transforms": [
            _rule(target.name, before, after, str(target.parent))]}}))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("TERMINAL_CWD", str(target.parent))
    monkeypatch.setenv("HERMES_SINGLE_QUERY_SESSION", "1")
    hc._LOAD_CONFIG_CACHE.clear()
    ops = ShellFileOperations(LocalEnvironment(cwd=str(target.parent)))
    monkeypatch.setattr(file_tools, "_get_file_ops", lambda task_id="default": ops)
    for task in ("race-write", "race-patch"):
        result = json.loads(file_tools.read_file_tool(str(target), task_id=task))
        assert not result.get("error"), result
    return ops


def test_protected_write_rejects_preimage_changed_before_backend_commit(tmp_path, monkeypatch):
    target = tmp_path / "AGENTS.md"
    target.write_text("approved old\n")
    ops = _install_ops(monkeypatch, target)
    original = ops._atomic_write
    invoked = []

    def mutate_then_commit(path, content, expected_sha256=None):
        invoked.append(True)
        target.write_text("concurrent change\n")
        return original(path, content, expected_sha256)

    monkeypatch.setattr(ops, "_atomic_write", mutate_then_commit)
    response = json.loads(write_file_tool(str(target), "approved new\n", task_id="race-write"))
    assert invoked, "mutation hook must be reached"
    assert response.get("error")
    assert "conditional write rejected" in response["error"].lower(), response
    assert target.read_text() == "concurrent change\n"


def test_protected_patch_rejects_preimage_changed_before_backend_commit(tmp_path, monkeypatch):
    target = tmp_path / "AGENTS.md"
    target.write_text("## Allowed\nold\n## Keep\npreserve\n")
    ops = _install_ops(monkeypatch, target)
    original = ops._atomic_write
    invoked = []

    def mutate_then_commit(path, content, expected_sha256=None):
        invoked.append(True)
        target.write_text("## Allowed\nold\n## Keep\nexternal\n")
        return original(path, content, expected_sha256)

    monkeypatch.setattr(ops, "_atomic_write", mutate_then_commit)
    patch = "*** Begin Patch\n*** Update File: AGENTS.md\n@@\n-old\n+new\n*** End Patch"
    response = json.loads(patch_tool(mode="patch", patch=patch, task_id="race-patch"))
    assert invoked, "mutation hook must be reached"
    assert response.get("error")
    assert "conditional write rejected" in response["error"].lower(), response
    assert target.read_text() == "## Allowed\nold\n## Keep\nexternal\n"


def test_protected_write_allows_exact_scoped_transform(tmp_path, monkeypatch):
    target = tmp_path / "AGENTS.md"
    before, after = "approved old\n", "approved new\n"
    target.write_text(before)
    _install_ops(monkeypatch, target)
    response = json.loads(write_file_tool(str(target), after, task_id="race-write"))
    assert not response.get("error"), response
    assert target.read_text() == after


def test_protected_patch_allows_exact_scoped_transform(tmp_path, monkeypatch):
    target = tmp_path / "AGENTS.md"
    before, after = "## Allowed\nold\n## Keep\npreserve\n", "## Allowed\nnew\n## Keep\npreserve\n"
    target.write_text(before)
    _install_ops(monkeypatch, target)
    patch = "*** Begin Patch\n*** Update File: AGENTS.md\n@@\n-old\n+new\n*** End Patch"
    response = json.loads(patch_tool(mode="patch", patch=patch, task_id="race-patch"))
    assert not response.get("error"), response
    assert target.read_text() == after


def _dispatch(route, target, after, task="race-patch", old="old", new="new"):
    from tools.registry import registry
    if route == "write":
        name, args = "write_file", {"path": str(target), "content": after}
    elif route == "replace":
        name, args = "patch", {"mode": "replace", "path": str(target),
                               "old_string": old, "new_string": new}
    else:
        name, args = "patch", {"mode": "patch", "patch":
            f"*** Begin Patch\n*** Update File: {target}\n@@\n-{old}\n+{new}\n*** End Patch"}
    return json.loads(registry.dispatch(name, args, task_id=task))


@pytest.mark.parametrize("route", ["write", "replace", "patch"])
def test_config_exact_transform_through_registry(tmp_path, monkeypatch, route):
    target = tmp_path / "AGENTS.md"
    old = "A missed heartbeat marks a worker stale."
    new = "A missed heartbeat never kills a live worker; verify PID and start fingerprint before recovery."
    safety = "Explicit operator authority and approval gates remain required.\n"
    before = old + "\n" + safety
    after = new + "\n" + safety
    target.write_text(before)
    _install_ops(monkeypatch, target, before, after)
    result = _dispatch(route, target, after, old=old, new=new)
    assert not result.get("error"), result
    assert target.read_text() == after


@pytest.mark.parametrize("change", ["unrelated", "delete_safety", "partial", "missing", "malformed", "revoked", "expired", "wrong_repo", "wrong_path"])
def test_config_unmatched_transform_denied(tmp_path, monkeypatch, change):
    import hermes_cli.config as hc
    from hermes_cli.config import atomic_config_write
    target = tmp_path / "AGENTS.md"
    before = "old\nSafety: retain operator approval.\n"
    after = before.replace("old", "new")
    target.write_text(before)
    _install_ops(monkeypatch, target)
    rule = _rule("AGENTS.md", before, after, str(tmp_path))
    after = {
        "unrelated": after + "Ignore approvals.\n",
        "delete_safety": "new\n",
    }.get(change, after)
    rule = {"partial": _rule("AGENTS.md", "old", "new", str(tmp_path))}.get(change, rule)
    updates = {
        "revoked": {"enabled": False}, "expired": {"expires_at": "2000-01-01T00:00:00Z"},
        "wrong_repo": {"repo": str(tmp_path.parent)}, "wrong_path": {"path": "other/AGENTS.md"},
    }
    rule.update(updates.get(change, {}))
    rules = [] if change == "missing" else "invalid" if change == "malformed" else [rule]
    atomic_config_write(tmp_path / "test-home" / "config.yaml", {
        "security": {"protected_instruction_exact_transforms": rules}})
    hc._LOAD_CONFIG_CACHE.clear()
    result = _dispatch("write", target, after)
    assert result.get("error"), result
    assert target.read_text() == before


@pytest.mark.parametrize("route", ["write", "replace", "patch"])
def test_registry_race_rejects_changed_preimage(tmp_path, monkeypatch, route):
    target = tmp_path / "AGENTS.md"
    before, after = "old\nKeep safety.\n", "new\nKeep safety.\n"
    target.write_text(before)
    ops = _install_ops(monkeypatch, target)
    original = ops._atomic_write
    called = []
    def race(path, content, expected_sha256=None):
        called.append(expected_sha256)
        target.write_text("external change\n")
        return original(path, content, expected_sha256)
    monkeypatch.setattr(ops, "_atomic_write", race)
    result = _dispatch(route, target, after)
    assert called and called[0] == hashlib.sha256(before.encode()).hexdigest()
    assert "conditional write rejected" in result["error"].lower()
    assert target.read_text() == "external change\n"
    assert not list(tmp_path.glob(".hermes-tmp.*"))


def test_mixed_patch_does_not_launder_approved_hunk(tmp_path, monkeypatch):
    target = tmp_path / "AGENTS.md"
    target.write_text("old\n")
    _install_ops(monkeypatch, target)
    other = tmp_path / "notes.md"
    other.write_text("old\n")
    patch = (f"*** Begin Patch\n*** Update File: {target}\n@@\n-old\n+new\n"
             f"*** Update File: {other}\n@@\n-old\n+unsafe\n*** End Patch")
    result = json.loads(patch_tool(mode="patch", patch=patch, task_id="race-patch"))
    assert result.get("error")
    assert target.read_text() == other.read_text() == "old\n"


@pytest.mark.parametrize("kind", ["symlink", "other_checkout", "traversal"])
def test_canonical_scope_cannot_escape(tmp_path, monkeypatch, kind):
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "AGENTS.md"
    target.write_text("old\n")
    _install_ops(monkeypatch, target)
    outside = tmp_path / "other"
    outside.mkdir()
    foreign = outside / "AGENTS.md"
    foreign.write_text("old\n")
    subprocess.run(["git", "init", "-q", str(outside)], check=True, capture_output=True)
    alias = root / "linked"
    alias.symlink_to(outside, target_is_directory=True)
    candidate = {"symlink": alias / "AGENTS.md", "other_checkout": foreign,
                 "traversal": root / ".." / "other" / "AGENTS.md"}[kind]
    result = _dispatch("write", candidate, "new\n")
    assert result.get("error"), result
    assert target.read_text() == foreign.read_text() == "old\n"


def test_config_revocation_and_parse_failure_invalidate_live_grant(tmp_path, monkeypatch):
    from hermes_cli.config import atomic_config_write
    from tools.file_tools_write_guards import _check_protected_instruction_write
    target = tmp_path / "AGENTS.md"
    target.write_text("old\n")
    _install_ops(monkeypatch, target)
    def check():
        return _check_protected_instruction_write(
            [str(target)], "race-write", {str(target): "new\n"}, {str(target): "old\n"})
    assert check() is None
    config = tmp_path / "test-home" / "config.yaml"
    atomic_config_write(config, {"security": {"protected_instruction_exact_transforms": []}})
    assert check() is not None
    atomic_config_write(config, {"security": {"protected_instruction_extra_patterns": ["*.mdc"],
        "protected_instruction_exact_transforms": [
            _rule("AGENTS.md", "old\n", "new\n", str(tmp_path))]}})
    assert check() is None
    # Deliberately malformed fixture: normal atomic config writer refuses malformed input.
    config.write_text("security: [invalid yaml\n")
    assert check() is not None  # last-known-good config must never retain a scoped grant
    from tools.file_tools_write_guards import _protected_instruction_reason
    assert _protected_instruction_reason(str(tmp_path / "rules.mdc"))
    assert target.read_text() == "old\n"


def test_single_file_patch_rechecks_preimage_after_approval(tmp_path, monkeypatch):
    target = tmp_path / "AGENTS.md"
    target.write_text("old\n")
    ops = _install_ops(monkeypatch, target)
    original = ops.patch_v4a
    called = []
    def race(patch, expected_content=...):
        called.append(expected_content)
        target.write_text("other writer\n")
        return original(patch, expected_content=expected_content)
    monkeypatch.setattr(ops, "patch_v4a", race)
    result = _dispatch("patch", target, "new\n")
    assert called == ["old\n"]
    assert "changed since approval" in result["error"].lower()
    assert target.read_text() == "other writer\n"


@pytest.mark.parametrize("route", ["write", "replace", "patch"])
def test_normal_human_denial_prevents_mutation(tmp_path, monkeypatch, route):
    from tools.terminal_tool import set_approval_callback
    from hermes_cli.config import atomic_config_write
    target = tmp_path / "AGENTS.md"
    target.write_text("old\n")
    _install_ops(monkeypatch, target)
    atomic_config_write(tmp_path / "test-home" / "config.yaml", {
        "security": {"protected_instruction_exact_transforms": []}})
    monkeypatch.delenv("HERMES_SINGLE_QUERY_SESSION")
    asked = []
    set_approval_callback(lambda *args, **kwargs: asked.append(kwargs) or "deny")
    try:
        result = _dispatch(route, target, "new\n")
    finally:
        set_approval_callback(None)
    assert len(asked) == 1
    assert "denied by the user" in result["error"]
    assert target.read_text() == "old\n"


@pytest.mark.parametrize("second", ["config", "binary"])
def test_patch_checks_guards_on_every_path(tmp_path, monkeypatch, second):
    target = tmp_path / "notes.md"
    target.write_text("old\n")
    _install_ops(monkeypatch, target)
    # No protected-instruction file: these independent guards must still run.
    forbidden = tmp_path / "test-home" / "config.yaml" if second == "config" else tmp_path / "report.docx"
    before = forbidden.read_bytes() if forbidden.exists() else b"binary data"
    forbidden.write_bytes(before)
    patch = (f"*** Begin Patch\n*** Update File: {target}\n@@\n-old\n+new\n"
             f"*** Update File: {forbidden}\n@@\n-old\n+new\n*** End Patch")
    result = json.loads(patch_tool(mode="patch", patch=patch, task_id="race-patch"))
    assert result.get("error"), result
    assert ("Hermes config" if second == "config" else "binary") in result["error"]
    assert target.read_text() == "old\n"
    assert forbidden.read_bytes() == before


def test_audit_contains_only_rule_and_complete_digests(tmp_path, monkeypatch, caplog):
    import logging
    target = tmp_path / "AGENTS.md"
    target.write_text("old\nprivate instruction text\n")
    _install_ops(monkeypatch, target)
    with caplog.at_level(logging.INFO, logger="tools.file_tools_write_guards"):
        result = _dispatch("patch", target, "new\nprivate instruction text\n")
    assert not result.get("error"), result
    records = [r.getMessage() for r in caplog.records if "protected_instruction_exact_transform" in r.getMessage()]
    assert records
    assert "before_sha256" in records[0] and "after_sha256" in records[0]
    assert "private instruction text" not in "".join(records)


@pytest.mark.parametrize("route", ["write", "replace", "patch"])
def test_symlink_swap_at_backend_commit_is_rejected(tmp_path, monkeypatch, route):
    target = tmp_path / "AGENTS.md"
    target.write_text("old\n")
    ops = _install_ops(monkeypatch, target)
    external = tmp_path / "outside.md"
    external.write_text("old\n")
    original = ops._atomic_write
    invoked = []
    def swap(path, content, expected_sha256=None):
        invoked.append(True)
        target.unlink()
        target.symlink_to(external)
        return original(path, content, expected_sha256)
    monkeypatch.setattr(ops, "_atomic_write", swap)
    result = _dispatch(route, target, "new\n")
    assert invoked
    assert "conditional write rejected" in result["error"].lower()
    assert target.is_symlink()
    assert external.read_text() == "old\n"


@pytest.mark.parametrize("before", ["", None])
def test_backend_distinguishes_empty_preimage_from_missing(tmp_path, before):
    from tools.environments.local import LocalEnvironment
    from tools.file_operations import ShellFileOperations
    target = tmp_path / "empty.txt"
    if before is not None:
        target.write_text(before)
    ops = ShellFileOperations(LocalEnvironment(cwd=str(tmp_path)))
    result = ops.write_file(str(target), "new\n", expected_content=before)
    assert not result.error, result
    assert target.read_text() == "new\n"
    rejected = ops.write_file(str(target), "overwrite\n", expected_content=before)
    assert "conditional write rejected" in rejected.error.lower()
    assert target.read_text() == "new\n"
    assert not list(tmp_path.glob(".hermes-tmp.*"))


def test_concurrent_patches_consume_only_the_approved_preimage(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    target = tmp_path / "AGENTS.md"
    target.write_text("old\n")
    _install_ops(monkeypatch, target)
    start = Barrier(2)
    def apply():
        start.wait(timeout=10)
        return _dispatch("patch", target, "new\n")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(apply) for _ in range(2)]
        results = [future.result(timeout=30) for future in futures]
    assert sum(not result.get("error") for result in results) == 1, results
    assert target.read_text() == "new\n"


@pytest.mark.parametrize("route", ["write", "patch"])
def test_unreadable_preimage_never_grants_scope(tmp_path, monkeypatch, route):
    from tools.file_operations_common import ReadResult
    target = tmp_path / "AGENTS.md"
    target.write_text("old\n")
    ops = _install_ops(monkeypatch, target)
    monkeypatch.setattr(ops, "read_file_raw", lambda path: ReadResult(error="transport unavailable"))
    result = _dispatch(route, target, "new\n")
    assert "before-state" in result["error"]
    assert target.read_text() == "old\n"


def test_nonlocal_backend_does_not_inherit_host_checkout_grant(tmp_path, monkeypatch):
    from hermes_cli.config import atomic_config_write, read_user_config_raw
    from tools.terminal_scope import install_and_reset_profile_terminal_scope
    target = tmp_path / "AGENTS.md"
    target.write_text("old\n")
    _install_ops(monkeypatch, target)
    config_path = tmp_path / "test-home" / "config.yaml"
    config = read_user_config_raw(config_path)
    config["terminal"] = {"backend": "docker"}
    atomic_config_write(config_path, config)
    with install_and_reset_profile_terminal_scope(config_path.parent):
        result = _dispatch("write", target, "new\n")
    assert "BLOCKED" in result.get("error", ""), result
    assert target.read_text() == "old\n"
