"""Forked unrelated work must not extend native drain transaction ownership."""
import os
import select

import pytest

from gateway import drain_control as dc

pytestmark = pytest.mark.platforms("posix")


def test_forked_child_cannot_extend_or_release_parent_ownership(tmp_path):
    ready_r, ready_w = os.pipe()
    release_r, release_w = os.pipe()
    child = None
    try:
        with dc.drain_marker_ownership(home=tmp_path) as ownership:
            marker = dc.write_drain_request(home=tmp_path, ownership=ownership)
            child = os.fork()
            if child == 0:
                try:
                    os.close(ready_r)
                    os.close(release_w)
                    try:
                        dc.clear_drain_request(home=tmp_path, ownership=ownership)
                    except ValueError:
                        os.write(ready_w, b'invalid')
                    else:
                        os.write(ready_w, b'escaped')
                    # Bounded even if the test parent fails unexpectedly.
                    if select.select([release_r], [], [], 10)[0]:
                        os.read(release_r, 1)
                finally:
                    os._exit(0)
            os.close(ready_w)
            ready_w = None
            os.close(release_r)
            release_r = None
            assert select.select([ready_r], [], [], 5)[0], 'child did not become ready'
            assert os.read(ready_r, 7) == b'invalid'
            # Child disposal must not unlock the still-active parent's flock.
            with pytest.raises(BlockingIOError):
                dc.clear_drain_request(home=tmp_path)
            assert dc.read_drain_request(home=tmp_path) == marker
        # The unrelated child remains alive, but transaction scope has ended.
        assert os.waitpid(child, os.WNOHANG) == (0, 0)
        assert dc.read_drain_request(home=tmp_path) == marker
        assert dc.clear_drain_request(home=tmp_path)
    finally:
        if child:
            os.write(release_w, b'x')
            assert os.waitpid(child, 0)[1] == 0
        for fd in (ready_r, ready_w, release_r, release_w):
            if fd is not None:
                os.close(fd)


def test_child_context_unwind_does_not_close_reused_descriptor(tmp_path):
    child = None
    replacement = None
    try:
        with dc.drain_marker_ownership(home=tmp_path) as ownership:
            original_fd = ownership.fd
            assert isinstance(original_fd, int)
            child = os.fork()
            if child == 0:
                # dup2 makes reuse deterministic, including on the RED source.
                replacement = os.open(tmp_path / 'child-file', os.O_CREAT | os.O_RDWR, 0o600)
                os.dup2(replacement, original_fd)
        if child == 0:
            try:
                os.fstat(original_fd)
            except OSError:
                os._exit(1)
            os._exit(0)
    finally:
        if child:
            assert os.waitpid(child, 0)[1] == 0
