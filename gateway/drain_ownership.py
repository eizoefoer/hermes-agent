"""POSIX cooperative mutation ownership for the native drain marker.

Readers remain lock-free. Unaware/direct filesystem writers are outside this
boundary. The lock file is persistent: unlinking it would split lock ownership.
This is not authorization, process settlement or gateway activation permission.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import stat

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
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    ownership = _Ownership(path, fd)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('drain ownership lock is not a regular file')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ownership.validate(home)
        yield ownership
    finally:
        # close releases flock; never unlink the shared lock inode or marker.
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
