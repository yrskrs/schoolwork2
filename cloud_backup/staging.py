import contextlib
import tempfile
from pathlib import Path
from .archive import safe_path


@contextlib.contextmanager
def stage(root, entries):
    with tempfile.TemporaryDirectory(prefix='restore-', dir=root) as temp:
        directory = Path(temp)
        for name, data in entries:
            safe_path(name)
            target = directory/name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        yield directory


@contextlib.contextmanager
def recovery(root, repo, snapshot, scope):
    with tempfile.TemporaryDirectory(prefix='restore-', dir=root) as temp:
        directory = Path(temp)
        repo.extract(snapshot, directory, scope)
        yield directory
