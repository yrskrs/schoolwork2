"""Explicit optional CLI adapter. Never install software, use a shell or reset other routes."""
import json
import subprocess
from urllib.parse import urlsplit
from django.conf import settings


class TailscaleError(Exception):
    pass


def run(*arguments):
    binary = getattr(settings, 'SCHOOLNET_TAILSCALE_BINARY', '')
    if not binary:
        raise TailscaleError('Керування Tailscale із сайту не підключено. Дивіться інструкцію нижче.')
    try:
        result = subprocess.run([binary, *arguments], capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise TailscaleError('Tailscale недоступний або не відповів за 15 секунд. Перевірте службу та права доступу.')
    if result.returncode:
        # Do not reflect daemon output (it may contain credentials or unrelated routes).
        raise TailscaleError('Команда Tailscale не виконана. Перевірте вхід у Tailscale, дозвіл Funnel і права оператора на сервері.')
    return result.stdout


def status():
    try:
        data = json.loads(run('status', '--json'))
        funnel = json.loads(run('funnel', 'status', '--json'))
    except (ValueError, TypeError):
        raise TailscaleError('Tailscale повернув некоректний стан.')
    if not isinstance(data, dict) or not isinstance(data.get('Self', {}), dict) or not isinstance(funnel, dict) or not isinstance(funnel.get('AllowFunnel', {}), dict):
        raise TailscaleError('Tailscale повернув некоректний стан.')
    return {'backend': data.get('BackendState', 'Невідомо'),
            'dns_name': data.get('Self', {}).get('DNSName', '').rstrip('.'),
            'funnel': any(funnel.get('AllowFunnel', {}).values()), '_config': funnel}


def set_funnel(connection, enabled):
    if enabled and not connection.enabled:
        raise TailscaleError('Спочатку збережіть підключення Tailscale.')
    parts = urlsplit(connection.public_url)
    if not parts.hostname:
        raise TailscaleError('Спочатку збережіть адресу Funnel.')
    current = status()
    if current['backend'] != 'Running' or current['dns_name'] != parts.hostname:
        raise TailscaleError('Адреса у налаштуваннях не відповідає підключеному вузлу Tailscale або вузол не підключений.')
    port = parts.port or 443
    target_port = int(getattr(settings, 'SCHOOLNET_TAILSCALE_TARGET_PORT', 8000))
    if not 1 <= target_port <= 65535:
        raise TailscaleError('Некоректний локальний порт сайту.')
    config = current['_config']
    root = config.get('Web', {}).get(f'{parts.hostname}:{port}', {}).get('Handlers', {}).get('/')
    target = f'http://127.0.0.1:{target_port}'
    if root and root.get('Proxy') != target:
        raise TailscaleError('На цьому порту вже опублікований інший сервіс. Оберіть вільний HTTPS-порт або змініть конфігурацію на сервері.')
    # --set-path=/ modifies only this site's root mount, not /test or other ports.
    args = ['funnel', '--bg', f'--https={port}', '--set-path=/', '--yes']
    if enabled:
        run(*args, f'http://127.0.0.1:{target_port}')
    else:
        run(*args, 'off')
