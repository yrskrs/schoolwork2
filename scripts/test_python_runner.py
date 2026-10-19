"""Real Docker smoke tests, using synthetic code and a disposable private broker."""
import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import uuid

IMAGE = os.environ.get('CODE_RUNNER_IMAGE', 'schoolwork2-python-runner:4.5.7')
NAME = 'schoolnet-runner-test-' + uuid.uuid4().hex[:12]
TOKEN = uuid.uuid4().hex + uuid.uuid4().hex
BASE = None
FINAL = {'finished', 'stopped', 'failed', 'timeout'}


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True).strip()


def api(path, data, method='POST', token=TOKEN):
    request = Request(BASE + path + ('?' + urlencode(data) if method == 'GET' else ''),
                      data=json.dumps(data).encode() if method == 'POST' else None,
                      headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}, method=method)
    with urlopen(request, timeout=10) as response:
        return json.load(response)


def start(code, extra=None, owner='teacher-test'):
    files = {'main.py': code.encode(), **(extra or {})}
    return api('/runs', {'owner': owner, 'entry': 'main.py', 'files': [
        {'name': name, 'content': base64.b64encode(content).decode()} for name, content in files.items()]})['id']


def status(job, owner='teacher-test'):
    return api('/runs/' + job, {'owner': owner}, 'GET')


def until(job, condition, seconds=60):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        data = status(job)
        if condition(data):
            return data
        if data['state'] in FINAL:
            raise AssertionError({**data, 'frame': bool(data['frame'])})
        time.sleep(.15)
    data = status(job)
    raise AssertionError('Timed out: ' + str({**data, 'frame': bool(data['frame'])}))


def finished(job, code=0, seconds=60):
    data = until(job, lambda value: value['state'] in FINAL, seconds)
    assert data['state'] == 'finished' and data['exit_code'] == code, {**data, 'frame': bool(data['frame'])}
    return data


def send(job, value):
    return api('/runs/' + job + '/input', {'owner': 'teacher-test', 'input': value})


def pygame_check():
    job = start('''print('IMPORT', flush=True)
import pygame, time
print('INIT', flush=True)
pygame.init()
print('DISPLAY', flush=True)
s=pygame.display.set_mode((300,200))
s.fill((20,160,220)); pygame.display.flip()
print('PYGAME', flush=True)
time.sleep(1)
''')
    data = finished(job, seconds=180)
    assert data['frame'] and 'PYGAME' in data['output']


def ui_check():
    root = Path(__file__).resolve().parent.parent
    subprocess.run([str(root / 'venv/bin/python'), str(root / 'scripts/test_python_runner_ui.py')],
                   env={**os.environ, 'CODE_RUNNER_URL': BASE, 'CODE_RUNNER_TOKEN': TOKEN}, check=True)


def main():
    global BASE
    docker('run', '-d', '--rm', '--name', NAME, '-p', '127.0.0.1::8002',
           '-v', '/var/run/docker.sock:/var/run/docker.sock',
           '-e', 'CODE_RUNNER_TOKEN=' + TOKEN, '-e', 'CODE_RUNNER_IMAGE=' + IMAGE,
           '-e', 'CODE_RUNNER_NAMESPACE=' + NAME, '-e',
           'CODE_RUNNER_RUN_SECONDS=' + ('60' if '--pygame-only' in sys.argv else '12'), IMAGE)
    try:
        port = docker('port', NAME, '8002/tcp').split(':')[-1]
        BASE = 'http://127.0.0.1:' + port
        for _ in range(100):
            try:
                with urlopen(BASE + '/healthz', timeout=1):
                    break
            except (URLError, OSError):
                time.sleep(.1)
        try:
            api('/runs/test', {'owner': 'teacher-test'}, 'GET', token='bad')
            raise AssertionError('Missing authentication')
        except HTTPError as exc:
            assert exc.code == 403
        if '--pygame-only' in sys.argv:
            pygame_check()
            print('PASS: pygame native graphical output', flush=True)
            return
        if '--ui-only' in sys.argv:
            ui_check()
            return
        job = start('''import sys, os, socket
print('VENV', sys.prefix != sys.base_prefix)
print('NO_PROJECT', not os.path.exists('/app') and not os.path.exists('/var/run/docker.sock'))
print('NO_SECRET', 'CODE_RUNNER_TOKEN' not in os.environ)
try:
    socket.create_connection(('1.1.1.1', 443), .2)
    print('NETWORK_LEAK')
except OSError:
    print('OFFLINE')
print('ANSWER', input('Name: '))
''')
        until(job, lambda d: 'Name: ' in d['output'])
        send(job, {'type': 'stdin', 'text': 'Привіт\n'})
        data = finished(job)
        for expected in ('VENV True', 'NO_PROJECT True', 'NO_SECRET True', 'OFFLINE', 'ANSWER Привіт'):
            assert expected in data['output'], data
        print('PASS: interactive stdin, fresh venv, no project/secrets/network', flush=True)

        job = start("from helper import value\nprint(value, open('data.txt').read())",
                    {'helper.py': b'value = 42', 'data.txt': b'asset'})
        assert '42 asset' in finished(job)['output']
        job = start("raise ValueError('expected traceback')")
        assert 'ValueError: expected traceback' in finished(job, code=1)['output']
        print('PASS: companion modules/assets and Python traceback', flush=True)

        job = start('import PIL\nfrom PIL import Image\nprint("PIL_INSTALLED", PIL.__version__, Image.new("RGB", (1, 1)).size)\n',
                    {'requirements.txt': b'Pillow==11.3.0'})
        assert 'PIL_INSTALLED 11.3.0' in finished(job, seconds=180)['output']
        print('PASS: automatic wheel download and offline installation in venv', flush=True)

        job = start('''import tkinter as tk
r = tk.Tk(); r.geometry('400x240+40+40')
b = tk.Button(r, text='Click', command=lambda: print('CLICKED', flush=True)); b.pack()
e = tk.Entry(r); e.pack(); e.bind('<Return>', lambda event: print('ENTRY:', e.get(), flush=True))
r.update()
print('BUTTON:', b.winfo_rootx()+10, b.winfo_rooty()+10, flush=True)
print('ENTRY_POS:', e.winfo_rootx()+10, e.winfo_rooty()+10, flush=True)
r.mainloop()
''')
        data = until(job, lambda d: 'BUTTON:' in d['output'] and d['frame'])
        until(job, lambda d: d['has_desktop'])
        image = base64.b64decode(data['frame'])
        assert image[:2] == b'\xff\xd8', data
        for label in ('BUTTON', 'ENTRY_POS'):
            x, y = map(int, re.search(label + r': (\d+) (\d+)', data['output']).groups())
            send(job, {'type': 'pointer', 'x': x, 'y': y, 'event': 'click', 'button': 1})
            if label == 'BUTTON':
                until(job, lambda d: 'CLICKED' in d['output'])
        send(job, {'type': 'text', 'text': 'Привіт'})
        send(job, {'type': 'key', 'event': 'keydown', 'key': 'Return'})
        send(job, {'type': 'key', 'event': 'keyup', 'key': 'Return'})
        until(job, lambda d: 'ENTRY: Привіт' in d['output'])
        api('/runs/' + job + '/stop', {'owner': 'teacher-test'})
        assert status(job)['state'] == 'stopped'
        print('PASS: Tkinter desktop, mouse, Unicode input, keyboard and Stop', flush=True)

        job = start('import turtle, time\nturtle.forward(80)\nturtle.update()\nprint("TURTLE")\ntime.sleep(1)')
        assert finished(job)['frame']
        pygame_check()
        print('PASS: turtle and pygame native graphical output', flush=True)

        job = start('while True:\n    pass')
        until(job, lambda d: d['state'] == 'running')
        send(job, {'type': 'stdin', 'text': '\x03'})
        assert 'KeyboardInterrupt' in finished(job, code=-2)['output']
        job = start('while True:\n    pass')
        data = until(job, lambda d: d['state'] == 'timeout', seconds=30)
        print('PASS: Ctrl+C and server-side execution timeout', flush=True)

        # An old run cannot be read under another owner.
        try:
            status(job, owner='another-teacher')
            raise AssertionError('Cross-owner access')
        except HTTPError as exc:
            assert exc.code == 404
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            remaining = docker('ps', '-aq', '--filter', 'label=schoolnet.runner=' + NAME)
            if not remaining:
                break
            time.sleep(.2)
        assert not remaining, remaining
        assert not docker('volume', 'ls', '-q', '--filter', 'label=schoolnet.runner=' + NAME)
        print('PASS: owner authorization and container cleanup', flush=True)
        if '--ui' in sys.argv:
            ui_check()
    except Exception:
        print(docker('logs', NAME), flush=True)
        raise
    finally:
        docker('rm', '-f', NAME)
        remaining = docker('ps', '-aq', '--filter', 'label=schoolnet.runner=' + NAME)
        if remaining:
            docker('rm', '-f', *remaining.splitlines())
        volumes = docker('volume', 'ls', '-q', '--filter', 'label=schoolnet.runner=' + NAME)
        if volumes:
            docker('volume', 'rm', *volumes.splitlines())


if __name__ == '__main__':
    main()
