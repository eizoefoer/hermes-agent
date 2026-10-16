"""Spawn callback invocation and failure accounting for the native dispatcher."""
from __future__ import annotations

import inspect


def call_spawn_fn(spawn_fn, task, workspace, board):
    """Never retry an admitted callback because its own body raised ValueError."""
    try:
        sig = inspect.signature(spawn_fn)
    except (TypeError, ValueError):
        return spawn_fn(task, workspace)
    if "board" in sig.parameters:
        return spawn_fn(task, workspace, board=board)
    return spawn_fn(task, workspace)


def record_spawn_failure(conn, claimed, exc, result, failure_limit, recovery_policy):
    """Preserve the native infrastructure exemption and optional retry receipts."""
    from hermes_cli import kanban_db_dispatch as dispatch
    from hermes_cli import kanban_db as kb
    from tools.process_registry import RestartSafeScopeUnavailable

    infrastructure = isinstance(exc, RestartSafeScopeUnavailable)
    if infrastructure:
        kb._log.warning("kanban dispatcher: spawn of %s deferred, host cannot place the worker: %s", claimed.id, exc)
    if dispatch._record_task_failure(
        conn, claimed.id, str(exc), outcome="spawn_failed", failure_limit=failure_limit,
        release_claim=True, end_run=True, infrastructure=infrastructure,
    ):
        result.auto_blocked.append(claimed.id)
    if recovery_policy is not None:
        from hermes_cli.kanban_db_recovery import observe_failures
        result.recovery_observations = observe_failures(conn, recovery_policy)


def resolve_default_assignee(default_assignee):
    """Resolve the configured default through the dispatch profile gate."""
    from hermes_cli import kanban_db_dispatch as dispatch

    name = (default_assignee or "").strip() or None
    if name:
        profile_exists = dispatch._profile_exists_fn()
        if profile_exists is not None and not profile_exists(name):
            return None
    return name
