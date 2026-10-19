"""Download wheels only; student code is never available in this networked container."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

packages = json.loads(sys.argv[1])
Path('/wheels').mkdir(exist_ok=True)
process = subprocess.Popen([
    '/opt/broker-venv/bin/python', '-m', 'pip', '--isolated', 'download',
    '--disable-pip-version-check', '--no-cache-dir', '--no-input',
    '--only-binary=:all:', '--index-url', 'https://pypi.org/simple',
    '--dest', '/wheels', *packages,
], stdin=subprocess.DEVNULL, start_new_session=True)
started = time.monotonic()
while process.poll() is None:
    size = sum(path.stat().st_size for path in Path('/wheels').iterdir() if path.is_file())
    if size > 256 * 1024 * 1024 or time.monotonic() - started > 150:
        os.killpg(process.pid, signal.SIGKILL)
        print('Перевищено ліміт завантаження: 256 МБ або 150 секунд.', flush=True)
        sys.exit(1)
    time.sleep(.2)
if process.returncode:
    sys.exit(process.returncode)
if sum(path.stat().st_size for path in Path('/wheels').iterdir()) > 256 * 1024 * 1024:
    print('Бібліотеки перевищують ліміт 256 МБ.', flush=True)
    sys.exit(1)
print('SCHOOLNET_WHEELS_READY', flush=True)
# Keep the downloader alive until the broker starts the offline runtime.
time.sleep(180)
