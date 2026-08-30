"""
Middleware для підрахунку онлайн-клієнтів та логування IP-адрес підключених пристроїв.
"""

import sys
import time
import traceback
from datetime import datetime
from threading import Lock

# Зберігає {ip: {'timestamp': ts, 'last_path': path, 'last_time_str': '23:45:00'}}
_active_clients = {}
_lock = Lock()
ONLINE_THRESHOLD_SECONDS = 35  # 35 секунд таймаут (активні вкладки надсилають heartbeat кожні 12 сек)


def get_client_ip(request):
    """Отримує реальну IP-адресу клієнта з HTTP заголовків або REMOTE_ADDR."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR', '127.0.0.1')
    return ip


def remove_client_ip(ip):
    """Видаляє IP клієнта при закритті вкладки/сайту."""
    with _lock:
        if ip in _active_clients:
            del _active_clients[ip]


def _safe_log(msg, is_error=False):
    """
    Безпечно записує повідомлення в stdout/stderr, запобігаючи падінню запиту через
    помилки Windows I/O пайпів/консолі (наприклад OSError [Errno 22] Invalid argument на flush/write).
    """
    try:
        stream = sys.stderr if is_error else sys.stdout
        if stream is not None:
            stream.write(msg)
            try:
                stream.flush()
            except (OSError, ValueError):
                pass
    except Exception:
        pass


class OnlineClientsMiddleware:
    """
    Відстежує кожен вхідний HTTP-запит для:
    1. Підрахунку активних онлайн-пристроїв у локальній мережі.
    2. Виведення в лог IP-адреси клієнта для кожного запиту та фіксації помилок від конкретного пристрою.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        ip = get_client_ip(request)
        now = time.time()
        path = request.get_full_path()
        
        # Ігноруємо службові запити статистики від самого лаунчера
        is_internal_poll = path.startswith('/api/server-stats/')
        is_disconnect = path.startswith('/api/client-disconnect/')
        
        if is_disconnect:
            remove_client_ip(ip)
        elif not is_internal_poll:
            with _lock:
                _active_clients[ip] = {
                    'timestamp': now,
                    'last_path': path if not path.startswith('/feed/check-updates/') else _active_clients.get(ip, {}).get('last_path', '/'),
                    'last_time_str': datetime.now().strftime('%H:%M:%S')
                }

        try:
            response = self.get_response(request)
        except Exception as exc:
            # Фіксуємо помилку із вказанням конкретної IP клієнта
            err_msg = f"[КЛІЄНТ IP: {ip}] ❌ ПОМИЛКА 500 на {request.method} {path}: {type(exc).__name__}: {str(exc)}\n"
            _safe_log(err_msg, is_error=True)
            try:
                traceback.print_exc()
            except Exception:
                pass
            raise

        # Виводимо в консоль інформацію про запит із зазначенням IP комп'ютера
        if not is_internal_poll and not is_disconnect:
            status = response.status_code
            if status >= 500:
                log_line = f"[КЛІЄНТ IP: {ip}] ❌ \"{request.method} {path}\" {status}\n"
                _safe_log(log_line, is_error=True)
            elif status >= 400:
                log_line = f"[КЛІЄНТ IP: {ip}] ⚠️ \"{request.method} {path}\" {status}\n"
                _safe_log(log_line, is_error=True)
            elif not path.startswith('/static/') and not path.startswith('/media/'):
                # Для звичайних сторінок та API запитів
                log_line = f"[КЛІЄНТ IP: {ip}] \"{request.method} {path}\" {status} OK\n"
                _safe_log(log_line, is_error=False)

        return response

    def process_exception(self, request, exception):
        ip = get_client_ip(request)
        path = request.get_full_path()
        err_msg = f"[КЛІЄНТ IP: {ip}] ❌ ВИНЯТОК на {request.method} {path}: {str(exception)}\n"
        _safe_log(err_msg, is_error=True)
        return None


def get_online_stats():
    """
    Повертає кількість та детальний список активних пристроїв.
    """
    now = time.time()
    cutoff = now - ONLINE_THRESHOLD_SECONDS
    
    with _lock:
        stale_ips = [ip for ip, data in _active_clients.items() if data['timestamp'] < cutoff]
        for ip in stale_ips:
            del _active_clients[ip]
            
        count = len(_active_clients)
        clients_list = [
            {
                'ip': ip,
                'last_seen_seconds_ago': max(0, int(now - data['timestamp'])),
                'last_time': data.get('last_time_str', ''),
                'last_path': data.get('last_path', '/')
            }
            for ip, data in sorted(_active_clients.items(), key=lambda item: item[1]['timestamp'], reverse=True)
        ]
        
    return {
        'online_count': count,
        'clients': clients_list,
        'threshold_seconds': ONLINE_THRESHOLD_SECONDS,
    }
