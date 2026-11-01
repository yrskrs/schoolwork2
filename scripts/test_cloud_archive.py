import contextlib
import io
import json
import tempfile
import unittest
from unittest.mock import patch

from cloud_backup.archive import Repository, digest, pack, safe_path, unpack
from cloud_backup.files import bytes_source, destination, install_files
from cloud_backup.state import State, next_run
from cloud_backup.control import save_config, profile, configuration, page_data
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


class MemoryRemote:
    def __init__(self): self.objects = {}; self.puts = []
    def prepare(self): pass
    def exists(self, name): return name in self.objects
    def put(self, name, value): self.objects[name] = value; self.puts.append(name)
    def get(self, name, limit=10**10):
        if name not in self.objects: raise FileNotFoundError(name)
        if len(self.objects[name]) > limit: raise ValueError('limit')
        return self.objects[name]
    def delete(self, name): self.objects.pop(name, None)
    def list(self, directory): return [p.split('/',1)[1] for p in self.objects if p.startswith(directory+'/')]


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.remote = MemoryRemote()
        self.repo = Repository(self.remote, 'test', 'a'*32, 'Секрет 42')
        self.entries = [('index.json', bytes_source(b'{"version":1}')), ('files/work.txt', bytes_source('Робота учня'.encode()))]

    def test_incremental_roundtrip_reuses_unchanged_and_transfers_change(self):
        first = self.repo.backup(self.entries)
        self.assertEqual(dict(self.repo.read(first['snapshot'])), {'index.json':b'{"version":1}', 'files/work.txt':'Робота учня'.encode()})
        self.remote.puts = []
        repeated = self.repo.backup(self.entries)
        self.assertEqual(repeated['reused_bytes'], first['original_bytes'])
        self.assertFalse(any(p.startswith('blocks/') for p in self.remote.puts))
        self.remote.puts = []
        self.repo.backup([self.entries[0], ('files/work.txt', bytes_source(b'changed'))])
        self.assertEqual(sum(p.startswith('blocks/') for p in self.remote.puts), 1)

    def test_zip_with_and_without_password(self):
        for password in ('', 'пароль'):
            repo = Repository(MemoryRemote(), 'test', 'b'*32, password)
            run = repo.backup(self.entries, archive_format='zip')
            self.assertIn('files/work.txt', dict(repo.read(run['snapshot'])))
            raw = repo.remote.get('snapshots/'+run['snapshot'])
            self.assertIn('index.json', unpack(raw, password))

    def test_wrong_password_does_not_publish_or_mutate(self):
        self.repo.backup(self.entries)
        before = self.remote.objects.copy()
        with self.assertRaises(Exception):
            Repository(self.remote,'test','a'*32,'wrong').backup(self.entries)
        self.assertEqual(before, self.remote.objects)

    def test_corrupt_block_rejected_before_new_snapshot(self):
        run = self.repo.backup(self.entries)
        block = next(p for p in self.remote.objects if p.startswith('blocks/'))
        self.remote.objects[block] = pack([('data', b'bad')], self.repo.password)
        with self.assertRaises(ValueError): self.repo.backup(self.entries)
        with self.assertRaises(ValueError): dict(self.repo.read(run['snapshot']))
        self.assertEqual(len(self.repo.snapshots()), 1)

    def test_interrupted_upload_preserves_previous_snapshot(self):
        old = self.repo.backup(self.entries)
        original_put = self.remote.put
        def fail(name, data):
            if name.startswith('blocks/'): raise OSError('offline')
            original_put(name,data)
        self.remote.put = fail
        with self.assertRaises(OSError): self.repo.backup([('changed', bytes_source(b'new'))])
        self.assertIn('index.json', dict(self.repo.read(old['snapshot'])))
        self.assertEqual(len(self.repo.snapshots()), 1)

    def test_repository_header_is_verified_before_publishing_any_snapshot(self):
        put=self.remote.put
        def corrupt(name,value):
            put(name,b'broken' if name=='repository.zip' else value)
        self.remote.put=corrupt
        with self.assertRaises(Exception):self.repo.backup(self.entries)
        self.assertFalse(self.repo.snapshots())

    def test_missing_block_rejected(self):
        run = self.repo.backup(self.entries)
        self.remote.objects.pop(next(p for p in self.remote.objects if p.startswith('blocks/')))
        with self.assertRaises(FileNotFoundError): dict(self.repo.read(run['snapshot']))

    def test_other_app_and_scope_rejected(self):
        self.repo.backup(self.entries)
        with self.assertRaises(ValueError): Repository(self.remote,'other','a'*32,self.repo.password).open()
        with self.assertRaises(ValueError): self.repo.open(scope='server')

    def test_traversal_and_bomb_limits(self):
        for name in ('../x','/x','a/../x','a\\x','a//x','a/./x','C:/x'):
            with self.assertRaises(ValueError): safe_path(name)
        with self.assertRaises(ValueError): unpack(pack([('data', b'x'*1000)]), limit=999)

    def test_pruning_keeps_newest_and_shared_blocks(self):
        with patch('cloud_backup.archive.time.time', return_value=1000000000):
            first = self.repo.backup(self.entries)
        latest = self.repo.backup(self.entries)
        self.repo.prune()
        self.assertNotIn(first['snapshot'], self.repo.snapshots())
        self.assertEqual(self.repo.snapshots(), [latest['snapshot']])
        self.assertIn('index.json', dict(self.repo.read(latest['snapshot'])))


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = State(self.temp.name, 'server secret')
    def tearDown(self): self.temp.cleanup()

    def test_secrets_encrypted_and_never_in_page(self):
        c = save_config(self.state, 1, 'personal', {'provider':'nextcloud', 'password':'private-pass','app_password':'private-app','materials':'on','students':'on'})
        self.assertNotIn(b'private-pass', (Path(self.temp.name)/'control.sqlite3').read_bytes())
        self.assertEqual(configuration(self.state,1,'personal')['password'],'private-pass')
        page = page_data(self.state,1,'personal')
        self.assertNotIn('private-pass', json.dumps(page))
        self.assertNotIn('private-app', json.dumps(page))
        self.assertEqual(len(c['repository']),32)

    def test_password_rotation_changes_repository(self):
        c = save_config(self.state,1,'personal', {'password':'first'})
        d = save_config(self.state,1,'personal', {'password':'second'})
        self.assertNotEqual(c['repository'],d['repository'])

    def test_personal_and_server_configs_independent(self):
        save_config(self.state,1,'personal', {'password':'one'})
        save_config(self.state,1,'server', {'password':'two'})
        self.assertEqual(configuration(self.state,1,'personal')['password'],'one')
        self.assertEqual(configuration(self.state,1,'server')['password'],'two')

    def test_queue_is_durable_and_refuses_overlapping_jobs(self):
        job = self.state.enqueue(1,'backup',{'config':{'password':'secret'}})
        with self.assertRaises(ValueError): self.state.enqueue(1,'backup',{})
        self.assertEqual(State(self.temp.name,'server secret').history(1)[0]['id'],job)
        self.assertNotIn(b'"password": "secret"', (Path(self.temp.name)/'control.sqlite3').read_bytes())

    def test_kyiv_schedule_and_monday_weekly(self):
        now = datetime(2026,11,1,3,0,tzinfo=ZoneInfo('Europe/Kyiv'))
        weekly = datetime.fromtimestamp(next_run(2,'weekly',now), ZoneInfo('Europe/Kyiv'))
        self.assertEqual((weekly.day,weekly.hour,weekly.weekday()), (2,2,0))
        daily = datetime.fromtimestamp(next_run(2,'daily',now),ZoneInfo('Europe/Kyiv'))
        self.assertEqual(daily.day,2)

    def test_file_install_rolls_back_and_refuses_overwrite(self):
        root = Path(self.temp.name)
        src = root/'source'; src.write_bytes(b'work')
        target = root/'out'/'work'
        with self.assertRaises(RuntimeError):
            with install_files([(src,target)]): raise RuntimeError('DB failure')
        self.assertFalse(target.exists())
        target.write_bytes(b'old')
        with self.assertRaises(FileExistsError):
            with install_files([(src,target)]): pass
        self.assertEqual(target.read_bytes(),b'old')


class QueueExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.state=State(self.temp.name,'server-secret')
        self.remote=MemoryRemote()
        self.config={'provider':'nextcloud','scope':'personal','repository':'f'*32,'format':'incremental','password':'p','hour':2,'frequency':'daily','automatic':True}
        self.notified=[]
        outer=self
        class Adapter:
            app='test'
            def authorize(self,owner,scope): pass
            @contextlib.contextmanager
            def snapshot(self,owner,config): yield [('index.json',bytes_source(b'{}'))]
            def notify(self,owner,title,body): outer.notified.append(title)
        self.adapter=Adapter()
        self.patch=patch('cloud_backup.remote.transport',return_value=self.remote);self.patch.start();self.addCleanup(self.patch.stop)

    def test_manual_and_due_schedule_finish_with_notifications(self):
        self.state.enqueue(1,'backup',{'config':self.config})
        self.state.tick(self.adapter)
        self.assertEqual(self.state.history(1)[0]['status'],'completed')
        self.state.save('1/personal',self.config)
        with self.state.db() as db: db.execute('UPDATE configs SET due=0')
        self.state.tick(self.adapter)
        self.assertEqual(len(self.notified),2)
        self.assertEqual(len(self.state.notices(1)),2)
        with self.state.db() as db: self.assertGreater(db.execute('SELECT due FROM configs').fetchone()[0],0)

    def test_failed_and_interrupted_runs_are_durable_failures(self):
        self.state.enqueue(1,'backup',{'config':self.config})
        with patch.object(self.remote,'put',side_effect=OSError('network failed')):
            self.state.tick(self.adapter)
        self.assertEqual(self.state.history(1)[0]['status'],'failed')
        self.assertEqual(len(self.state.notices(1)),1)
        job=self.state.enqueue(1,'backup',{'config':self.config})
        with self.state.db() as db: db.execute("UPDATE jobs SET status='running' WHERE id=?",(job,))
        self.state.tick(self.adapter)
        self.assertEqual(self.state.history(1)[0]['status'],'failed')
        self.assertEqual(len(self.state.notices(1)),2)

    def test_provider_error_does_not_leak_secrets(self):
        self.state.enqueue(1,'backup',{'config':self.config})
        with patch.object(self.remote,'put',side_effect=RuntimeError('token=private-password')):
            self.state.tick(self.adapter)
        self.assertNotIn('private-password',self.state.history(1)[0]['error'])

if __name__ == '__main__': unittest.main()
