"""Fingerprint runtime source only; never enumerate private configuration/data."""
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = ('Dockerfile', 'compose.yaml', 'compose.integration.yaml', 'VERSION',
              'requirements.txt', 'requirements-docker.txt', 'requirements-docker.lock', 'gunicorn.conf.py', 'manage.py')
SOURCE_ROOTS = ('feed', 'schoolnet', 'school_sync', 'runner', 'static', 'templates', 'scripts')
EXTENSIONS = {'.py','.html','.js','.css','.ttf','.txt'}


def source_fingerprint(root=ROOT):
    files = [root / name for name in ROOT_FILES if (root/name).is_file()]
    for directory in SOURCE_ROOTS:
        files.extend(path for path in (root/directory).rglob('*') if path.is_file()
                     and path.suffix in EXTENSIONS and '__pycache__' not in path.parts
                     and not any(part.startswith('.') for part in path.relative_to(root).parts))
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(str(path.relative_to(root)).encode()+b'\0')
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


if __name__ == '__main__':
    print(source_fingerprint())
