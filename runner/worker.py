"""Offline, disposable desktop and PTY. Runs only inside a restricted job container."""
import base64
import codecs
import io
import json
import os
from pathlib import Path
import pty
import select
import signal
import struct
import subprocess
import sys
import termios
import time
import fcntl

from PIL import ImageGrab
from dependencies import validate_bundle


def emit(kind, **values):
    print(json.dumps({'kind': kind, **values}), flush=True)


def command(args, **kwargs):
    return subprocess.run(args, check=True, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs)


def main():
    payload = json.loads(sys.stdin.buffer.readline(16 * 1024 * 1024))
    files, entry = validate_bundle(payload)
    root = Path('/work')
    for name, content in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    emit('state', state='preparing')
    emit('output', text='Створення окремого середовища Python…\r\n')
    command(['/usr/bin/python3', '-m', 'venv', '/tmp/venv'], timeout=30)
    python = '/tmp/venv/bin/python'
    if payload.get('packages'):
        emit('output', text='Встановлення бібліотек у venv: ' + ', '.join(payload['packages']) + '\r\n')
        try:
            result = command([python, '-m', 'pip', '--isolated', 'install', '--no-input',
                              '--disable-pip-version-check', '--no-index', '--no-cache-dir',
                              '--only-binary=:all:', '--find-links=/wheels',
                              *payload['packages']], timeout=90)
        except subprocess.CalledProcessError as exc:
            emit('output', text=exc.output.decode('utf-8', errors='replace'))
            raise RuntimeError('Не вдалося встановити бібліотеки у venv.') from exc
        emit('output', text=result.stdout.decode('utf-8', errors='replace'))
    env = {
        'PATH': '/tmp/venv/bin:/usr/bin:/bin', 'HOME': '/tmp', 'DISPLAY': ':99',
        'LANG': 'C.UTF-8', 'PYTHONUNBUFFERED': '1', 'TERM': 'xterm',
        'SDL_AUDIODRIVER': 'dummy', 'QT_X11_NO_MITSHM': '1',
        'SDL_VIDEODRIVER': 'x11', 'SDL_RENDER_DRIVER': 'software',
        'LIBGL_ALWAYS_SOFTWARE': '1', 'QT_QUICK_BACKEND': 'software',
        'MPLBACKEND': 'TkAgg', 'PYGAME_HIDE_SUPPORT_PROMPT': '1',
    }
    desktop = subprocess.Popen(['Xvfb', ':99', '-screen', '0', '1024x768x24',
                                '-nolisten', 'tcp', '-ac'], env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        if Path('/tmp/.X11-unix/X99').exists():
            break
        if desktop.poll() is not None:
            raise RuntimeError('Не вдалося створити графічне середовище.')
        time.sleep(.05)
    manager = subprocess.Popen(['openbox', '--startup', 'touch /tmp/schoolnet-desktop-ready'],
                               env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # SDL can wait indefinitely for mapping events if its first window races the
    # window manager's startup. Start student code only after Openbox is ready.
    for _ in range(100):
        if Path('/tmp/schoolnet-desktop-ready').exists():
            break
        if manager.poll() is not None:
            raise RuntimeError('Не вдалося запустити диспетчер графічних вікон.')
        time.sleep(.05)
    else:
        raise RuntimeError('Графічне середовище не відповідає.')
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 28, 100, 0, 0))
    def own_terminal():
        os.setsid()
        fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

    process = subprocess.Popen([python, '-u', str(root / entry)], cwd=root, env=env,
                               stdin=slave, stdout=slave, stderr=slave, preexec_fn=own_terminal)
    os.close(slave)
    emit('state', state='running')
    decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
    last_frame = 0
    last_image = None
    had_window = False
    controls = b''
    os.set_blocking(sys.stdin.fileno(), False)
    os.set_blocking(master, False)

    def read_output():
        try:
            data = os.read(master, 8192)
        except (OSError, BlockingIOError):
            return
        if data:
            emit('output', text=decoder.decode(data))

    def capture():
        nonlocal last_image, had_window
        try:
            windows = subprocess.run(['xdotool', 'search', '--onlyvisible', '--class', '.'],
                                     env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, timeout=2)
            visible = windows.returncode == 0 and bool(windows.stdout.strip())
            if visible and not had_window:
                had_window = True
                emit('desktop')
            if had_window and not visible:
                return  # Preserve the last program frame after its window closes.
            picture = ImageGrab.grab(xdisplay=':99')
            buffer = io.BytesIO()
            picture.save(buffer, 'JPEG', quality=75)
            data = buffer.getvalue()
            if data != last_image:
                last_image = data
                emit('frame', data=base64.b64encode(data).decode())
        except Exception:
            pass  # The display may still be starting.

    try:
        while process.poll() is None:
            ready, _, _ = select.select([master, sys.stdin.fileno()], [], [], .05)
            if master in ready:
                read_output()
            if sys.stdin.fileno() in ready:
                chunk = os.read(sys.stdin.fileno(), 32768)
                if not chunk:
                    break
                controls += chunk
                while b'\n' in controls:
                    line, controls = controls.split(b'\n', 1)
                    message = json.loads(line)
                    kind = message.get('type')
                    if kind == 'stdin':
                        os.write(master, message['text'].encode())
                    elif kind == 'pointer':
                        args = ['xdotool', 'mousemove', str(message['x']), str(message['y'])]
                        if message.get('button'):
                            args += [message['event'], str(message['button'])]
                        command(args, env=env, timeout=2)
                    elif kind == 'key':
                        command(['xdotool', message['event'], message['key']], env=env, timeout=2)
                    elif kind == 'text':
                        # xdotool type handles Unicode via X keysyms, without a shell.
                        command(['xdotool', 'type', '--clearmodifiers', '--', message['text']], env=env, timeout=2)
            if time.monotonic() - last_frame > .25:
                capture()
                last_frame = time.monotonic()
        for _ in range(32):
            read_output()
        capture()
        emit('output', text=decoder.decode(b'', final=True))
        emit('exit', code=process.poll())
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        desktop.terminate()
        os.close(master)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        emit('output', text=f'\r\n{exc}\r\n')
        emit('error', message='Помилка підготовки або виконання програми.')
        sys.exit(1)
