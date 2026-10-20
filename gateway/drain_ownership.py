"""POSIX cooperative mutation ownership for the native drain marker.

Readers remain lock-free. Unaware/direct filesystem writers are outside this
boundary. The lock file is persistent: unlinking it would split lock ownership.
This is not authorization, process settlement or gateway activation permission.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import stat
import threading


# Serialize descriptor publication/closure with Python's fork boundary. CLOEXEC
# alone leaves a fork-only child holding the parent's flock open description.
_ownerships = set()
_descriptor_lock = threading.Lock()


def _after_fork_child():
    try:
        for ownership in _ownerships:
            fd, ownership.fd = ownership.fd, None
            os.close(fd)
        _ownerships.clear()
    finally:
        _descriptor_lock.release()


if hasattr(os, 'register_at_fork'):
    os.register_at_fork(before=_descriptor_lock.acquire,
                        after_in_parent=_descriptor_lock.release,
                        after_in_child=_after_fork_child)

try:
    import fcntl
except ImportError:  # Existing marker operations retain their non-POSIX behavior.
    fcntl = None


class _Ownership:
    def __init__(self, path, fd):
        self.path = path
        self.fd: int | None = fd
        self.pid = os.getpid()

    def validate(self, home):
        if self.fd is None or self.pid != os.getpid() or self.path.parent != Path(home).resolve():
            raise ValueError('drain ownership is released, inherited or for a different home')
        opened = os.fstat(self.fd)
        current = self.path.lstat()
        if (not stat.S_ISREG(current.st_mode) or
                (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)):
            raise ValueError('drain ownership lock path changed')


@contextmanager
def drain_marker_ownership(*, home):
    """Hold native marker mutations until exit; never change/clear the marker.

    Nonblocking, exclusive across processes and independent same-process calls.
    Pass the yielded capability explicitly to native write/clear operations.
    Owner death releases the lock but leaves marker/transaction effects unknown.
    Unsupported platforms refuse this optional ownership API.
    """
    if fcntl is None:
        raise NotImplementedError('drain marker ownership requires POSIX flock')
    home = Path(home).resolve()
    home.mkdir(parents=True, exist_ok=True)
    path = home / '.drain_request.lock'
    with _descriptor_lock:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        ownership = _Ownership(path, fd)
        _ownerships.add(ownership)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('drain ownership lock is not a regular file')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ownership.validate(home)
        yield ownership
    finally:
        # close releases flock; never unlink the shared lock inode or marker.
        with _descriptor_lock:
            _ownerships.discard(ownership)
            if ownership.fd is not None:
                ownership.fd = None
                os.close(fd)


@contextmanager
def marker_mutation(*, home, ownership=None):
    if ownership is not None:
        if not isinstance(ownership, _Ownership):
            raise ValueError('invalid drain ownership capability')
        ownership.validate(home)
        yield
    elif fcntl is None:
        yield
    else:
        with drain_marker_ownership(home=home):
            yield
