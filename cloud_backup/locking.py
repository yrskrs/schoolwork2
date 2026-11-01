"""Process-wide queue ownership on a local filesystem (Unix and Windows)."""
import contextlib
import os


@contextlib.contextmanager
def worker_lock(path):
    with path.open('a+b') as handle:
        if os.name=='nt':
            import msvcrt
            if handle.tell()==0:
                handle.write(b'0');handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:
                yield False;return
        else:
            import fcntl
            try:
                fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                yield False;return
        try:
            yield True
        finally:
            if os.name=='nt':
                handle.seek(0)
                msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
