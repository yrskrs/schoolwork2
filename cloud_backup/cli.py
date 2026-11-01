"""Offline verification/recovery of an assembled ZIP on a fresh installation.

python -m cloud_backup.cli --application schooltest --owner 1 --scope server \
    --archive /private/backup.zip --password-prompt --verify-only
"""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

import pyzipper
from .archive import CHUNK, MAX_MANIFEST, MAX_TOTAL, file_digest, safe_path


def extract_portable(archive, directory, password=''):
    with pyzipper.AESZipFile(archive) as z:
        if password: z.setpassword(password.encode())
        infos=z.infolist()
        if len(infos)>500001 or sum(i.file_size for i in infos)>MAX_TOTAL+MAX_MANIFEST:
            raise ValueError('Перевищено ліміт розпакування')
        seen=set()
        for info in infos:
            name=safe_path(info.filename)
            if name in seen or info.is_dir() or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Недопустимий або повторний файл')
            seen.add(name)
            path=Path(directory)/name;path.parent.mkdir(parents=True,exist_ok=True)
            with z.open(info) as inp,path.open('xb') as out:
                shutil.copyfileobj(inp,out,CHUNK)
        if 'backup-manifest.json' in seen:
            manifest_path=Path(directory)/'backup-manifest.json'
            if manifest_path.stat().st_size>MAX_MANIFEST: raise ValueError('Завеликий маніфест')
            manifest=json.loads(manifest_path.read_text())
            if manifest.get('format')!='zip':
                raise ValueError('Це опис інкрементальної копії. Спочатку зберіть повний ZIP через сайт')
            if set(manifest['files']) != seen-{'backup-manifest.json'}:
                raise ValueError('Склад архіву не відповідає маніфесту')
            for name,info in manifest['files'].items():
                path=Path(directory)/safe_path(name)
                if path.stat().st_size!=info['size'] or file_digest(path)!=info['sha256']:
                    raise ValueError('Контрольна сума файла не збігається')


def main():
    parser=argparse.ArgumentParser(description='Перевірка та відновлення ZIP лише на новому встановленні сайту')
    parser.add_argument('--application',choices=('schooltest','schoolwork'),required=True)
    parser.add_argument('--owner',type=int,required=True,help='ID поточного вчителя / адміністратора на новому встановленні')
    parser.add_argument('--scope',choices=('personal','server'),required=True)
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--password-prompt',action='store_true')
    parser.add_argument('--verify-only',action='store_true')
    parser.add_argument('--yes',action='store_true')
    args=parser.parse_args()
    if args.application=='schoolwork':
        os.environ.setdefault('DJANGO_SETTINGS_MODULE','schoolnet.settings')
        import django
        django.setup()
        from feed.cloud_backups import Adapter
    else:
        from app.services.cloud_backups import Adapter
    adapter=Adapter();adapter.authorize(args.owner,args.scope)
    password=getpass.getpass('Пароль архіву: ') if args.password_prompt else ''
    config={'scope':args.scope,'repository':file_digest(args.archive)[:32], '_snapshot':'offline-'+file_digest(args.archive)}
    with tempfile.TemporaryDirectory(prefix='offline-restore-') as directory:
        extract_portable(args.archive,directory,password)
        directory=Path(directory)
        print(json.dumps(adapter.validate(args.owner,config,directory),ensure_ascii=False))
        if not args.verify_only:
            if not args.yes and input('Для відновлення на новому встановленні введіть ВІДНОВИТИ: ').strip()!='ВІДНОВИТИ':
                raise SystemExit('Відновлення скасовано')
            print(json.dumps(adapter.restore(args.owner,config,directory),ensure_ascii=False))


if __name__=='__main__': main()
