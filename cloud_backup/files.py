"""File reads detect concurrent mutation; restores never overwrite existing files."""
import contextlib
import io
import os
from pathlib import Path
from .archive import safe_path


def bytes_source(data):
    return lambda: contextlib.closing(io.BytesIO(data))


def file_source(path):
    path = Path(path)
    @contextlib.contextmanager
    def source():
        if path.is_symlink():
            raise ValueError('Посилання замість файла не резервуються')
        with path.open('rb') as stream:
            before = os.fstat(stream.fileno())
            if not __import__('stat').S_ISREG(before.st_mode):
                raise ValueError('Спеціальний файл не резервується')
            yield stream
            after = path.stat()
            if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                raise ValueError('Файл змінився під час резервування. Повторіть запуск')
    return source


def destination(root, name):
    safe_path(name)
    root = Path(root).resolve()
    target = root/name
    if not target.resolve().is_relative_to(root) or any(p.is_symlink() for p in [target, *target.parents] if p != root.parent):
        raise ValueError('Небезпечний шлях файла')
    return target


@contextlib.contextmanager
def install_files(pairs):
    """Files become visible before DB commit; remove them if DB work fails.

    Only a fresh installation is accepted. Existing content is never overwritten.
    """
    import shutil
    created = []
    try:
        for source, target in pairs:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as out, source.open('rb') as inp:
                created.append(target)
                shutil.copyfileobj(inp, out)
        yield
    except BaseException:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise
