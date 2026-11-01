"""Bounded ZIP/AES archives and content-addressed incremental snapshots.

A snapshot is visible only after all referenced blocks have been read back and
verified. Failed runs never replace earlier snapshots. No executable pickle/SQL.
"""
import hashlib
import io
import json
import tempfile
from pathlib import Path
import re
import time
import uuid
from pathlib import PurePosixPath

import pyzipper

CHUNK = 4 * 1024 * 1024
MAX_MANIFEST = 32 * 1024 * 1024
MAX_TOTAL = 100 * 1024**3
NAME = re.compile(r'^[a-zA-Z0-9_.-]+$')


def safe_path(name):
    p = PurePosixPath(name)
    if not name or '\\' in name or name.startswith('/') or any(x in ('', '.', '..') for x in name.split('/')) or ':' in name:
        raise ValueError('Небезпечний шлях в архіві')
    return str(p)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def pack(entries, password=''):
    out = io.BytesIO()
    with pyzipper.AESZipFile(out, 'w', compression=pyzipper.ZIP_DEFLATED, compresslevel=6) as z:
        if password:
            z.setpassword(password.encode())
            z.setencryption(pyzipper.WZ_AES, nbits=256)
        for name, data in entries:
            z.writestr(safe_path(name), data)
    return out.getvalue()


def unpack(data, password='', limit=MAX_TOTAL):
    result, size = {}, 0
    with pyzipper.AESZipFile(io.BytesIO(data)) as z:
        if password:
            z.setpassword(password.encode())
        for info in z.infolist():
            name = safe_path(info.filename)
            if name in result or info.is_dir() or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Повторний або недопустимий файл архіву')
            size += info.file_size
            if size > limit or len(result) >= 500000:
                raise ValueError('Архів перевищує ліміт розпакування')
            with z.open(info) as f:
                value = f.read(min(info.file_size, limit) + 1)
            if len(value) != info.file_size:
                raise ValueError('Пошкоджений архів')
            result[name] = value
    return result


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


class Repository:
    def __init__(self, remote, app, repository_id, password=''):
        if not re.fullmatch(r'[a-f0-9]{32}', repository_id):
            raise ValueError('Ідентифікатор сховища повинен містити 32 шістнадцяткові символи')
        self.remote, self.app, self.id, self.password = remote, app, repository_id, password

    def open(self, create=False, scope='personal'):
        if self.remote.exists('repository.zip'):
            meta = json.loads(unpack(self.remote.get('repository.zip', MAX_MANIFEST), self.password, MAX_MANIFEST)['repository.json'])
            if meta != {'protocol': 1, 'app': self.app, 'id': self.id, 'scope': scope}:
                raise ValueError('Інший сайт, обсяг копії або несумісний формат')
        elif create:
            self.remote.put('repository.zip', pack([('repository.json', json_bytes(
                {'protocol': 1, 'app': self.app, 'id': self.id, 'scope': scope}))], self.password))
        else:
            raise ValueError('Сховище не знайдено. Перевірте підключення та ідентифікатор')

    def snapshots(self):
        return sorted((name for name in self.remote.list('snapshots') if re.fullmatch(r'[0-9]{13}-[a-f0-9]{32}\.zip', name)), reverse=True)

    def backup(self, entries, scope='personal', archive_format='incremental', progress=lambda **kw: None):
        with tempfile.TemporaryDirectory(prefix='cloud-archive-') as work:
            return self._backup(entries, scope, archive_format, progress, Path(work))

    def _backup(self, entries, scope, archive_format, progress, work):
        self.open(True, scope)
        if archive_format not in ('incremental', 'zip'):
            raise ValueError('Невідомий формат')
        stamp = f'{int(time.time()*1000):013d}-{uuid.uuid4().hex}.zip'
        manifest = {'protocol': 1, 'app': self.app, 'scope': scope, 'format': archive_format,
                    'created': time.time(), 'files': {}, 'original_bytes': 0, 'uploaded_bytes': 0,
                    'reused_bytes': 0, 'objects': 0, 'files_count': 0, 'records': 0, 'percent': 0}
        full = None
        for name, source in entries:
            safe_path(name)
            if name in manifest['files']:
                raise ValueError('Дубль шляху')
            hashes, size, h = [], 0, hashlib.sha256()
            full = tempfile.TemporaryFile() if archive_format == 'zip' else None
            with source() as stream:
                while True:
                    block = stream.read(CHUNK)
                    if not block:
                        break
                    size += len(block)
                    manifest['original_bytes'] += len(block)
                    if manifest['original_bytes'] > MAX_TOTAL:
                        raise ValueError('Перевищено ліміт копії 100 GiB')
                    h.update(block)
                    if archive_format == 'zip':
                        full.write(block)
                    else:
                        key = digest(block)
                        path = 'blocks/' + key + '.zip'
                        if self.remote.exists(path):
                            stored = unpack(self.remote.get(path, CHUNK*2), self.password, CHUNK)['data']
                            if digest(stored) != key:
                                raise ValueError('Пошкоджений блок у хмарі')
                            manifest['reused_bytes'] += len(block)
                        else:
                            encoded = pack([('data', block)], self.password)
                            self.remote.put(path, encoded)
                            verified = unpack(self.remote.get(path, CHUNK*2), self.password, CHUNK)['data']
                            if digest(verified) != key:
                                raise ValueError('Помилка перевірки завантаження')
                            manifest['uploaded_bytes'] += len(encoded)
                        hashes.append(key)
            manifest['files'][name] = {'size': size, 'sha256': h.hexdigest(), 'blocks': hashes}
            if archive_format == 'zip':
                # Full ZIP intentionally trades deduplication for portability.
                full.seek(0)
                temporary = work/str(manifest['objects'])
                with temporary.open('wb') as out:
                    __import__('shutil').copyfileobj(full, out)
                full.close()
                manifest['files'][name]['content'] = temporary
            manifest['objects'] += 1
            manifest['files_count'] += int(name.startswith('files/'))
            manifest['records'] += int(name.startswith('db/'))
            manifest['percent'] = min(95, round(manifest['objects']*95/max(len(entries),1))) if hasattr(entries,'__len__') else 0
            progress(**{k: manifest[k] for k in ('objects', 'original_bytes', 'uploaded_bytes', 'reused_bytes','files_count','records','percent')})
        if archive_format == 'zip':
            members = [(name, value.pop('content')) for name, value in manifest['files'].items()]
            encoded = work/'snapshot.zip'
            metadata = json_bytes(manifest)
            if len(metadata)>MAX_MANIFEST: raise ValueError('Завеликий опис копії')
            write_zip(encoded, [('backup-manifest.json', lambda: io.BytesIO(metadata))]+
                      [(name, lambda p=path: p.open('rb')) for name,path in members], self.password)
            manifest['uploaded_bytes'] = encoded.stat().st_size
        else:
            metadata = json_bytes(manifest)
            if len(metadata)>MAX_MANIFEST: raise ValueError('Завеликий опис копії')
            encoded = pack([('backup-manifest.json', metadata)], self.password)
            manifest['uploaded_bytes'] += len(encoded)
        path = 'snapshots/' + stamp
        # File-capable transports stream ZIP bodies without loading whole backups
        # into the web worker's memory. Test transports may accept bytes only.
        self.remote.put(path, encoded if not isinstance(encoded, Path) or hasattr(self.remote,'download') else encoded.read_bytes())
        verified = work/'verified.zip'
        self._download(path, verified)
        expected = file_digest(encoded) if isinstance(encoded, Path) else digest(encoded)
        if file_digest(verified) != expected:
            self.remote.delete(path)
            raise ValueError('Не вдалося перевірити завершену копію')
        return {'snapshot': stamp, 'percent':100, **{k: manifest[k] for k in ('objects', 'original_bytes', 'uploaded_bytes', 'reused_bytes','files_count','records')}}

    def _download(self, path, target):
        if hasattr(self.remote, 'download'):
            self.remote.download(path, target, MAX_TOTAL)
        else:
            Path(target).write_bytes(self.remote.get(path, MAX_TOTAL))

    def extract(self, snapshot, directory, scope='personal'):
        self.open(False, scope)
        if snapshot not in self.snapshots():
            raise ValueError('Копію не знайдено')
        directory = Path(directory)
        with tempfile.TemporaryDirectory(prefix='cloud-download-') as work:
            archive = Path(work)/'snapshot.zip'
            self._download('snapshots/'+snapshot, archive)
            with pyzipper.AESZipFile(archive) as z:
                if self.password: z.setpassword(self.password.encode())
                infos = z.infolist()
                if len(infos) > 500001 or sum(i.file_size for i in infos) > MAX_TOTAL+MAX_MANIFEST:
                    raise ValueError('Перевищено ліміт розпакування')
                names = set()
                for info in infos:
                    safe_path(info.filename)
                    if info.filename in names or info.is_dir() or (info.external_attr >> 16) & 0o170000 == 0o120000:
                        raise ValueError('Повторний або недопустимий файл архіву')
                    names.add(info.filename)
                info = z.getinfo('backup-manifest.json')
                if info.file_size > MAX_MANIFEST: raise ValueError('Завеликий маніфест')
                manifest = json.loads(z.read(info))
                if manifest.get('protocol') != 1 or manifest.get('app') != self.app or manifest.get('scope') != scope or manifest.get('format') not in ('zip','incremental'):
                    raise ValueError('Несумісна копія')
                if not isinstance(manifest.get('files'),dict) or len(manifest['files'])>500000:
                    raise ValueError('Некоректний маніфест')
                total = sum(row['size'] for row in manifest['files'].values())
                if total > MAX_TOTAL or any(row['size']<0 for row in manifest['files'].values()):
                    raise ValueError('Перевищено ліміт відновлення')
                for name,row in manifest['files'].items():
                    safe_path(name)
                    target = directory/name
                    target.parent.mkdir(parents=True,exist_ok=True)
                    size, h = 0, hashlib.sha256()
                    with target.open('xb') as out:
                        if manifest['format'] == 'zip':
                            with z.open(name) as inp:
                                while True:
                                    block = inp.read(CHUNK)
                                    if not block: break
                                    size += len(block)
                                    if size > row['size']: raise ValueError('Завеликий файл')
                                    out.write(block); h.update(block)
                        else:
                            if len(row['blocks']) > (row['size']//CHUNK)+1:
                                raise ValueError('Некоректне число блоків')
                            for key in row['blocks']:
                                if not re.fullmatch('[a-f0-9]{64}', key): raise ValueError('Небезпечний ідентифікатор блока')
                                block = unpack(self.remote.get('blocks/'+key+'.zip', CHUNK*2), self.password, CHUNK)['data']
                                if digest(block) != key: raise ValueError('Контрольна сума блока не збігається')
                                size += len(block)
                                if size > row['size']: raise ValueError('Завеликий файл')
                                out.write(block); h.update(block)
                    if size != row['size'] or h.hexdigest() != row['sha256']:
                        raise ValueError('Контрольна сума файла не збігається')

    def read(self, snapshot, scope='personal'):
        # Convenience API for small consumers/tests; workers use extract().
        with tempfile.TemporaryDirectory(prefix='cloud-read-') as work:
            self.extract(snapshot, work, scope)
            for path in sorted(Path(work).rglob('*')):
                if path.is_file(): yield path.relative_to(work).as_posix(), path.read_bytes()

    def prune(self, days=30):
        # Retain at least the newest snapshot. Blocks are deliberately retained:
        # automatic deletion would race another writer/recovery on a new server.
        snapshots = self.snapshots()
        cutoff = (time.time() - days * 86400)*1000
        for name in snapshots[1:]:
            if int(name.split('-')[0]) < cutoff:
                self.remote.delete('snapshots/' + name)


def file_digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(CHUNK), b''): h.update(block)
    return h.hexdigest()


def write_zip(target, entries, password=''):
    import shutil
    with pyzipper.AESZipFile(target, 'w', compression=pyzipper.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as z:
        if password:
            z.setpassword(password.encode())
            z.setencryption(pyzipper.WZ_AES, nbits=256)
        for name,source in entries:
            with source() as inp, z.open(safe_path(name), 'w', force_zip64=True) as out:
                shutil.copyfileobj(inp,out,CHUNK)
