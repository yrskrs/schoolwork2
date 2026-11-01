"""Small private durable control store; independent from the restored application DB."""
import base64
import contextlib
import hashlib
import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet


def next_run(hour, frequency, now=None):
    now = now or datetime.now(ZoneInfo('Europe/Kyiv'))
    target = now.replace(hour=int(hour), minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    if frequency == 'weekly':
        target += timedelta(days=(7-target.weekday()) % 7)
    return target.timestamp()


class State:
    def __init__(self, root, secret):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.cipher = Fernet(base64.urlsafe_b64encode(hashlib.sha256(('cloud-backup:'+secret).encode()).digest()))
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS configs(owner TEXT PRIMARY KEY, value BLOB NOT NULL, due REAL);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL,
              status TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL, payload BLOB NOT NULL,
              result TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '', scope TEXT NOT NULL DEFAULT 'personal');
            CREATE INDEX IF NOT EXISTS jobs_owner ON jobs(owner,created);
            CREATE TABLE IF NOT EXISTS notices(id TEXT PRIMARY KEY, owner TEXT, title TEXT, body TEXT,
              created REAL, seen INTEGER NOT NULL DEFAULT 0);
            ''')
        os.chmod(self.root/'control.sqlite3', 0o600)

    @contextlib.contextmanager
    def db(self):
        db = sqlite3.connect(self.root/'control.sqlite3', timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def seal(self, value):
        return self.cipher.encrypt(json.dumps(value).encode())

    def unseal(self, value):
        return json.loads(self.cipher.decrypt(value))

    def config(self, owner):
        with self.db() as db:
            row = db.execute('SELECT value FROM configs WHERE owner=?', (str(owner),)).fetchone()
        return self.unseal(row[0]) if row else {}

    def save(self, owner, config):
        due = next_run(config['hour'], config['frequency']) if config.get('automatic') else None
        with self.db() as db:
            db.execute('INSERT INTO configs VALUES(?,?,?) ON CONFLICT(owner) DO UPDATE SET value=excluded.value,due=excluded.due',
                       (str(owner), self.seal(config), due))

    def enqueue(self, owner, kind, payload):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM jobs WHERE owner=? AND status IN ('queued','running')", (str(owner),)).fetchone():
                raise ValueError('Попередня операція ще виконується')
            job = uuid.uuid4().hex
            db.execute('INSERT INTO jobs(id,owner,kind,status,created,updated,payload) VALUES(?,?,?,?,?,?,?)',
                       (job, str(owner), kind, 'queued', time.time(), time.time(), self.seal(payload)))
            db.execute('UPDATE jobs SET scope=?, result=? WHERE id=?', (payload.get('config',{}).get('scope','personal'), json.dumps({'repository':payload.get('config',{}).get('repository')}),job))
        return job

    def history(self, owner):
        with self.db() as db:
            return [dict(r) for r in db.execute('SELECT id,kind,status,created,updated,result,error,scope FROM jobs WHERE owner=? ORDER BY created DESC LIMIT 30', (str(owner),))]

    def progress(self, job, **result):
        with self.db() as db:
            row = db.execute('SELECT result FROM jobs WHERE id=?',(job,)).fetchone()
            previous = json.loads(row[0]) if row else {}
            db.execute('UPDATE jobs SET result=?,updated=? WHERE id=?', (json.dumps({**previous,**result}), time.time(), job))

    def notice(self, owner, title, body, notice_id=None):
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO notices(id,owner,title,body,created) VALUES(?,?,?,?,?)',
                       (notice_id or uuid.uuid4().hex, str(owner), title, body, time.time()))

    def notices(self, owner):
        with self.db() as db:
            return [dict(r) for r in db.execute('SELECT * FROM notices WHERE owner=? ORDER BY created DESC LIMIT 30', (str(owner),))]

    def tick(self, adapter):
        import fcntl
        # One process owns the queue, including restart recovery. No clock-based
        # stealing of a lease while a slow cloud transfer is still running.
        with (self.root/'worker.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            with self.db() as db:
                interrupted = db.execute("SELECT id,owner FROM jobs WHERE status='running'").fetchall()
                for row in interrupted:
                    db.execute("UPDATE jobs SET status='failed',error=?,updated=? WHERE id=?", ('Операцію перервано перезапуском. Попередні завершені копії збережено; запустіть повторно.', time.time(), row['id']))
                configs = db.execute('SELECT owner,value FROM configs WHERE due<=?', (time.time(),)).fetchall()
            for row in interrupted:
                self.notice(row['owner'], 'Резервування перервано', 'Повторіть операцію. Попередні завершені копії збережено.', row['id'])
            for row in configs:
                config = self.unseal(row['value'])
                try:
                    self.enqueue(row['owner'].split('/')[0], 'backup', {'config': config})
                except ValueError:
                    pass
                with self.db() as db:
                    db.execute('UPDATE configs SET due=? WHERE owner=?', (next_run(config['hour'], config['frequency']), row['owner']))
            with self.db() as db:
                row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
                if not row:
                    return
                db.execute("UPDATE jobs SET status='running',updated=? WHERE id=?", (time.time(), row['id']))
            from .archive import Repository
            from .remote import transport
            try:
                payload = self.unseal(row['payload'])
                config = payload['config']
                adapter.authorize(row['owner'], config['scope'])
                remote = transport(config, adapter.app, config['repository'])
                repo = Repository(remote, adapter.app, config['repository'], config.get('password', ''))
                if row['kind'] == 'backup':
                    remote.prepare()
                    with adapter.snapshot(row['owner'], config) as entries:
                        result = repo.backup(entries, config['scope'], config['format'], lambda **kw: self.progress(row['id'], **kw))
                    repo.prune(30)
                    title = 'Резервну копію створено'
                    body = f"Об’єктів: {result['objects']}; даних: {result['original_bytes']} байт; передано: {result['uploaded_bytes']} байт; повторно використано: {result['reused_bytes']} байт."
                elif row['kind'] == 'list':
                    repo.open(False, config['scope'])
                    result = {'snapshots': repo.snapshots()}
                    title, body = 'Хмарні копії знайдено', f"Доступно копій: {len(result['snapshots'])}"
                elif row['kind'] in ('verify', 'restore', 'download'):
                    from .staging import recovery
                    with recovery(self.root, repo, payload['snapshot'], config['scope']) as directory:
                        if row['kind'] == 'restore':
                            result = adapter.restore(row['owner'], config, directory)
                            title, body = 'Відновлення завершено', 'Дані та вибрані файли відновлено. Оновіть сторінку; для повної копії увійдіть знову.'
                        elif row['kind'] == 'download':
                            from .archive import write_zip
                            target = self.root/(row['id']+'.zip')
                            write_zip(target, ((p.relative_to(directory).as_posix(), lambda path=p:path.open('rb')) for p in directory.rglob('*') if p.is_file()), config.get('password', ''))
                            os.chmod(target, 0o600)
                            result = {'download': row['id']}
                            title, body = 'ZIP підготовлено', 'Завантажте архів у розділі резервних копій.'
                        else:
                            result = adapter.validate(row['owner'], config, directory)
                            title, body = 'Копію перевірено', 'Контрольні суми, склад і сумісність копії перевірено. Дані сайту не змінювалися.'
                else:
                    raise ValueError('Невідома операція')
                self.progress(row['id'], **result)
                with self.db() as db:
                    db.execute("UPDATE jobs SET status='completed',updated=? WHERE id=?", (time.time(), row['id']))
                self.notice(row['owner'], title, body, row['id'])
                try: adapter.notify(row['owner'], title, body)
                except Exception: pass
            except Exception as error:
                # Exception text may contain file paths, SQL or credentials.
                # Expose only deliberate user-facing validation errors.
                detail = str(error) if type(error) is ValueError else 'Операція не завершена. Перевірте пароль, мережу, доступ до хмари та сумісність версій. Повторіть запуск.'
                with self.db() as db:
                    db.execute("UPDATE jobs SET status='failed',error=?,updated=? WHERE id=?", (detail[:600], time.time(), row['id']))
                self.notice(row['owner'], 'Помилка резервного копіювання / відновлення', detail[:600], row['id'])
                try: adapter.notify(row['owner'], 'Помилка резервного копіювання / відновлення', detail[:600])
                except Exception: pass
            # Ready ZIP exports are temporary private files, not a second backup.
            for path in self.root.glob('*.zip'):
                if path.stat().st_mtime < time.time()-86400:
                    path.unlink(missing_ok=True)


def start_worker(state, adapter, stop):
    import threading
    def run():
        while not stop.is_set():
            try:
                state.tick(adapter)
            except Exception:
                # The durable queue remains for the next tick. Credentials are
                # intentionally not logged by a background worker.
                pass
            stop.wait(5)
    thread = threading.Thread(target=run, daemon=True, name='cloud-backup')
    thread.start()
    return thread
