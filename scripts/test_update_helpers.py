"""Check Internet-fetch routing and safe image selection using disposable files."""
import importlib.util
import json
import shutil
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('fetch_update', SCRIPTS / 'fetch_update.py')
fetch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch)


class UpdateHelpers(unittest.TestCase):
    def test_github_ssh_uses_same_repository_via_https(self):
        for remote in ('git@github.com:owner/school_test.git', 'git@github.com:owner/school_test', 'ssh://git@github.com/owner/school_test.git'):
            self.assertEqual(fetch.github_https(remote), 'https://github.com/owner/school_test.git')
        for remote in ('https://github.com/owner/other.git', 'git@other.example:owner/other.git', '/tmp/synthetic.git'):
            self.assertEqual(fetch.github_https(remote), remote)

    def test_fetch_updates_origin_tracking_without_rewriting_origin(self):
        with patch.object(sys, 'argv', ['fetch_update', '--branch', 'main']), patch.object(fetch.subprocess, 'check_output', return_value='git@github.com:owner/school_test.git\n'), patch.object(fetch.subprocess, 'run') as run:
            fetch.main()
        command = run.call_args_list[-1].args[0]
        self.assertEqual(command, ['git', 'fetch', '--no-tags', 'https://github.com/owner/school_test.git', 'refs/heads/main:refs/remotes/origin/main'])
        self.assertEqual(run.call_args_list[-1].kwargs['env']['GIT_TERMINAL_PROMPT'], '0')
        self.assertNotIn('--force', command)

    def test_fetch_failure_stops_update(self):
        with patch.object(sys, 'argv', ['fetch_update']), patch.object(fetch.subprocess, 'check_output', return_value='https://github.com/owner/test.git'), patch.object(fetch.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, ['git', 'fetch'])):
            with self.assertRaisesRegex(SystemExit, 'робочі дані не змінено'):
                fetch.main()

    @unittest.skipUnless((SCRIPTS / 'select_release_image.py').exists(), 'Journal uses candidate images instead of .env image tags')
    def test_image_update_preserves_config_and_never_evaluates_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'app').mkdir()
            (root / 'app/version.py').write_text('__version__ = "9.8.7"\n')
            environment = 'DUMMY=$(touch forbidden)\nSCHOOLTEST_IMAGE="registry.example:5000/custom/app:1.0.0"\n'
            (root / '.env').write_text(environment)
            result = subprocess.run([sys.executable, str(SCRIPTS / 'select_release_image.py'), 'schooltest5'], cwd=root, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'registry.example:5000/custom/app:9.8.7')
            self.assertIn('DUMMY=$(touch forbidden)', (root / '.env').read_text())
            self.assertEqual((root / '.env.before-image-update').read_text(), environment)
            self.assertFalse((root / 'forbidden').exists())
            self.assertEqual((root / '.env').stat().st_mode & 0o777, 0o600)
            pinned = 'SCHOOLTEST_IMAGE=custom/app@sha256:synthetic\n'
            (root / '.env').write_text(pinned)
            result = subprocess.run([sys.executable, str(SCRIPTS / 'select_release_image.py'), 'schooltest5'], cwd=root, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((root / '.env').read_text(), pinned)



# Optional launchers must resolve the repository root after relocation.
ROOT = Path(__file__).resolve().parents[1]
SCHOOLTEST = (ROOT / 'app').is_dir()


@unittest.skipUnless((ROOT / 'desktop').exists(), 'Optional PC tools are excluded from the server image')
class DesktopLauncherPaths(unittest.TestCase):
    def test_linux_launchers_keep_venv_and_working_directory_at_project_root(self):
        launchers = [('start_linux.sh', 'desktop/run_gui.py'), ('start_register.sh', 'desktop/run_register.py')] if SCHOOLTEST else [
            ('start.sh', 'desktop/launcher.py'), ('start_register.sh', 'desktop/user_manager.py')]
        with tempfile.TemporaryDirectory(prefix='desktop paths ') as temporary:
            root = Path(temporary) / 'project with spaces'
            desktop = root / 'desktop';desktop.mkdir(parents=True)
            fake_bin = root / 'fake-bin';fake_bin.mkdir()
            fake_python = fake_bin / 'python3'
            fake_python.write_text('#!' + sys.executable + '\nimport os,sys,json\nprint(json.dumps({"cwd":os.getcwd(),"args":sys.argv[1:]}))\n')
            fake_python.chmod(0o755)
            (root / 'venv/bin').mkdir(parents=True)
            shutil.copy2(fake_python, root / 'venv/bin/python')
            (root / 'venv/bin/activate').write_text('')
            for name, target in launchers:
                shutil.copy2(ROOT / 'desktop' / name, desktop / name)
                (root / target).write_text('# no GUI execution during path tests')
                env = dict(os.environ, PATH=str(fake_bin) + os.pathsep + os.environ['PATH'])
                result = subprocess.run(['bash', str(desktop / name)], cwd=temporary, env=env,
                                        capture_output=True, text=True, check=True)
                record = json.loads(result.stdout.strip())
                self.assertEqual(record['cwd'], str(root))
                self.assertIn(target, ' '.join(record['args']))
                self.assertFalse((desktop / 'venv').exists())


if __name__ == '__main__':
    unittest.main()
