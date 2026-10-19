"""Private broker: only this service has Docker access; Django never executes code.

The networked downloader receives package names only. The offline runtime receives
copies of submission files and wheels, no host mounts, credentials or Docker socket.
"""
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
import re
import struct
import threading
import time
from urllib.parse import parse_qs, urlsplit
import uuid

import docker
from dependencies import discover_requirements, validate_bundle

IMAGE = os.environ.get('CODE_RUNNER_IMAGE', 'schoolwork2-python-runner:4.5.7')
NAMESPACE = os.environ.get('CODE_RUNNER_NAMESPACE', 'schoolwork2')
TOKEN = os.environ.get('CODE_RUNNER_TOKEN', '')
MAX_SESSIONS = int(os.environ.get('CODE_RUNNER_MAX_SESSIONS', '4'))
RUN_SECONDS = int(os.environ.get('CODE_RUNNER_RUN_SECONDS', '180'))
OUTPUT_LIMIT = 128 * 1024
TERMINAL = {'finished', 'failed', 'stopped', 'timeout'}
client = None
sessions = {}
lock = threading.RLock()


def container_options():
    return dict(image=IMAGE, user='1000:1000', read_only=True, cap_drop=['ALL'],
                security_opt=['no-new-privileges:true'], mem_limit='768m', memswap_limit='768m',
                nano_cpus=1_000_000_000, pids_limit=128, init=True,
                tmpfs={'/tmp': 'rw,exec,nosuid,nodev,size=512m,mode=1777',
                       '/work': 'rw,nosuid,nodev,size=16m,uid=1000,gid=1000,mode=700'},
                labels={'schoolnet.runner': NAMESPACE},
                log_config=docker.types.LogConfig(type='local', config={
                    'max-size': '2m', 'max-file': '1', 'compress': 'false'}))


class Session:
    def __init__(self, owner, payload, packages):
        self.id = uuid.uuid4().hex
        self.owner = owner
        self.payload = payload
        self.packages = packages
        self.state = 'preparing'
        self.output = ''
        self.offset = 0
        self.frame = None
        self.frame_id = 0
        self.has_desktop = False
        self.code = None
        self.created = self.touched = time.monotonic()
        self.running_since = None
        self.container = self.downloader = self.socket = self.volume = None
        self.mutex = threading.RLock()

    def append(self, value):
        with self.mutex:
            self.output += value
            if len(self.output) > OUTPUT_LIMIT:
                excess = len(self.output) - OUTPUT_LIMIT
                self.output = self.output[excess:]
                self.offset += excess

    def status(self, cursor=0, frame=0):
        with self.mutex:
            self.touched = time.monotonic()
            start = max(0, cursor - self.offset)
            return {'id': self.id, 'state': self.state, 'exit_code': self.code,
                    'output': self.output[start:], 'offset': self.offset + len(self.output),
                    'truncated': cursor < self.offset, 'frame_id': self.frame_id,
                    'has_desktop': self.has_desktop,
                    'frame': self.frame if self.frame_id != frame else None}

    def send(self, message):
        with self.mutex:
            if self.state != 'running' or not self.socket:
                raise ValueError('Програма ще не запущена або вже завершена.')
            self.touched = time.monotonic()
            self.socket._sock.sendall(json.dumps(message).encode() + b'\n')

    def stop(self, state='stopped'):
        with self.mutex:
            if self.state not in TERMINAL:
                self.state = state
            containers = [self.container, self.downloader]
        for container in containers:
            if container:
                try:
                    container.remove(force=True)
                except docker.errors.NotFound:
                    pass
                except docker.errors.DockerException:
                    pass
        if self.volume:
            try:
                self.volume.remove()
            except docker.errors.DockerException:
                pass  # A simultaneous cleanup will retry after removing containers.

    def event(self, event):
        kind = event.get('kind')
        with self.mutex:
            if self.state in TERMINAL:
                return
            if kind == 'output':
                self.append(str(event.get('text', ''))[:OUTPUT_LIMIT])
            elif kind == 'frame':
                value = event.get('data', '')
                if len(value) < 1024 * 1024:
                    self.frame = value
                    self.frame_id += 1
            elif kind == 'desktop':
                self.has_desktop = True
            elif kind == 'state' and event.get('state') == 'running':
                self.state = 'running'
                self.running_since = time.monotonic()
            elif kind == 'exit':
                self.code = event.get('code')
                self.state = 'finished'
            elif kind == 'error':
                self.state = 'failed'
                self.append(str(event.get('message', '')))

    def run(self):
        try:
            # Hold the session lock while assigning resources: a simultaneous Stop
            # must always see and remove every newly created container.
            if self.packages:
                self.append('Завантаження бібліотек: ' + ', '.join(self.packages) + '\r\n')
                with self.mutex:
                    if self.state in TERMINAL:
                        return
                    self.volume = client.volumes.create(
                        name='schoolnet-wheels-' + self.id, labels={'schoolnet.runner': NAMESPACE})
                    self.downloader = client.containers.create(
                        **container_options(), network_mode='bridge',
                        volumes={self.volume.name: {'bind': '/wheels', 'mode': 'rw'}},
                        command=['/usr/bin/python3', '/opt/runner/download.py', json.dumps(self.packages)])
                    self.downloader.start()
                ready = False
                for line in self.downloader.logs(stream=True, follow=True):
                    text = line.decode('utf-8', errors='replace')
                    if 'SCHOOLNET_WHEELS_READY' in text:
                        ready = True
                        break
                    self.append(text)
                    if self.state in TERMINAL:
                        return
                if not ready:
                    raise ValueError('Не вдалося завантажити бібліотеки. Перевірте назви та версії пакетів.')
            with self.mutex:
                if self.state in TERMINAL:
                    return
                self.container = client.containers.create(
                    **container_options(), network_mode='none', stdin_open=True,
                    volumes={self.volume.name: {'bind': '/wheels', 'mode': 'ro'}} if self.volume else {},
                    command=['/usr/bin/python3', '/opt/runner/worker.py'])
                self.socket = self.container.attach_socket(params={
                    'stdin': 1, 'stdout': 1, 'stderr': 1, 'stream': 1})
                self.socket._sock.settimeout(15)
                self.container.start()
                if self.downloader:
                    self.downloader.remove(force=True)
                    self.downloader = None
                self.payload['packages'] = self.packages
                self.socket._sock.sendall(json.dumps(self.payload).encode() + b'\n')
                self.payload = None
                self.socket._sock.settimeout(None)
            pending = b''
            buffer = b''
            while self.state not in TERMINAL:
                data = self.socket._sock.recv(65536)
                if not data:
                    break
                buffer += data
                # Docker multiplexes stdout/stderr with an 8-byte header.
                while len(buffer) >= 8:
                    size = struct.unpack('>I', buffer[4:8])[0]
                    if size > 2 * 1024 * 1024:
                        raise ValueError('Некоректна відповідь середовища виконання.')
                    if len(buffer) < 8 + size:
                        break
                    stream, packet, buffer = buffer[0], buffer[8:8 + size], buffer[8 + size:]
                    if stream == 2:
                        self.append(packet.decode('utf-8', errors='replace'))
                        continue
                    pending += packet
                    while b'\n' in pending:
                        line, pending = pending.split(b'\n', 1)
                        self.event(json.loads(line))
                    if len(pending) > 2 * 1024 * 1024:
                        raise ValueError('Завелика відповідь середовища виконання.')
            if self.state not in TERMINAL:
                self.append('\r\nСередовище виконання несподівано завершилося (можливо, перевищено ліміт пам’яті).\r\n')
                self.state = 'failed'
        except Exception as exc:
            with self.mutex:
                if self.state not in TERMINAL:
                    logging.exception('Python runner job %s failed', self.id)
                    # Docker errors may include host details; show a bounded message.
                    self.append('\r\n' + (str(exc)[:1000] if isinstance(exc, ValueError)
                                          else 'Сервіс виконання недоступний або перевищено ліміт ресурсів.') + '\r\n')
                    self.state = 'failed'
        finally:
            self.stop(self.state)
            if self.socket:
                self.socket.close()
            self.payload = None


def validate_input(data):
    kind = data.get('type')
    if kind in ('stdin', 'text'):
        value = data.get('text')
        if not isinstance(value, str) or len(value) > 4096:
            raise ValueError('Введіть до 4096 символів.')
        return {'type': kind, 'text': value}
    if kind == 'pointer':
        x, y = int(data['x']), int(data['y'])
        if not (0 <= x < 1024 and 0 <= y < 768):
            raise ValueError('Некоректні координати.')
        button = data.get('button', 0)
        event = data.get('event', 'mousemove')
        if button not in (0, 1, 2, 3, 4, 5) or event not in ('mousemove', 'mousedown', 'mouseup', 'click'):
            raise ValueError('Некоректна подія миші.')
        return {'type': kind, 'x': x, 'y': y, 'button': button, 'event': event}
    if kind == 'key':
        key = data.get('key', '')
        if (not re.fullmatch(r'[A-Za-z0-9_]{1,30}', key)
                or data.get('event') not in ('keydown', 'keyup')):
            raise ValueError('Некоректна клавіша.')
        return {'type': kind, 'key': key, 'event': data['event']}
    raise ValueError('Невідомий тип вводу.')


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Never log authorization headers or student code.

    def reply(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request()

    def handle_request(self):
        path = urlsplit(self.path)
        if path.path == '/healthz' and self.command == 'GET':
            return self.reply(200, {'ok': True})
        if not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + TOKEN):
            return self.reply(403, {'error': 'Доступ заборонено.'})
        try:
            if self.command == 'POST':
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 15 * 1024 * 1024:
                    return self.reply(413, {'error': 'Завеликий запит.'})
                data = json.loads(self.rfile.read(size))
            else:
                data = {key: values[0] for key, values in parse_qs(path.query).items()}
            owner = data.get('owner')
            if not isinstance(owner, str) or not owner or len(owner) > 100:
                raise ValueError('Некоректний власник запуску.')
            if path.path == '/runs' and self.command == 'POST':
                files, _ = validate_bundle(data)
                packages = discover_requirements(files)
                with lock:
                    for previous in sessions.values():
                        if previous.owner == owner and previous.state not in TERMINAL:
                            previous.stop()
                    if sum(s.state not in TERMINAL for s in sessions.values()) >= MAX_SESSIONS:
                        return self.reply(429, {'error': 'Усі середовища зайняті. Спробуйте трохи пізніше.'})
                    session = Session(owner, data, packages)
                    sessions[session.id] = session
                    threading.Thread(target=session.run, daemon=True).start()
                return self.reply(201, {'id': session.id, 'state': session.state})
            match = re.fullmatch(r'/runs/([a-f0-9]{32})(?:/(input|stop))?', path.path)
            if not match:
                return self.reply(404, {'error': 'Запуск не знайдено.'})
            with lock:
                session = sessions.get(match[1])
            if not session or session.owner != owner:
                return self.reply(404, {'error': 'Запуск не знайдено або термін його дії минув.'})
            if self.command == 'GET' and not match[2]:
                return self.reply(200, session.status(max(0, int(data.get('cursor', 0))),
                                                      max(0, int(data.get('frame', 0)))))
            if self.command == 'POST' and match[2] == 'stop':
                session.stop()
                return self.reply(200, {'state': session.state})
            if self.command == 'POST' and match[2] == 'input':
                session.send(validate_input(data.get('input', {})))
                return self.reply(200, {'ok': True})
            self.reply(405, {'error': 'Метод не підтримується.'})
        except ValueError as exc:
            self.reply(400, {'error': str(exc)[:500] or 'Некоректні дані запиту.'})
        except (TypeError, KeyError, AttributeError):
            self.reply(400, {'error': 'Некоректні дані запиту.'})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.reply(503, {'error': 'Середовище запуску тимчасово недоступне.'})


def reap():
    while True:
        time.sleep(5)
        with lock:
            now = time.monotonic()
            for key, session in list(sessions.items()):
                if session.state not in TERMINAL and (
                    now - session.created > 420 or now - session.touched > 90
                    or (session.running_since and now - session.running_since > RUN_SECONDS)
                ):
                    session.append('\r\nЗапуск зупинено: вичерпано час виконання або закрито переглядач.\r\n')
                    session.stop('timeout')
                if session.state in TERMINAL and now - session.touched > 600:
                    del sessions[key]


if __name__ == '__main__':
    if len(TOKEN) < 32:
        raise SystemExit('Set CODE_RUNNER_TOKEN to a secret of at least 32 characters.')
    client = docker.from_env(timeout=20)
    client.ping()
    # Remove orphaned jobs after a broker restart, within this deployment only.
    for orphan in client.containers.list(all=True, filters={'label': f'schoolnet.runner={NAMESPACE}'}):
        orphan.remove(force=True)
    for volume in client.volumes.list(filters={'label': f'schoolnet.runner={NAMESPACE}'}):
        volume.remove()
    threading.Thread(target=reap, daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', 8002), Handler).serve_forever()
