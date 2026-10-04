"""Filesystem-only recovery tests; never touch a database or real uploads."""
import hashlib
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from scripts.backup_tools import install, stage, validate


class BackupToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.backup = self.root / 'backup'; self.backup.mkdir()
        (self.backup / 'database.dump').write_bytes(b'PGDMPsynthetic-header')
        self.archive([('folder/work.txt', 'file', b'new work'), ('.hidden', 'file', b'new hidden')])

    def archive(self, entries):
        with tarfile.open(self.backup / 'media.tar.gz', 'w:gz') as archive:
            for name, kind, content in entries:
                member = tarfile.TarInfo(name)
                if kind == 'symlink': member.type = tarfile.SYMTYPE; member.linkname = '/etc/passwd'
                else: member.size = len(content)
                archive.addfile(member, io.BytesIO(content) if kind == 'file' else None)
        manifest = '\n'.join(hashlib.sha256((self.backup / name).read_bytes()).hexdigest()+'  '+name for name in ('database.dump','media.tar.gz'))
        (self.backup / 'SHA256SUMS').write_text(manifest+'\n')

    def test_valid_snapshot(self): validate(self.backup)

    def test_corrupt_snapshot_fails_before_staging(self):
        (self.backup / 'database.dump').write_bytes(b'PGDMPcorrupt')
        with self.assertRaises(ValueError): validate(self.backup)

    def test_missing_media_or_checksum_fails(self):
        (self.backup / 'SHA256SUMS').unlink()
        with self.assertRaises(ValueError): validate(self.backup)
        validate(self.backup, legacy=True)
        (self.backup / 'media.tar.gz').unlink()
        with self.assertRaises(ValueError): validate(self.backup, legacy=True)

    def test_absolute_traversal_and_links_rejected(self):
        for name, kind in [('../outside','file'),('/outside','file'),('link','symlink')]:
            with self.subTest(name=name):
                self.archive([(name,kind,b'bad')])
                with self.assertRaises(ValueError): validate(self.backup)

    def test_staged_files_and_dotfiles_replace_current_uploads(self):
        media=self.root/'media'; media.mkdir(); (media/'.old-hidden').write_text('old')
        (media/'old.txt').write_text('old')
        with patch('builtins.print') as output: stage(self.backup/'media.tar.gz',media)
        staged=output.call_args.args[0]
        self.assertTrue((media/'old.txt').is_file())
        install(media,staged)
        self.assertEqual((media/'folder/work.txt').read_bytes(),b'new work')
        self.assertTrue((media/'.hidden').is_file())
        self.assertFalse((media/'.old-hidden').exists())
        self.assertFalse((media/'old.txt').exists())

    def test_failed_install_rolls_back_existing_uploads(self):
        media=self.root/'media';media.mkdir();(media/'old.txt').write_text('old')
        with patch('builtins.print') as output: stage(self.backup/'media.tar.gz',media)
        staged=output.call_args.args[0]; rename=Path.rename
        def failing_rename(path,target):
            if path.name=='.hidden' and path.parent.name==staged: raise OSError('synthetic disk failure')
            return rename(path,target)
        with patch.object(Path,'rename',failing_rename), self.assertRaises(OSError): install(media,staged)
        self.assertEqual((media/'old.txt').read_text(),'old')
        self.assertFalse((media/'folder').exists())

    def test_stage_argument_cannot_escape_media(self):
        media=self.root/'media';media.mkdir()
        with self.assertRaises(ValueError): install(media,'../backup')


if __name__=='__main__': unittest.main()
