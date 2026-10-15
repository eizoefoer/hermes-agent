"""Approve complete protected-file proposals before the backend can mutate them."""

import copy
import hashlib

from tools.file_operations_common import PatchResult, ReadResult, WriteResult
from tools.patch_parser import apply_v4a_operations, parse_v4a_patch


class _PreviewFileOps:
    """Read-only in-memory adapter used to validate the complete V4A result."""

    def __init__(self, before: str):
        self.before = before
        self.after_content: str | None = None

    def read_file_raw(self, path):
        return ReadResult(content=self.before)

    def write_file(self, path, content, **kwargs):
        self.after_content = content
        return WriteResult(_content_sha256=hashlib.sha256(content.encode()).hexdigest())


def approved_replace(file_ops, target, path, old, new, replace_all, task_id):
    from tools.file_tools import _check_protected_instruction_write, _protected_instruction_reason
    if not _protected_instruction_reason(path, task_id):
        return file_ops.patch_replace(target, old, new, replace_all)

    grant = {}
    def approve(before, after):
        error = _check_protected_instruction_write(
            [path], task_id, {path: after}, {path: before}, grant)
        return error, grant.get("scoped", False)

    return file_ops.patch_replace(target, old, new, replace_all, approve_write=approve)


def approved_patch(file_ops, patch, rewritten, paths, resolved, task_id):
    from tools.file_tools import _check_protected_instruction_write, _protected_instruction_reason
    operations, error = parse_v4a_patch(patch)
    if error:
        return PatchResult(error=f"Failed to parse patch: {error}")
    protected = [op for op in operations if _protected_instruction_reason(op.file_path, task_id)]
    if not (protected and len(operations) == 1 and protected[0].operation.value == "update"):
        error = _check_protected_instruction_write(paths, task_id)
        return PatchResult(error=error) if error else file_ops.patch_v4a(rewritten)
    op = protected[0]
    target = resolved.get(op.file_path) or op.file_path
    before = file_ops.read_file_raw(target)
    if before.error or not isinstance(before.content, str):
        return PatchResult(error="Unable to establish exact before-state for protected instruction edit.")
    preview_ops = _PreviewFileOps(before.content)
    preview = apply_v4a_operations(copy.deepcopy(operations), preview_ops)
    if preview.error or preview_ops.after_content is None:
        return PatchResult(error=f"Unable to validate exact protected instruction patch: {preview.error or 'no candidate'}")
    grant = {}
    error = _check_protected_instruction_write(
        [op.file_path], task_id, {op.file_path: preview_ops.after_content}, {op.file_path: before.content}, grant)
    if error:
        return PatchResult(error=error)
    return file_ops.patch_v4a(rewritten, expected_content=before.content if grant.get("scoped", False) else ...)

def _read_complete_current_file(path: str, task_id: str) -> str | None:
    """Read untruncated backend content; inability to prove state grants no scope."""
    from tools.file_tools import _get_file_ops, _resolve_or_none

    try:
        resolved = _resolve_or_none(path, task_id)
        if resolved is None:
            return None
        result = _get_file_ops(task_id).read_file_raw(resolved)
        content = getattr(result, "content", None)
        return content if not getattr(result, "error", None) and isinstance(content, str) else None
    except (OSError, ValueError, RuntimeError):
        return None
