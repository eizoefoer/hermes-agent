"""Per-operation preconditions without shared mutable backend state or nested locks."""

import hashlib

from tools.file_operations_common import ReadResult, WriteResult


def expected_digest(content):
    if content is ...:
        return None
    if content is None:
        return "__MISSING__"
    if not isinstance(content, str):
        raise ValueError("Conditional write requires a verified text preimage.")
    return hashlib.sha256(content.encode("utf-8", "surrogateescape")).hexdigest()


class ConditionalPatchOps:
    def __init__(self, delegate, before):
        self.delegate = delegate
        self.before = before

    def read_file_raw(self, path):
        result = self.delegate.read_file_raw(path)
        if result.error or result.content != self.before:
            return ReadResult(error="Conditional patch rejected: target changed since approval.")
        return result

    def write_file(self, path, content, pre_content=None):
        if pre_content is not None and pre_content != self.before:
            return WriteResult(error="Conditional patch rejected: target changed since approval.")
        return self.delegate.write_file(path, content, pre_content=pre_content,
                                        expected_content=self.before)
