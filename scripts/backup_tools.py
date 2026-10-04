"""Validate and stage a trusted SchoolNet backup without extracting unsafe paths."""
import argparse
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile


def entries(archive):
    with tarfile.open(archive, 'r|gz') as package:
        for member in package:
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or not (member.isdir() or member.isfile()):
                raise ValueError('Архів містить небезпечний шлях, посилання або спеціальний файл.')
            if name == PurePosixPath('.') and not member.isdir():
                raise ValueError('Некоректний кореневий запис архіву.')
            yield package, member, name


def validate(folder, legacy=False):
    folder = Path(folder)
    for filename in ('database.dump', 'media.tar.gz'):
        if not (folder / filename).is_file():
            raise ValueError(f'Відсутній обов’язковий файл {filename}.')
    with (folder / 'database.dump').open('rb') as dump:
        if dump.read(5) != b'PGDMP':
            raise ValueError('Очікується custom-format PostgreSQL dump.')
    manifest = folder / 'SHA256SUMS'
    if not manifest.is_file() and not legacy:
        raise ValueError('Відсутній SHA256SUMS. Для старої довіреної копії є --allow-legacy.')
    if manifest.is_file():
        checksums = {}
        for line in manifest.read_text().splitlines():
            digest, name = line.split(maxsplit=1)
            name = name.lstrip('*')
            if name not in {'database.dump', 'media.tar.gz'} or name in checksums:
                raise ValueError('Некоректний список контрольних сум.')
            checksums[name] = digest
        if set(checksums) != {'database.dump', 'media.tar.gz'}:
            raise ValueError('Неповний список контрольних сум.')
        for name, expected in checksums.items():
            with (folder / name).open('rb') as source:
                if hashlib.file_digest(source, 'sha256').hexdigest() != expected:
                    raise ValueError(f'Контрольна сума не збігається: {name}.')
    for _ in entries(folder / 'media.tar.gz'):
        pass


def stage(archive, media):
    media = Path(media)
    target = Path(tempfile.mkdtemp(prefix='.schoolnet-restore-', dir=media))
    try:
        for package, member, name in entries(archive):
            destination = target / str(name)
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with package.extractfile(member) as source, destination.open('xb') as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
                destination.chmod(0o644)
                os.utime(destination, (member.mtime, member.mtime))
    except BaseException:
        shutil.rmtree(target)
        raise
    print(target.name)


def install(media, staged):
    media = Path(media).resolve()
    target = media / staged
    if not staged.startswith('.schoolnet-restore-') or Path(staged).name != staged or not target.is_dir() or target.is_symlink():
        raise ValueError('Некоректний каталог підготовлених медіа.')
    old = Path(tempfile.mkdtemp(prefix='.schoolnet-previous-', dir=media))
    moved_old, moved_new = [], []
    try:
        for child in media.iterdir():
            if child not in (target, old):
                child.rename(old / child.name)
                moved_old.append(child.name)
        for child in target.iterdir():
            child.rename(media / child.name)
            moved_new.append(child.name)
    except BaseException:
        for name in reversed(moved_new):
            (media / name).rename(target / name)
        for name in reversed(moved_old):
            (old / name).rename(media / name)
        old.rmdir()
        raise
    target.rmdir()
    shutil.rmtree(old)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['validate', 'stage', 'install'])
    parser.add_argument('path')
    parser.add_argument('target', nargs='?')
    parser.add_argument('--allow-legacy', action='store_true')
    args = parser.parse_args()
    if args.action == 'validate':
        validate(args.path, args.allow_legacy)
    elif args.action == 'stage':
        stage(args.path, args.target)
    else:
        install(args.path, args.target)
